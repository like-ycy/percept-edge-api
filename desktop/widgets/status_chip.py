"""通用状态徽章组件。"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel


class StatusChip(QLabel):
    """用于运行阶段、顶栏和升降台卡片的状态徽章。"""

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
