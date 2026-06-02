"""升降台高度控制组件。

仅在 [desktop.lift].enabled = true 时渲染，通过注入的执行器设置高度。
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from desktop.services.lift_executors import LiftExecutor
from desktop.widgets.status_chip import StatusChip


class LiftControlWidget(QFrame):
    """升降台高度控制卡片，样式与 _StageCardWidget 一致。"""

    def __init__(
        self,
        executor: LiftExecutor,
        min_height: int = 0,
        max_height: int = 600,
        step: int = 100,
        parent: QFrame | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StageCard")
        self.setProperty("active", False)

        self._executor = executor
        self._min = min_height
        self._max = max_height
        self._step = step
        self._available = True
        self._busy = False

        self._title_label = QLabel(f"升降台高度 ({min_height}-{max_height}mm)")
        self._title_label.setStyleSheet("font-weight: 700; font-size: 13px;")
        self._status_chip = StatusChip("idle")

        self._spinbox = QSpinBox()
        self._spinbox.setRange(min_height, max_height)
        self._spinbox.setValue(350)
        self._spinbox.setSuffix(" mm")

        self._set_button = QPushButton("设置")
        self._set_button.clicked.connect(self._on_set_clicked)

        self._minus_button = QPushButton(f"-{step}")
        self._minus_button.clicked.connect(self._on_minus_clicked)

        self._plus_button = QPushButton(f"+{step}")
        self._plus_button.clicked.connect(self._on_plus_clicked)

        self._build_layout()
        self._connect_executor_signals()

    def _build_layout(self) -> None:
        title_row = QHBoxLayout()
        title_row.addWidget(self._title_label)
        title_row.addStretch(1)
        title_row.addWidget(self._status_chip)

        input_row = QHBoxLayout()
        input_row.addWidget(self._spinbox)
        input_row.addWidget(self._set_button)

        step_row = QHBoxLayout()
        step_row.addWidget(self._minus_button)
        step_row.addStretch(1)
        step_row.addWidget(self._plus_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        layout.addLayout(title_row)
        layout.addLayout(input_row)
        layout.addLayout(step_row)

    def set_enabled(self, enabled: bool) -> None:
        """启用/禁用所有交互控件。"""
        self._available = enabled
        self._apply_interactive_state()

    def _apply_interactive_state(self) -> None:
        enabled = self._available and not self._busy
        self._spinbox.setEnabled(enabled)
        self._set_button.setEnabled(enabled)
        self._minus_button.setEnabled(enabled)
        self._plus_button.setEnabled(enabled)

    def _connect_executor_signals(self) -> None:
        finished = getattr(self._executor, "finished", None)
        if finished is not None:
            finished.connect(self._on_command_finished)

    def _on_set_clicked(self) -> None:
        if not self._validate_manual_input():
            return
        self._execute_command(self._spinbox.value())

    def _validate_manual_input(self) -> bool:
        line_edit = self._spinbox.lineEdit()
        if line_edit is None:
            return True

        raw_text = line_edit.text().replace(self._spinbox.suffix(), "").strip()
        if not raw_text:
            self._show_out_of_range_warning()
            return False

        try:
            input_value = int(raw_text)
        except ValueError:
            self._show_out_of_range_warning()
            return False

        if self._min <= input_value <= self._max:
            return True

        self._show_out_of_range_warning()
        return False

    def _show_out_of_range_warning(self) -> None:
        QMessageBox.warning(
            self,
            "升降高度超出范围",
            f"请输入 {self._min}-{self._max} mm 范围内的高度。",
        )

    def _on_minus_clicked(self) -> None:
        new_value = max(self._min, self._spinbox.value() - self._step)
        self._spinbox.setValue(new_value)
        self._execute_command(new_value)

    def _on_plus_clicked(self) -> None:
        new_value = min(self._max, self._spinbox.value() + self._step)
        self._spinbox.setValue(new_value)
        self._execute_command(new_value)

    def _execute_command(self, height: int) -> None:
        self._busy = True
        self._apply_interactive_state()
        self._status_chip.set_status("starting")
        self._executor.set_height(height)

    def _on_command_finished(self, success: bool, _message: str = "") -> None:
        self._busy = False
        self._apply_interactive_state()
        self._status_chip.set_status("running" if success else "failed")
        # 2 秒后恢复 idle
        QTimer.singleShot(2000, lambda: self._status_chip.set_status("idle"))
