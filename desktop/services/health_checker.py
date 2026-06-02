"""通用健康检查器：消费 HealthProbe，异步执行单次探测，通过信号回报结果。

轮询（interval / deadline）由 FlowRunner 负责，此处只提供 check_once。
支持的 probe kind:
    - "http"          HTTP GET；可选 expect={"status": int, "match": {"a.b.c": value, ...}}
    - "zmq_monitor"   Robot OS command monitor 探测（复用 scripts.debug.wait_robot_os_ready）
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from desktop.adapters.base import HealthProbe
from desktop.models.runtime_health import RuntimeHealth


def _dig(payload: object, path: str) -> object:
    current: Any = payload
    for segment in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)  # type: ignore[arg-type]
    return current


def _match_json(payload: object, expect: Mapping[str, Any]) -> bool:
    match = expect.get("match") or {}
    if not isinstance(match, Mapping):
        return False
    for path, expected in match.items():
        if _dig(payload, path) != expected:
            return False
    return True


class _HttpProbeWorker(QRunnable):
    class Signals(QObject):
        finished = Signal(str, int, bool, object)  # name, generation, ready, payload

    def __init__(self, probe: HealthProbe, name: str, generation: int) -> None:
        super().__init__()
        self._probe = probe
        self._name = name
        self._generation = generation
        self.signals = self.Signals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:  # noqa: A003
        probe = self._probe
        expect = probe.expect or {}
        try:
            with urllib.request.urlopen(probe.target, timeout=probe.timeout) as response:
                expected_status = int(expect.get("status", 200))
                if response.status != expected_status:
                    self._emit(False, None)
                    return
                body = response.read()
            payload: object
            try:
                payload = json.loads(body)
            except (ValueError, json.JSONDecodeError):
                payload = None
            ready = True
            if expect.get("match"):
                ready = isinstance(payload, dict) and _match_json(payload, expect)
            self._emit(ready, payload)
        except (urllib.error.URLError, TimeoutError, OSError):
            self._emit(False, None)

    def _emit(self, ready: bool, payload: object) -> None:
        self.signals.finished.emit(self._name, self._generation, ready, payload)


class _ZmqMonitorProbeWorker(QRunnable):
    class Signals(QObject):
        finished = Signal(str, int, bool, object)

    def __init__(self, probe: HealthProbe, name: str, generation: int, repo_root: Path) -> None:
        super().__init__()
        self._probe = probe
        self._name = name
        self._generation = generation
        self._repo_root = repo_root
        self.signals = self.Signals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:  # noqa: A003
        try:
            root_str = str(self._repo_root)
            if root_str not in sys.path:
                sys.path.append(root_str)
            from scripts.debug.wait_robot_os_ready import probe_monitor

            ready = bool(probe_monitor(self._probe.target, self._probe.timeout))
        except Exception:
            ready = False
        self.signals.finished.emit(self._name, self._generation, ready, None)


class HealthChecker(QObject):
    """单次探测分发器。poll/deadline 逻辑由调用方管理。"""

    result = Signal(str, int, bool, object)  # name, generation, ready, payload

    def __init__(self, repo_root: Path, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._repo_root = repo_root
        self._pool = QThreadPool.globalInstance()
        self._active_workers: set[QRunnable] = set()

    def check_once(self, probe: HealthProbe, *, name: str, generation: int) -> None:
        if probe.kind == "http":
            worker = _HttpProbeWorker(probe, name, generation)
        elif probe.kind == "zmq_monitor":
            worker = _ZmqMonitorProbeWorker(probe, name, generation, self._repo_root)
        else:
            raise ValueError(f"不支持的 probe kind: {probe.kind}")
        if self._pool is None:
            raise RuntimeError("QThreadPool.globalInstance() 不可用")
        worker.setAutoDelete(False)
        self._active_workers.add(worker)
        worker.signals.finished.connect(
            lambda result_name, result_generation, ready, payload, done_worker=worker: (
                self._on_worker_finished(
                    done_worker,
                    result_name,
                    result_generation,
                    ready,
                    payload,
                )
            )
        )
        self._pool.start(worker)

    def _on_worker_finished(
        self,
        worker: QRunnable,
        name: str,
        generation: int,
        ready: bool,
        payload: object,
    ) -> None:
        self._active_workers.discard(worker)
        self.result.emit(name, generation, ready, payload)


class RuntimeHealthCollector(QObject):
    """聚合型健康采集：用于 RUNNING 阶段周期性拉取 /health、/api/monitor/system、
    /api/monitor/robot 并合成 RuntimeHealth。保留旧 RuntimeHealthService 行为。"""

    health_ready = Signal(int, object)  # generation, RuntimeHealth

    def __init__(self, api_url_root: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._api_url_root = api_url_root.rstrip("/")
        self._pool = QThreadPool.globalInstance()

    def collect_async(self, generation: int) -> None:
        worker = _HealthCollectWorker(self._api_url_root, generation)
        worker.signals.finished.connect(self.health_ready.emit)
        if self._pool is None:
            raise RuntimeError("QThreadPool.globalInstance() 不可用")
        self._pool.start(worker)


class _HealthCollectWorker(QRunnable):
    class Signals(QObject):
        finished = Signal(int, object)

    def __init__(self, api_url_root: str, generation: int) -> None:
        super().__init__()
        self._api_url_root = api_url_root
        self._generation = generation
        self.signals = self.Signals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:  # noqa: A003
        try:
            health = RuntimeHealth()
            health_payload = self._get_json(f"{self._api_url_root}/health")
            if isinstance(health_payload, dict):
                data = health_payload.get("data") or {}
                health.overall = str(data.get("status", "unknown"))
                cloud_api = data.get("cloud_api") or {}
                robot_os = data.get("robot_os") or {}
                health.cloud_api = str(cloud_api.get("status", "unknown"))
                health.robot_os = str(robot_os.get("status", "unknown"))

            system_payload = self._get_json(f"{self._api_url_root}/api/monitor/system")
            if isinstance(system_payload, dict):
                data = system_payload.get("data") or {}
                cpu = data.get("cpu") or {}
                memory = data.get("memory") or {}
                disk = data.get("disk") or {}
                health.cpu_percent = self._as_float(cpu.get("cpu_percent"))
                health.memory_percent = self._as_float(memory.get("percent"))
                health.disk_percent = self._as_float(disk.get("percent"))

            robot_payload = self._get_json(f"{self._api_url_root}/api/monitor/robot")
            if isinstance(robot_payload, dict):
                data = robot_payload.get("data") or {}
                components = data.get("components") or []
                if isinstance(components, list):
                    health.robot_components = len(components)

            self.signals.finished.emit(self._generation, health)
        except Exception:
            self.signals.finished.emit(self._generation, RuntimeHealth())

    def _get_json(self, url: str) -> Optional[dict]:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if response.status != 200:
                    return None
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            return None

    def _as_float(self, value: object) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if not isinstance(value, str):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
