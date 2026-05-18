"""Desktop 入口装配：读取 profile → 构建 RuntimeFacade → 挂 MainWindow。"""

from __future__ import annotations

import faulthandler
import signal
import sys
import traceback
from pathlib import Path
from types import TracebackType
from typing import TextIO

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from desktop.profiles import load_profile  # noqa: F401  触发 profile 注册
from desktop.services.runtime_facade import RuntimeFacade
from desktop.widgets.main_window import MainWindow

_FAULT_LOG_FILE: TextIO | None = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _desktop_root() -> Path:
    return Path(__file__).resolve().parent


def _stylesheet_path() -> Path:
    return _desktop_root() / "theme" / "styles.qss"


def _icon_path() -> Path:
    return _desktop_root() / "assets" / "icons" / "icon.png"


def _crash_log_path() -> Path:
    return Path.home() / ".percept_edge" / "desktop_crash.log"


def _enable_fault_handler() -> None:
    global _FAULT_LOG_FILE  # noqa: PLW0603

    if _FAULT_LOG_FILE is not None:
        return
    log_path = _crash_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _FAULT_LOG_FILE = log_path.open("a", encoding="utf-8")
    faulthandler.enable(_FAULT_LOG_FILE, all_threads=True)


def _load_stylesheet() -> str:
    path = _stylesheet_path()
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def create_application() -> QApplication:
    app = QApplication(sys.argv)
    app.setApplicationName("Percept Edge Runtime Console")
    icon_path = _icon_path()
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    style = _load_stylesheet()
    if style:
        app.setStyleSheet(style)
    return app


def run(
    profile_name: str | None = None,
    environment: str = "test",
    launch_mode: str | None = None,
) -> int:
    _enable_fault_handler()
    app = create_application()
    profile = load_profile(profile_name)
    facade = RuntimeFacade(
        profile=profile,
        repo_root=_repo_root(),
        environment=environment,
        launch_mode=launch_mode,
    )
    window = MainWindow(facade)
    app.aboutToQuit.connect(facade.cleanup_for_exit)

    previous_excepthook = sys.excepthook

    def _handle_exception(
        exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        try:
            facade.cleanup_for_exit()
            log_path = _crash_log_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as fp:
                traceback.print_exception(exc_type, exc, tb, file=fp)
        finally:
            previous_excepthook(exc_type, exc, tb)

    sys.excepthook = _handle_exception

    def _handle_interrupt(_signum: int, _frame: object) -> None:
        QTimer.singleShot(0, window.close)

    signal.signal(signal.SIGINT, _handle_interrupt)
    window.show()
    return app.exec()
