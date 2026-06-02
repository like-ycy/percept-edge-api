"""状态面板：合并 StageCardWidget / StagePanel / FooterStatusBar。

对外暴露：
    StagePanel   — 左侧阶段卡片列表
    FooterStatusBar — 底部环境/状态/资源信息条
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from desktop.models.runtime_snapshot import RuntimeSnapshot
from desktop.models.stage_state import StageState
from desktop.services.lift_executors import ApiLiftExecutor, ScriptLiftExecutor
from desktop.services.local_api_client import LocalApiClient
from desktop.widgets.status_chip import StatusChip

if TYPE_CHECKING:
    from desktop.widgets.lift_control_widget import LiftControlWidget


class _StageCardWidget(QFrame):
    def __init__(self, stage_name: str) -> None:
        super().__init__()
        self.setObjectName("StageCard")
        self.setProperty("active", False)

        self.title_label = QLabel(stage_name)
        self.title_label.setStyleSheet("font-weight: 700; font-size: 13px;")
        self.status_chip = StatusChip()
        self.summary_label = QLabel("-")
        self.summary_label.setStyleSheet("color: #949fae;")
        self.pid_label = QLabel("PID: -")
        self.pid_label.setStyleSheet("color: #ababab;")
        self.dependency_label = QLabel("依赖: -")
        self.dependency_label.setStyleSheet("color: #ababab;")
        self.error_label = QLabel("最后错误: 无")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #ababab;")

        title_row = QHBoxLayout()
        title_row.addWidget(self.title_label)
        title_row.addStretch(1)
        title_row.addWidget(self.status_chip)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        layout.addLayout(title_row)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.pid_label)
        layout.addWidget(self.dependency_label)
        layout.addWidget(self.error_label)

    def update_stage(self, stage: StageState) -> None:
        self.status_chip.set_status(stage.status.value)
        self.summary_label.setText(stage.summary)
        self.pid_label.setText(f"PID: {stage.pid or '-'}")
        self.dependency_label.setText(f"依赖: {stage.dependency or '-'}")
        self.error_label.setText(f"最后错误: {stage.last_error or '无'}")
        self.setProperty("active", stage.status.value in {"starting", "running"})
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)


class StagePanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("StagePanel")
        self._cards: dict[str, _StageCardWidget] = {}
        self._lift_widget: LiftControlWidget | None = None
        self._lift_configured = False

        title = QLabel("运行阶段")
        title.setStyleSheet("color: #a3c9ff; font-weight: 800; font-size: 14px;")
        subtitle = QLabel("进程监控")
        subtitle.setStyleSheet("color: #949fae; font-size: 11px;")

        self._content_layout = QVBoxLayout()
        self._content_layout.setSpacing(8)
        self._content_layout.addStretch(1)

        content = QWidget()
        content.setLayout(self._content_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(scroll)

    def update_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        current_stage_names = {stage.name for stage in snapshot.stages}
        for stale_stage_name in tuple(self._cards):
            if stale_stage_name in current_stage_names:
                continue
            stale_card = self._cards.pop(stale_stage_name)
            self._content_layout.removeWidget(stale_card)
            stale_card.deleteLater()

        for stage in snapshot.stages:
            card = self._cards.get(stage.name)
            if card is None:
                card = _StageCardWidget(stage.name)
                self._cards[stage.name] = card
            self._content_layout.removeWidget(card)
            self._content_layout.insertWidget(self._content_layout.count() - 1, card)
            card.update_stage(stage)

    def set_lift_config(
        self,
        enabled: bool,
        transport: str = "script",
        api_path: str = "/api/desktop/lift/height",
        api_url: str = "http://127.0.0.1:8000/",
        python_bin: str = "",
        script_path: str = "",
        min_height: int = 0,
        max_height: int = 600,
        step: int = 100,
    ) -> None:
        """Configure and optionally show the lift control card."""
        if not enabled:
            return
        if self._lift_configured:
            return
        from desktop.widgets.lift_control_widget import LiftControlWidget

        if transport == "http":
            executor = ApiLiftExecutor(LocalApiClient(api_url), api_path)
        else:
            executor = ScriptLiftExecutor(python_bin, script_path)

        self._lift_configured = True
        self._lift_widget = LiftControlWidget(
            executor=executor,
            min_height=min_height,
            max_height=max_height,
            step=step,
        )
        # Insert at position 0 in _content_layout (before the stretch)
        self._content_layout.insertWidget(0, self._lift_widget)

    def set_lift_enabled(self, enabled: bool) -> None:
        """Enable/disable lift control interactions based on runtime state."""
        if self._lift_widget is not None:
            self._lift_widget.set_enabled(enabled)


class FooterStatusBar(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("FooterBar")

        self.env_label = QLabel("环境: test")
        self.metrics_label = QLabel("资源: -")
        self.uptime_label = QLabel("运行时长: 00:00:00")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.addWidget(self.env_label)
        layout.addSpacing(12)
        layout.addWidget(self.metrics_label)
        layout.addStretch(1)
        layout.addWidget(self.uptime_label)

    def update_runtime(
        self,
        environment: str,
        started_at: Optional[datetime],
        metrics_text: str = "-",
    ) -> None:
        self.env_label.setText(f"环境: {environment}")
        self.metrics_label.setText(f"资源: {metrics_text}")
        if started_at is None:
            self.uptime_label.setText("运行时长: 00:00:00")
            return
        delta = datetime.now() - started_at
        total_seconds = int(delta.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.uptime_label.setText(f"运行时长: {hours:02d}:{minutes:02d}:{seconds:02d}")
