"""主窗口：顶部 ControlPanel + 左侧 StagePanel + 右侧 LogPanel + 底部 FooterStatusBar。

依赖 RuntimeFacade 的信号与方法（见 desktop.services.runtime_facade）。
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from desktop.models.runtime_snapshot import RuntimeSnapshot
from desktop.services.status_poll_service import StatusPollService
from desktop.widgets.control_panel import ControlPanel
from desktop.widgets.cr4a_lift_panel import Cr4aLiftPanel
from desktop.widgets.log_panel import LogPanel
from desktop.widgets.status_panel import FooterStatusBar, StagePanel

if TYPE_CHECKING:
    from desktop.services.runtime_facade import RuntimeFacade


class MainWindow(QMainWindow):
    _ROOT_DISK_WARNING_THRESHOLD = 90.0

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
        self._root_disk_warning_active = False

        self.control_panel = ControlPanel(title=facade.display_title)
        self.control_panel.set_environment(facade.environment)
        self.control_panel.set_launch_modes(facade.launch_modes, facade.launch_mode)
        self.stage_panel = StagePanel()
        self.log_panel = LogPanel()
        self.footer_bar = FooterStatusBar()
        self.lift_panel: Cr4aLiftPanel | None = None
        self.disk_warning_toast = _DiskWarningToast(self)
        self.facade.set_vr_ready_confirmation_handler(self._confirm_vr_ready)

        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)
        root_layout.addWidget(self.control_panel)
        if facade.robot_name == "robot-cr4a" and facade.lift_config.enabled:
            self.lift_panel = Cr4aLiftPanel(facade)
            root_layout.addWidget(self.lift_panel)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(self.stage_panel)
        main_splitter.addWidget(self.log_panel)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([360, 1040])

        root_layout.addWidget(main_splitter, 1)
        root_layout.addWidget(self.footer_bar)
        self.setCentralWidget(central)

        self._wire_events()
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
        if self.facade.requires_ros_for_non_vr:
            if self.facade.uses_managed_vr_ros:
                message = (
                    "当前已选择 VR 模式。\n\n"
                    "本程序会先启动 VR ROS，随后弹出准备确认窗口。\n"
                    "请在确认窗口倒计时结束前连接并准备好 VR 设备。"
                )
            else:
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

    def _confirm_vr_ready(self, countdown_seconds: int) -> bool:
        dialog = _VrReadyDialog(max(0, countdown_seconds), self)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _apply_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        self._latest_snapshot = snapshot
        self.stage_panel.update_snapshot(snapshot)
        self.control_panel.set_global_status(snapshot.global_status)
        self.control_panel.set_running(snapshot.running)
        self.footer_bar.update_runtime(
            snapshot.environment,
            self._display_status(snapshot),
            self.facade.started_at,
            health_text=self._health_text(snapshot),
            metrics_text=self._metrics_text(snapshot),
        )
        if self._close_requested and not snapshot.running and not self.facade.is_busy():
            self._closing_now = True
            QTimer.singleShot(0, self.close)

    def _refresh_footer(self) -> None:
        snapshot = self._latest_snapshot
        if snapshot is None:
            return
        self.footer_bar.update_runtime(
            snapshot.environment,
            self._display_status(snapshot),
            self.facade.started_at,
            health_text=self._health_text(snapshot),
            metrics_text=self._metrics_text(snapshot),
        )

    def _display_status(self, snapshot: RuntimeSnapshot) -> str:
        if snapshot.global_status == "启动中":
            return snapshot.message
        return snapshot.global_status

    def _health_text(self, snapshot: RuntimeSnapshot) -> str:
        healthy = snapshot.health.overall == "healthy"
        color = "#30d158" if healthy else "#8e8e93"
        return f"<span style='color:{color};'>●</span>"

    def _metrics_text(self, snapshot: RuntimeSnapshot) -> str:
        parts: list[str] = []
        if snapshot.health.cpu_percent is not None:
            parts.append(f"CPU {snapshot.health.cpu_percent:.0f}%")
        if snapshot.health.memory_percent is not None:
            parts.append(f"MEM {snapshot.health.memory_percent:.0f}%")
        root_disk_percent = self._root_disk_percent()
        if root_disk_percent is not None:
            color = (
                "#ff453a" if root_disk_percent > self._ROOT_DISK_WARNING_THRESHOLD else "#30d158"
            )
            parts.append(f"<span style='color:{color};'>/ 磁盘 {root_disk_percent:.1f}%</span>")
            self._update_root_disk_warning(root_disk_percent)
        else:
            self._root_disk_warning_active = False
            self.disk_warning_toast.hide()
            parts.append("<span style='color:#8e8e93;'>/ 磁盘不可用</span>")
        return " | ".join(parts) if parts else "-"

    def _root_disk_percent(self) -> float | None:
        try:
            usage = shutil.disk_usage("/")
        except OSError:
            return None
        if usage.total <= 0:
            return None
        return usage.used / usage.total * 100

    def _update_root_disk_warning(self, percent: float) -> None:
        above_threshold = percent > self._ROOT_DISK_WARNING_THRESHOLD
        if above_threshold and not self._root_disk_warning_active:
            self.disk_warning_toast.show_message(
                f"/ 磁盘使用率已超过 {self._ROOT_DISK_WARNING_THRESHOLD:.0f}%：{percent:.1f}%"
            )
        self._root_disk_warning_active = above_threshold

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

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        disk_warning_toast = getattr(self, "disk_warning_toast", None)
        if disk_warning_toast is not None:
            disk_warning_toast.reposition()


class _DiskWarningToast(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("DiskWarningToast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "QFrame#DiskWarningToast {"
            " background: #3a1515;"
            " border: 1px solid #ff453a;"
            " border-radius: 10px;"
            "}"
            "QLabel { color: #ffd8d6; font-weight: 700; }"
        )

        self._label = QLabel()
        self._label.setWordWrap(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.addWidget(self._label)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, message: str) -> None:
        self._label.setText(message)
        self.setFixedWidth(max(320, min(460, self.sizeHint().width())))
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._hide_timer.start(8000)

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        margin = 18
        x = max(margin, parent.width() - self.width() - margin)
        self.move(x, margin)


class _VrReadyDialog(QDialog):
    def __init__(self, countdown_seconds: int, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("VR 准备确认")
        self.setModal(True)
        self._remaining_seconds = countdown_seconds

        title = QLabel("VR ROS 已启动")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #a3c9ff;")
        message = QLabel(
            "请佩戴并连接 VR 设备，确认手柄/头显已经准备好。\n"
            "倒计时结束后，点击“确定”继续启动 Robot OS 和采集程序。"
        )
        message.setWordWrap(True)

        self._countdown_label = QLabel()
        self._countdown_label.setStyleSheet("color: #ffd60a; font-size: 15px; font-weight: 700;")

        self._confirm_button = QPushButton()
        self._confirm_button.setObjectName("PrimaryButton")
        self._confirm_button.clicked.connect(self.accept)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self._confirm_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(message)
        layout.addWidget(self._countdown_label)
        layout.addLayout(button_row)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._refresh_button()
        if self._remaining_seconds > 0:
            self._timer.start(1000)

    def _tick(self) -> None:
        self._remaining_seconds = max(0, self._remaining_seconds - 1)
        self._refresh_button()
        if self._remaining_seconds <= 0:
            self._timer.stop()

    def _refresh_button(self) -> None:
        if self._remaining_seconds > 0:
            self._confirm_button.setEnabled(False)
            self._confirm_button.setText(f"确定（{self._remaining_seconds}s）")
            self._countdown_label.setText(f"请等待 {self._remaining_seconds} 秒后继续。")
            return
        self._confirm_button.setEnabled(True)
        self._confirm_button.setText("确定")
        self._countdown_label.setText("VR 准备完成后，可点击确定继续。")
