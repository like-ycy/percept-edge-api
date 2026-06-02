"""日志面板：合并 LogTableModel / LogFilterProxyModel / LogViewer / LogFilterBar。

对外暴露：
    LogPanel — 顶部过滤条 + 表格，接收 LogEntry、支持搜索/级别过滤。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from desktop.models.log_entry import LogEntry


class LogTableModel(QAbstractTableModel):
    headers = ["时间", "来源", "级别", "消息"]
    MAX_ROWS = 10000
    _TRIM_BATCH = 2000

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[LogEntry] = []

    def rowCount(
        self,
        /,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),
    ) -> int:
        _ = parent
        return len(self._rows)

    def columnCount(
        self,
        /,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),
    ) -> int:
        _ = parent
        return len(self.headers)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return str(section + 1)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        entry = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return [entry.timestamp, entry.source, entry.level, entry.message][index.column()]
        if role == Qt.ItemDataRole.ForegroundRole:
            if entry.level == "ERROR":
                return QColor("#ee7d77")
            if entry.level == "WARN":
                return QColor("#f2c46d")
            if entry.level == "INFO":
                return QColor("#e6e5e5")
        return None

    def add_entry(self, entry: LogEntry) -> None:
        self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
        self._rows.append(entry)
        self.endInsertRows()
        if len(self._rows) > self.MAX_ROWS:
            trim = self._TRIM_BATCH
            self.beginRemoveRows(QModelIndex(), 0, trim - 1)
            del self._rows[:trim]
            self.endRemoveRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()


class LogFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._search_text = ""
        self._level = "ALL"

    def set_search_text(self, text: str) -> None:
        self._search_text = text.lower().strip()
        self.invalidateFilter()

    def set_level(self, level: str) -> None:
        self._level = level
        self.invalidateFilter()

    def filterAcceptsRow(
        self,
        source_row: int,
        source_parent: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        model = self.sourceModel()
        if model is None:
            return True
        values = [
            str(model.index(source_row, column, source_parent).data() or "") for column in range(4)
        ]
        joined = " ".join(values).lower()
        level = values[2].upper()

        if self._level != "ALL" and level != self._level:
            return False
        if self._search_text and self._search_text not in joined:
            return False
        return True


class _CopyableLogTableView(QTableView):
    """支持按行复制完整日志内容的表格视图。"""

    _COPY_COLUMN_SEPARATOR = "\t"
    _COPY_ROW_SEPARATOR = "\n"

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection_to_clipboard()
            return
        super().keyPressEvent(event)

    def copy_selection_to_clipboard(self) -> None:
        selection_model = self.selectionModel()
        model = self.model()
        if selection_model is None or model is None:
            return

        rows = [index.row() for index in selection_model.selectedRows()]
        if not rows:
            current_index = self.currentIndex()
            if not current_index.isValid():
                return
            rows = [current_index.row()]

        unique_rows = sorted(set(rows))
        lines = []
        for row in unique_rows:
            values = []
            for column in range(model.columnCount()):
                value = model.index(row, column).data(Qt.ItemDataRole.DisplayRole)
                values.append(str(value or ""))
            lines.append(self._COPY_COLUMN_SEPARATOR.join(values))

        QGuiApplication.clipboard().setText(self._COPY_ROW_SEPARATOR.join(lines))


class _LogFilterBar(QFrame):
    search_changed = Signal(str)
    level_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("LogFilterBar")

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索日志...")
        self.search_input.textChanged.connect(self.search_changed.emit)

        self.all_button = QPushButton("全部")
        self.info_button = QPushButton("信息")
        self.warn_button = QPushButton("警告")
        self.error_button = QPushButton("错误")
        self.all_button.clicked.connect(lambda: self.level_changed.emit("ALL"))
        self.info_button.clicked.connect(lambda: self.level_changed.emit("INFO"))
        self.warn_button.clicked.connect(lambda: self.level_changed.emit("WARN"))
        self.error_button.clicked.connect(lambda: self.level_changed.emit("ERROR"))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        layout.addWidget(QLabel("过滤器"))
        layout.addWidget(self.search_input, 1)
        layout.addWidget(self.all_button)
        layout.addWidget(self.info_button)
        layout.addWidget(self.warn_button)
        layout.addWidget(self.error_button)


class LogPanel(QWidget):
    """对外顶层组件：顶部过滤条 + 日志表格。"""

    def __init__(self) -> None:
        super().__init__()
        self.filter_bar = _LogFilterBar()
        self.model = LogTableModel()
        self.proxy_model = LogFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.model)

        self.table = _CopyableLogTableView()
        self.table.setModel(self.proxy_model)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.filter_bar)
        layout.addWidget(self.table, 1)

        self.filter_bar.search_changed.connect(self.proxy_model.set_search_text)
        self.filter_bar.level_changed.connect(self.proxy_model.set_level)

    def add_entry(self, entry: LogEntry) -> None:
        self.model.add_entry(entry)
        self.table.scrollToBottom()

    def clear(self) -> None:
        self.model.clear()
