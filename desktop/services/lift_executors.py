"""升降控制执行器。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from PySide6.QtCore import QObject, QProcess, QRunnable, QThreadPool, Signal, Slot


class LiftExecutor(Protocol):
    def set_height(self, height: int) -> None:
        """设置升降高度。"""


class LiftProcess(Protocol):
    def start(self, program: str, arguments: list[str]) -> None:
        """启动升降控制命令。"""


class LiftApiClient(Protocol):
    def post_json(self, path: str, payload: dict[str, int]) -> object:
        """发送升降控制 JSON 请求。"""


class WorkerPool(Protocol):
    def start(self, worker: QRunnable) -> None:
        """启动异步 worker。"""


class ScriptLiftExecutor(QObject):
    """通过本地脚本控制升降高度。"""

    finished = Signal(bool, str)

    def __init__(
        self,
        python_bin: str,
        script_path: str,
        process_factory: Callable[[], LiftProcess] | None = None,
    ) -> None:
        super().__init__()
        self._python_bin = python_bin
        self._script_path = script_path
        self._process = process_factory() if process_factory is not None else QProcess(self)
        self._running = False
        self._connect_process_signals()

    def set_height(self, height: int) -> None:
        if self._running:
            self.finished.emit(False, "升降命令正在执行")
            return
        self._running = True
        self._process.start(self._python_bin, [self._script_path, str(height)])

    def _connect_process_signals(self) -> None:
        finished = getattr(self._process, "finished", None)
        if finished is not None:
            finished.connect(self._on_process_finished)
        error_occurred = getattr(self._process, "errorOccurred", None)
        if error_occurred is not None:
            error_occurred.connect(self._on_process_error)

    def _on_process_finished(self, exit_code: int = 0, *_args: object) -> None:
        self._running = False
        if exit_code == 0:
            self.finished.emit(True, "")
            return
        self.finished.emit(False, f"升降脚本退出码: {exit_code}")

    def _on_process_error(self, error: object) -> None:
        self._running = False
        self.finished.emit(False, f"升降脚本启动失败: {error}")


class _ApiLiftWorker(QRunnable):
    class Signals(QObject):
        finished = Signal(bool, str)

    def __init__(self, client: LiftApiClient, api_path: str, height: int) -> None:
        super().__init__()
        self._client = client
        self._api_path = api_path
        self._height = height
        self.signals = self.Signals()
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:  # noqa: A003
        try:
            response = self._client.post_json(self._api_path, {"height": self._height})
            self._validate_response(response)
        except Exception as exc:
            self.signals.finished.emit(False, str(exc))
            return
        self.signals.finished.emit(True, "")

    @staticmethod
    def _validate_response(response: object) -> None:
        if not isinstance(response, Mapping):
            raise RuntimeError("本地 API 响应必须是 JSON object")
        payload: Mapping[str, object] = {str(key): value for key, value in response.items()}
        code = payload.get("code", 200)
        if code != 200:
            message = payload.get("msg") or payload.get("message") or "本地 API 返回失败"
            raise RuntimeError(str(message))


class ApiLiftExecutor(QObject):
    """通过本地 API 控制升降高度。"""

    finished = Signal(bool, str)

    def __init__(
        self,
        client: LiftApiClient,
        api_path: str,
        pool: WorkerPool | None = None,
    ) -> None:
        super().__init__()
        self._client = client
        self._api_path = api_path
        self._pool = pool or QThreadPool.globalInstance()
        self._active_worker: _ApiLiftWorker | None = None

    def set_height(self, height: int) -> None:
        if self._active_worker is not None:
            self.finished.emit(False, "升降命令正在执行")
            return
        if self._pool is None:
            self.finished.emit(False, "QThreadPool.globalInstance() 不可用")
            return
        worker = _ApiLiftWorker(self._client, self._api_path, height)
        self._active_worker = worker
        worker.signals.finished.connect(
            lambda success, message, done_worker=worker: self._on_worker_finished(
                done_worker,
                success,
                message,
            )
        )
        self._pool.start(worker)

    def _on_worker_finished(
        self,
        worker: _ApiLiftWorker,
        success: bool,
        message: str,
    ) -> None:
        if self._active_worker is worker:
            self._active_worker = None
        self.finished.emit(success, message)
