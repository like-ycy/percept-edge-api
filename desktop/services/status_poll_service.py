"""定时心跳服务，用于触发 UI 周期刷新（如运行时长更新）。"""

from PySide6.QtCore import QObject, QTimer, Signal


class StatusPollService(QObject):
    tick = Signal()

    def __init__(self, interval_ms: int = 1000) -> None:
        super().__init__()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
