"""状态面板：合并 StatusChip / StageCardWidget / StagePanel / FooterStatusBar。

对外暴露：
    StatusChip   — 状态徽章（用于顶栏共用）
    StagePanel   — 左侧阶段卡片列表
    FooterStatusBar — 底部环境/状态/资源信息条
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

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


class StatusChip(QLabel):
    _STYLE_MAP = {
        "idle": ("空闲", "#252626", "#ababab"),
        "starting": ("启动中", "#004883", "#b5d2ff"),
        "running": ("运行中", "#005a3c", "#9bffce"),
        "stopping": ("停止中", "#323c49", "#d9e3f4"),
        "stopped": ("已停止", "#252626", "#ababab"),
        "failed": ("错误", "#7f2927", "#ff9993"),
        "global-starting": ("启动中", "#004883", "#b5d2ff"),
        "global-running": ("运行中", "#005a3c", "#9bffce"),
        "global-partial": ("部分运行", "#323c49", "#d9e3f4"),
        "global-stopping": ("停止中", "#323c49", "#d9e3f4"),
        "global-stopped": ("已停止", "#252626", "#ababab"),
        "global-error": ("错误", "#7f2927", "#ff9993"),
        "global-idle": ("未启动", "#252626", "#ababab"),
    }

    def __init__(self, key: str = "idle") -> None:
        super().__init__()
        self.setObjectName("StatusChip")
        self.set_status(key)

    def set_status(self, key: str) -> None:
        text, background, foreground = self._STYLE_MAP.get(key, self._STYLE_MAP["idle"])
        self.setText(text)
        self.setStyleSheet(
            f"QLabel#StatusChip {{ background: {background}; color: {foreground}; }}"
        )


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


class FooterStatusBar(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("FooterBar")

        self.env_label = QLabel("环境: test")
        self.status_label = QLabel("状态: 未启动")
        self.health_label = QLabel("健康: <span style='color:#8e8e93;'>●</span>")
        self.metrics_label = QLabel("资源: -")
        self.uptime_label = QLabel("运行时长: 00:00:00")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.addWidget(self.env_label)
        layout.addSpacing(12)
        layout.addWidget(self.status_label)
        layout.addSpacing(12)
        layout.addWidget(self.health_label)
        layout.addSpacing(12)
        layout.addWidget(self.metrics_label)
        layout.addStretch(1)
        layout.addWidget(self.uptime_label)

    def update_runtime(
        self,
        environment: str,
        status: str,
        started_at: Optional[datetime],
        health_text: str = "unknown / unknown",
        metrics_text: str = "-",
    ) -> None:
        self.env_label.setText(f"环境: {environment}")
        self.status_label.setText(f"状态: {status}")
        self.health_label.setText(f"健康: {health_text}")
        self.metrics_label.setText(f"资源: {metrics_text}")
        if started_at is None:
            self.uptime_label.setText("运行时长: 00:00:00")
            return
        delta = datetime.now() - started_at
        total_seconds = int(delta.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.uptime_label.setText(f"运行时长: {hours:02d}:{minutes:02d}:{seconds:02d}")
