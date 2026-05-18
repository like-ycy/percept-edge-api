"""进程监控服务"""

from __future__ import annotations

import asyncio
import os
import platform
import time
from datetime import datetime, timezone

import psutil

from src.core.logging import logger
from src.models.database import now_shanghai
from src.schemas.process_monitor import (
    ProcessInfo,
    ProcessMonitorResponse,
    SystemOverview,
)


class ProcessMonitor:
    """主进程及子进程指标采样器"""

    def __init__(
        self,
        interval: float,
        refresh_window: float,
        cmdline_max_len: int,
    ) -> None:
        self._interval = interval
        self._refresh_window = refresh_window
        self._cmdline_max_len = cmdline_max_len
        self._main_pid = os.getpid()
        self._main_proc = psutil.Process(self._main_pid)
        self._shanghai_tz = now_shanghai().tzinfo
        self._snapshot: ProcessMonitorResponse | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"ProcessMonitor 已启动，间隔 {self._interval}s，刷新窗口 {self._refresh_window}s"
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("ProcessMonitor 已停止")

    async def refresh(self) -> ProcessMonitorResponse:
        async with self._lock:
            data = await asyncio.to_thread(self._sample, True)
            self._snapshot = data
            return data

    def get_snapshot(self) -> ProcessMonitorResponse | None:
        return self._snapshot

    async def _loop(self) -> None:
        while self._running:
            try:
                async with self._lock:
                    self._snapshot = await asyncio.to_thread(self._sample, False)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ProcessMonitor 后台采样异常")
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                raise

    def _sample(self, blocking: bool) -> ProcessMonitorResponse:
        procs = self._collect_procs()
        for p in procs:
            try:
                p.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        if blocking:
            time.sleep(self._refresh_window)
        infos: list[ProcessInfo] = []
        for p in procs:
            info = self._build_info(p)
            if info is not None:
                infos.append(info)
        infos.sort(key=lambda i: 0 if i.pid == self._main_pid else 1)
        system = self._build_system_overview()
        return ProcessMonitorResponse(
            sampled_at=now_shanghai(),
            from_cache=False,
            main_pid=self._main_pid,
            system=system,
            processes=infos,
        )

    def _collect_procs(self) -> list[psutil.Process]:
        result: list[psutil.Process] = [self._main_proc]
        try:
            result.extend(self._main_proc.children(recursive=True))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            logger.warning("ProcessMonitor 枚举子进程失败")
        return result

    def _build_info(self, proc: psutil.Process) -> ProcessInfo | None:
        try:
            with proc.oneshot():
                pid = proc.pid
                name = proc.name()
                cmdline_list = proc.cmdline() or []
                cmdline = " ".join(cmdline_list)[: self._cmdline_max_len]
                status = proc.status()
                parent_pid = proc.ppid() if pid != self._main_pid else None
                create_ts = proc.create_time()
                cpu_percent = proc.cpu_percent(None)
                num_threads = proc.num_threads()
                mem = proc.memory_info()
                memory_percent = float(proc.memory_percent())
                cpu_num = self._safe_attr(proc, "cpu_num")
                cpu_affinity = self._safe_attr(proc, "cpu_affinity")
                num_fds = self._safe_attr(proc, "num_fds")
            shanghai_tz = self._shanghai_tz
            create_dt = datetime.fromtimestamp(create_ts, tz=timezone.utc).astimezone(shanghai_tz)
            return ProcessInfo(
                pid=pid,
                parent_pid=parent_pid,
                name=name,
                cmdline=cmdline,
                status=status,
                create_time=create_dt,
                cpu_percent=float(cpu_percent),
                cpu_num=cpu_num,
                cpu_affinity=list(cpu_affinity) if cpu_affinity is not None else None,
                num_threads=int(num_threads),
                memory_rss_mb=mem.rss / (1024 * 1024),
                memory_percent=memory_percent,
                num_fds=int(num_fds) if num_fds is not None else None,
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None

    def _build_system_overview(self) -> SystemOverview:
        vm = psutil.virtual_memory()
        per_cpu = psutil.cpu_percent(percpu=True)
        try:
            load = os.getloadavg()
        except (OSError, AttributeError):
            load = (0.0, 0.0, 0.0)
        physical = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 1
        logical = psutil.cpu_count(logical=True) or 1
        return SystemOverview(
            cpu_count_logical=int(logical),
            cpu_count_physical=int(physical),
            per_cpu_percent=[float(x) for x in per_cpu],
            load_avg_1_5_15=(float(load[0]), float(load[1]), float(load[2])),
            memory_total_mb=vm.total / (1024 * 1024),
            memory_used_mb=vm.used / (1024 * 1024),
            memory_percent=float(vm.percent),
            platform=platform.system(),
        )

    @staticmethod
    def _safe_attr(proc: psutil.Process, name: str):
        fn = getattr(proc, name, None)
        if fn is None:
            return None
        try:
            return fn()
        except (psutil.AccessDenied, AttributeError, NotImplementedError, OSError):
            return None
