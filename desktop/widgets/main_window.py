"""主窗口：顶部 ControlPanel + 左侧 StagePanel + 右侧 LogPanel + 底部 FooterStatusBar。

依赖 RuntimeFacade 的信号与方法（见 desktop.services.runtime_facade）。
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from desktop.models.runtime_snapshot import RuntimeSnapshot

from desktop.models.stage_state import StageStatus
from desktop.services.status_poll_service import StatusPollService
from desktop.widgets.control_panel import ControlPanel
from desktop.widgets.log_panel import LogPanel
from desktop.widgets.status_panel import FooterStatusBar, StagePanel

if TYPE_CHECKING:
    from desktop.services.runtime_facade import RuntimeFacade


class _VrReadyCountdownDialog(QDialog):
    def __init__(self, countdown_seconds: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._remaining = max(0, countdown_seconds)
        self._ready = self._remaining == 0
        self.setWindowTitle("VR 准备确认")
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

        self._message_label = QLabel(
            "VR ROS 已启动。\n\n请佩戴并连接 VR 设备，在 VR 中完成准备后再继续启动采集程序。"
        )
        self._message_label.setWordWrap(True)
        self._countdown_label = QLabel()
        self._ok_button = QPushButton()
        self._ok_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(self._message_label)
        layout.addWidget(self._countdown_label)
        layout.addWidget(self._ok_button)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._refresh_state()
        if not self._ready:
            self._timer.start()

    def _tick(self) -> None:
        self._remaining = max(0, self._remaining - 1)
        if self._remaining == 0:
            self._ready = True
            self._timer.stop()
        self._refresh_state()

    def _refresh_state(self) -> None:
        if self._ready:
            self._countdown_label.setText("倒计时已结束，可以继续启动后续程序。")
            self._ok_button.setText("确定，继续启动")
            self._ok_button.setEnabled(True)
            return
        self._countdown_label.setText(f"请完成 VR 准备，{self._remaining} 秒后可点击确定。")
        self._ok_button.setText(f"确定（{self._remaining}s）")
        self._ok_button.setEnabled(False)

    def reject(self) -> None:
        if self._ready:
            self.accept()


class MainWindow(QMainWindow):
    _ROOT_DISK_WARNING_THRESHOLD = 90.0
    _RESOURCE_OK_COLOR = "#30d158"
    _RESOURCE_WARNING_COLOR = "#ff453a"
    _API_STAGE_NAMES = frozenset({"api", "采集程序"})
    _ROBOT_OS_STAGE_NAMES = frozenset({"robot_os", "Robot OS"})

    @classmethod
    def _resource_metrics_text(cls, disk_percent: float | None) -> str:
        if disk_percent is None:
            return "根目录磁盘 -"
        color = (
            cls._RESOURCE_WARNING_COLOR
            if disk_percent >= cls._ROOT_DISK_WARNING_THRESHOLD
            else cls._RESOURCE_OK_COLOR
        )
        return f"<span style='color:{color};'>根目录磁盘 {disk_percent:.0f}%</span>"

    @staticmethod
    def _root_disk_percent() -> float | None:
        try:
            usage = shutil.disk_usage("/")
        except OSError:
            return None
        if usage.total <= 0:
            return None
        return usage.used / usage.total * 100

    def _maybe_warn_root_disk(self, disk_percent: float | None) -> None:
        if disk_percent is None:
            return
        if disk_percent < self._ROOT_DISK_WARNING_THRESHOLD:
            self._disk_warning_shown = False
            return
        if self._disk_warning_shown:
            return
        self._disk_warning_shown = True
        QMessageBox.warning(
            self,
            "根目录磁盘空间告警",
            (
                "当前运行 desktop 的机器根目录 / 磁盘使用率"
                f"已达到 {disk_percent:.0f}%。\n"
                "请及时清理磁盘空间，避免采集或运行时服务异常。"
            ),
        )

    def __init__(
        self, facade: "RuntimeFacade", title: str = "Percept Edge Runtime Console"
    ) -> None:
        super().__init__()
        self.setWindowTitle(title)
        self.resize(1480, 920)

        self.facade = facade
        self.poll_service = StatusPollService()
        self._latest_snapshot: RuntimeSnapshot | None = None
        self._close_requested = False
        self._closing_now = False
        self._vr_notice_shown = False
        self._disk_warning_shown = False

        self.control_panel = ControlPanel(title=facade.display_title)
        self.control_panel.set_environment(facade.environment)
        self.control_panel.set_launch_modes(facade.launch_modes, facade.launch_mode)
        self.stage_panel = StagePanel()
        lift_cfg = self.facade.lift_config
        lift_enabled = cast(bool, self._lift_value(lift_cfg, "enabled", False))
        if lift_enabled:
            self.stage_panel.set_lift_config(
                enabled=lift_enabled,
                transport=cast(str, self._lift_value(lift_cfg, "transport", "script")),
                api_path=cast(
                    str,
                    self._lift_value(lift_cfg, "api_path", "/api/desktop/lift/height"),
                ),
                api_url=self.facade.api_url,
                python_bin=cast(str, self._lift_value(lift_cfg, "python_bin", "")),
                script_path=cast(str, self._lift_value(lift_cfg, "script_path", "")),
                min_height=cast(int, self._lift_value(lift_cfg, "min_height", 0)),
                max_height=cast(int, self._lift_value(lift_cfg, "max_height", 600)),
                step=cast(int, self._lift_value(lift_cfg, "step", 100)),
            )
        self.log_panel = LogPanel()
        self.footer_bar = FooterStatusBar()

        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)
        root_layout.addWidget(self.control_panel)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(self.stage_panel)
        main_splitter.addWidget(self.log_panel)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([360, 1040])

        root_layout.addWidget(main_splitter, 1)
        root_layout.addWidget(self.footer_bar)
        self.setCentralWidget(central)

        self._wire_events()
        vr_handler = getattr(self.facade, "set_vr_ready_confirmation_handler", None)
        if callable(vr_handler):
            vr_handler(self._show_vr_ready_confirmation)
        self.facade.reset_ui_state()
        self.poll_service.start()
        if facade.launch_mode == "vr":
            QTimer.singleShot(0, self._show_vr_mode_notice)

    def _wire_events(self) -> None:
        self.control_panel.environment_changed.connect(self.facade.set_environment)
        self.control_panel.launch_mode_changed.connect(self._on_launch_mode_changed)
        self.control_panel.start_requested.connect(self.facade.start_runtime)
        self.control_panel.stop_requested.connect(self._request_stop)
        self.control_panel.restart_requested.connect(self.facade.restart_runtime)
        self.control_panel.clear_logs_requested.connect(self.log_panel.clear)
        self.control_panel.emergency_stop_requested.connect(lambda: self.facade.stop_runtime(True))
        self.control_panel.reset_requested.connect(self._reset_ui)

        self.facade.log_emitted.connect(self.log_panel.add_entry)
        self.facade.snapshot_changed.connect(self._apply_snapshot)
        self.poll_service.tick.connect(self._refresh_footer)

    @staticmethod
    def _lift_value(config: object, key: str, default: object) -> object:
        if isinstance(config, dict):
            mapping = cast(dict[str, object], config)
            return mapping.get(key, default)
        return getattr(config, key, default)

    def _lift_transport(self) -> str:
        return cast(str, self._lift_value(self.facade.lift_config, "transport", "script"))

    def _api_stage_ready(self, snapshot: RuntimeSnapshot) -> bool:
        return any(
            stage.name in self._API_STAGE_NAMES and stage.status is StageStatus.RUNNING
            for stage in snapshot.stages
        )

    def _robot_os_stage_ready(self, snapshot: RuntimeSnapshot) -> bool:
        return any(
            stage.name in self._ROBOT_OS_STAGE_NAMES and stage.status is StageStatus.RUNNING
            for stage in snapshot.stages
        )

    def _lift_available(self, snapshot: RuntimeSnapshot) -> bool:
        if self._lift_transport() == "http":
            return self._robot_os_stage_ready(snapshot) and self._api_stage_ready(snapshot)
        return not snapshot.running and not self.facade.is_busy()

    def _request_stop(self) -> None:
        self.facade.stop_runtime(False)

    def _on_launch_mode_changed(self, launch_mode: str) -> None:
        self.facade.set_launch_mode(launch_mode)
        if launch_mode == "vr":
            self._show_vr_mode_notice()
        else:
            self._vr_notice_shown = False

    def _show_vr_mode_notice(self) -> None:
        if self._vr_notice_shown:
            return
        self._vr_notice_shown = True
        managed_vr_ros = bool(getattr(self.facade, "uses_managed_vr_ros", False))
        requires_ros_for_non_vr = bool(getattr(self.facade, "requires_ros_for_non_vr", False))
        if managed_vr_ros:
            message = (
                "当前已选择 VR 模式。\n\n"
                "本程序会自动启动系统准备、VR ROS 和 VR 通信节点。\n"
                "VR ROS 启动后会弹出准备确认窗口；倒计时结束并点击确定后，"
                "才会继续启动 Robot OS 和采集程序。"
            )
        elif requires_ros_for_non_vr:
            message = (
                "当前已选择 VR 模式。\n\n"
                "请先手动启动 ROS 程序，并连接好 VR 设备。\n"
                "在 VR 中完成相关设置后，再点击本程序的“开始全部”。"
            )
        else:
            message = (
                "当前已选择 VR 模式。\n\n"
                "请连接好 VR 设备，并在 VR 中完成相关设置后，"
                "再点击本程序的“开始全部”。"
            )
        QMessageBox.information(
            self,
            "VR 模式启动前确认",
            message,
        )

    def _show_vr_ready_confirmation(self, countdown_seconds: int) -> bool:
        dialog = _VrReadyCountdownDialog(countdown_seconds, parent=self)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _apply_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        self._latest_snapshot = snapshot
        self.stage_panel.update_snapshot(snapshot)
        # HTTP lift commands require the local API to be ready; script commands remain offline-only.
        self.stage_panel.set_lift_enabled(self._lift_available(snapshot))
        self.control_panel.set_global_status(snapshot.global_status)
        self.control_panel.set_running(snapshot.running)
        disk_percent = self._root_disk_percent()
        self.footer_bar.update_runtime(
            snapshot.environment,
            self.facade.started_at,
            metrics_text=self._resource_metrics_text(disk_percent),
        )
        self._maybe_warn_root_disk(disk_percent)
        if self._close_requested and not snapshot.running and not self.facade.is_busy():
            self._closing_now = True
            QTimer.singleShot(0, self.close)

    def _refresh_footer(self) -> None:
        snapshot = self._latest_snapshot
        if snapshot is None:
            return
        disk_percent = self._root_disk_percent()
        self.footer_bar.update_runtime(
            snapshot.environment,
            self.facade.started_at,
            metrics_text=self._resource_metrics_text(disk_percent),
        )
        self._maybe_warn_root_disk(disk_percent)

    def _reset_ui(self) -> None:
        self.log_panel.clear()
        self.facade.reset_ui_state()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing_now:
            self.poll_service.stop()
            super().closeEvent(event)
            return
        if self.facade.is_busy():
            if not self._close_requested:
                self._close_requested = True
                self.facade.stop_runtime(False)
            event.ignore()
            return
        self.poll_service.stop()
        super().closeEvent(event)
