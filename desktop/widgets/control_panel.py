"""控制面板：顶部按钮栏 + 全局状态徽章 + 环境/启动方式切换。

由旧 TopControlBar 平迁，Chip 复用 status_chip.StatusChip。
新增 display_name 标题字段，用于展示当前机型（CR4C / W1 ...）。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton

from desktop.widgets.status_chip import StatusChip


class ControlPanel(QFrame):
    start_requested = Signal()
    stop_requested = Signal()
    restart_requested = Signal()
    clear_logs_requested = Signal()
    emergency_stop_requested = Signal()
    reset_requested = Signal()
    environment_changed = Signal(str)
    launch_mode_changed = Signal(str)

    def __init__(self, title: str = "运行控制台") -> None:
        super().__init__()
        self.setObjectName("TopBar")

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet("font-size: 18px; font-weight: 800;")

        self.environment_combo = QComboBox()
        self.environment_combo.addItems(["test", "prod"])
        self.environment_combo.currentTextChanged.connect(self.environment_changed.emit)

        self.launch_mode_combo = QComboBox()
        self.launch_mode_combo.currentIndexChanged.connect(self._emit_launch_mode_changed)

        self.status_chip = StatusChip("global-idle")

        self.start_button = QPushButton("开始全部")
        self.start_button.setObjectName("PrimaryButton")
        self.stop_button = QPushButton("停止全部")
        self.restart_button = QPushButton("重启全部")
        self.clear_logs_button = QPushButton("清除日志")
        self.emergency_button = QPushButton("紧急停止")
        self.emergency_button.setObjectName("DangerButton")
        self.reset_button = QPushButton("全局重置")

        self.start_button.clicked.connect(self.start_requested.emit)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.restart_button.clicked.connect(self.restart_requested.emit)
        self.clear_logs_button.clicked.connect(self.clear_logs_requested.emit)
        self.emergency_button.clicked.connect(self.emergency_stop_requested.emit)
        self.reset_button.clicked.connect(self.reset_requested.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(self._title_label)
        layout.addSpacing(16)
        layout.addWidget(QLabel("环境"))
        layout.addWidget(self.environment_combo)
        layout.addWidget(QLabel("启动方式"))
        layout.addWidget(self.launch_mode_combo)
        layout.addWidget(self.status_chip)
        layout.addStretch(1)
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.restart_button)
        layout.addWidget(self.clear_logs_button)
        layout.addWidget(self.emergency_button)
        layout.addWidget(self.reset_button)

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def set_environment(self, environment: str) -> None:
        previous = self.environment_combo.blockSignals(True)
        try:
            index = self.environment_combo.findText(environment)
            if index >= 0:
                self.environment_combo.setCurrentIndex(index)
        finally:
            self.environment_combo.blockSignals(previous)

    def set_launch_modes(self, modes: Sequence[str], current: str) -> None:
        previous = self.launch_mode_combo.blockSignals(True)
        try:
            self.launch_mode_combo.clear()
            for mode in modes:
                self.launch_mode_combo.addItem(_LAUNCH_MODE_LABELS.get(mode, mode), mode)
            index = self.launch_mode_combo.findData(current)
            self.launch_mode_combo.setCurrentIndex(index if index >= 0 else 0)
            self.launch_mode_combo.setEnabled(len(modes) > 1)
        finally:
            self.launch_mode_combo.blockSignals(previous)

    def set_global_status(self, status: str) -> None:
        key = {
            "启动中": "global-starting",
            "运行中": "global-running",
            "部分运行中": "global-partial",
            "停止中": "global-stopping",
            "已停止": "global-stopped",
            "错误": "global-error",
        }.get(status, "global-idle")
        self.status_chip.set_status(key)

    def set_running(self, running: bool) -> None:
        self.environment_combo.setEnabled(not running)
        self.launch_mode_combo.setEnabled(not running and self.launch_mode_combo.count() > 1)
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.restart_button.setEnabled(running)
        self.emergency_button.setEnabled(running)
        self.reset_button.setEnabled(not running)

    def _emit_launch_mode_changed(self) -> None:
        data = self.launch_mode_combo.currentData()
        if isinstance(data, str):
            self.launch_mode_changed.emit(data)


_LAUNCH_MODE_LABELS = {
    "bilateral": "同构臂",
    "vr": "VR",
}
