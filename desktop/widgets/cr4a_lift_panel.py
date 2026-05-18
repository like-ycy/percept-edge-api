"""CR4A 专属升降台控制卡片。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QProcess
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from desktop.services.config_loader import LiftControlConfig

if TYPE_CHECKING:
    from desktop.services.runtime_facade import RuntimeFacade


class Cr4aLiftPanel(QFrame):
    """只给 CR4A 显示的升降台高度控制。"""

    def __init__(self, facade: "RuntimeFacade") -> None:
        super().__init__()
        self.setObjectName("LiftPanel")
        self._config = facade.lift_config
        self._process: QProcess | None = None
        self._current_height = self._config.min_height

        title = QLabel("CR4A 升降台")
        title.setStyleSheet("color: #a3c9ff; font-weight: 800; font-size: 14px;")
        subtitle = QLabel(f"高度范围 {self._config.min_height}-{self._config.max_height} mm")
        subtitle.setStyleSheet("color: #949fae; font-size: 11px;")

        self.height_input = QLineEdit(str(self._current_height))
        self.height_input.setPlaceholderText("输入高度")
        self.height_input.setValidator(
            QIntValidator(self._config.min_height, self._config.max_height, self)
        )
        self.height_input.returnPressed.connect(self._apply_input_height)

        self.decrease_button = QPushButton(f"-{self._config.step}")
        self.apply_button = QPushButton("设置高度")
        self.apply_button.setObjectName("PrimaryButton")
        self.increase_button = QPushButton(f"+{self._config.step}")

        self.decrease_button.clicked.connect(lambda: self._adjust_height(-self._config.step))
        self.apply_button.clicked.connect(self._apply_input_height)
        self.increase_button.clicked.connect(lambda: self._adjust_height(self._config.step))

        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #ababab;")
        self.command_label = QLabel(self._command_preview(self._current_height))
        self.command_label.setStyleSheet("color: #646b75; font-size: 11px;")
        self.command_label.setWordWrap(True)

        header_layout = QVBoxLayout()
        header_layout.setSpacing(2)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)

        control_layout = QHBoxLayout()
        control_layout.setSpacing(8)
        control_layout.addWidget(QLabel("目标高度"))
        control_layout.addWidget(self.height_input)
        control_layout.addWidget(self.decrease_button)
        control_layout.addWidget(self.apply_button)
        control_layout.addWidget(self.increase_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(header_layout)
        layout.addLayout(control_layout)
        layout.addWidget(self.status_label)
        layout.addWidget(self.command_label)

        if not self._is_config_ready(self._config):
            self._set_controls_enabled(False)
            self.status_label.setText("升降台配置未启用或脚本路径为空")

    def _adjust_height(self, delta: int) -> None:
        base_height = self._read_input_height(default=self._current_height)
        self._set_height(base_height + delta, execute=True)

    def _apply_input_height(self) -> None:
        self._set_height(self._read_input_height(default=self._current_height), execute=True)

    def _set_height(self, height: int, *, execute: bool) -> None:
        normalized_height = self._clamp_height(height)
        self._current_height = normalized_height
        self.height_input.setText(str(normalized_height))
        self.command_label.setText(self._command_preview(normalized_height))
        if execute:
            self._run_lift_command(normalized_height)

    def _run_lift_command(self, height: int) -> None:
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self.status_label.setText("升降台命令执行中，请稍候")
            return
        if not self._is_config_ready(self._config):
            self.status_label.setText("升降台配置未启用或脚本路径为空")
            return

        process = QProcess(self)
        process.setProgram(self._config.python_bin)
        process.setArguments([self._config.script_path, str(height)])
        process.setWorkingDirectory(str(Path(self._config.script_path).parent))
        process.finished.connect(
            lambda exit_code, _status: self._on_process_finished(exit_code, height)
        )
        process.errorOccurred.connect(self._on_process_error)
        self._process = process
        self._set_controls_enabled(False)
        self.status_label.setText(f"正在调整到 {height} mm ...")
        process.start()

    def _on_process_finished(self, exit_code: int, height: int) -> None:
        stdout = self._read_process_stream("stdout")
        stderr = self._read_process_stream("stderr")
        self._set_controls_enabled(True)
        if exit_code == 0:
            detail = f"，输出: {stdout}" if stdout else ""
            self.status_label.setText(f"已调整到 {height} mm{detail}")
            return
        detail = stderr or stdout or "无输出"
        self.status_label.setText(f"调整失败，退出码 {exit_code}: {detail}")

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        self._set_controls_enabled(True)
        self.status_label.setText(f"升降台命令启动失败: {error.name}")

    def _read_process_stream(self, stream: str) -> str:
        if self._process is None:
            return ""
        if stream == "stdout":
            raw = self._process.readAllStandardOutput()
        else:
            raw = self._process.readAllStandardError()
        return bytes(raw.data()).decode("utf-8", errors="replace").strip()

    def _read_input_height(self, *, default: int) -> int:
        raw = self.height_input.text().strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            self.status_label.setText("请输入整数高度")
            return default

    def _clamp_height(self, height: int) -> int:
        return min(max(height, self._config.min_height), self._config.max_height)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.height_input.setEnabled(enabled)
        self.decrease_button.setEnabled(enabled)
        self.apply_button.setEnabled(enabled)
        self.increase_button.setEnabled(enabled)

    def _command_preview(self, height: int) -> str:
        return f"命令: {self._config.python_bin} {self._config.script_path} {height}"

    @staticmethod
    def _is_config_ready(config: LiftControlConfig) -> bool:
        return (
            config.enabled and bool(config.python_bin.strip()) and bool(config.script_path.strip())
        )
