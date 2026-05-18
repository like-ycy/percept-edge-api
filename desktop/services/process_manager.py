"""通用进程管理器：消费 ProcessSpec，维护 QProcess 生命周期。

不关心业务语义（stage / ros 顺序），仅提供：
- spawn / stop / stop_all / force_kill_all
- 代次隔离，过滤旧进程的延迟回调
- 按行缓冲 stdout / stderr，通过 line 信号发出
- quiet(name) 抑制高噪音进程的日志输出
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal as _signal
from typing import Mapping, Optional

import psutil
from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from desktop.adapters.base import ProcessSpec, ShutdownStep
from desktop.services.process_bridge import ProcessLineBuffer


class ProcessManager(QObject):
    started = Signal(str, int)  # name, pid
    line = Signal(str, str, str)  # name, stream("stdout"|"stderr"), text
    finished = Signal(str, int, bool)  # name, exit_code, expected_stop
    error = Signal(str, str, bool)  # name, error_name, expected_stop

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._processes: dict[str, QProcess] = {}
        self._gen: dict[str, int] = {}
        self._buffers: dict[str, dict[str, ProcessLineBuffer]] = {}
        self._quiet: set[str] = set()
        self._shutdown_signal: dict[str, int] = {}
        self._shutdown_sequences: dict[str, tuple[ShutdownStep, ...]] = {}
        self._force_kill_grace: dict[str, float] = {}
        self._session_ids: dict[str, int] = {}
        self._process_group_ids: dict[str, int] = {}
        self._stopping: set[str] = set()
        self._gen_counter = 0

    # ---- 生命周期 ----

    def spawn(self, spec: ProcessSpec, env_extra: Optional[Mapping[str, str]] = None) -> int:
        """启动（或替换）指定名字的进程，返回本次启动的代次号。"""
        self._discard(spec.name, keep_gen=False)
        self._gen_counter += 1
        gen = self._gen_counter
        self._gen[spec.name] = gen

        process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        for k, v in spec.env.items():
            env.insert(k, v)
        if env_extra:
            for k, v in env_extra.items():
                env.insert(k, v)
        process.setProcessEnvironment(env)
        process.setWorkingDirectory(spec.cwd)
        if spec.shell:
            self._set_process_command(process, ["bash", "-lc", spec.cmd])
        else:
            parts = shlex.split(spec.cmd)
            if not parts:
                raise ValueError(f"空命令: spec.name={spec.name}")
            self._set_process_command(process, parts)

        name = spec.name
        self._processes[name] = process
        self._shutdown_signal[name] = int(spec.shutdown_signal)
        self._shutdown_sequences[name] = spec.effective_shutdown_sequence()
        self._force_kill_grace[name] = float(spec.force_kill_grace)
        self._buffers[name] = {
            "stdout": ProcessLineBuffer(),
            "stderr": ProcessLineBuffer(),
        }

        process.started.connect(lambda n=name, g=gen: self._on_started(n, g))
        process.readyReadStandardOutput.connect(lambda n=name: self._read(n, "stdout"))
        process.readyReadStandardError.connect(lambda n=name: self._read(n, "stderr"))
        process.finished.connect(
            lambda exit_code, _exit_status, n=name, g=gen: self._on_finished(n, g, exit_code)
        )
        process.errorOccurred.connect(lambda err, n=name, g=gen: self._on_error(n, g, err))
        process.start()
        return gen

    def stop(
        self,
        name: str,
        *,
        emergency: bool = False,
        sig: Optional[int] = None,
    ) -> None:
        process = self._processes.get(name)
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        if emergency:
            self._stopping.add(name)
            self._terminate_process_tree(
                pid=process.processId(), sig=int(_signal.SIGKILL), name=name
            )
            process.kill()
            return
        pid = process.processId()
        if pid <= 0:
            return
        sequence = self._shutdown_sequence_for(name, override_sig=sig)
        gen = self._gen.get(name, 0)
        self._stopping.add(name)
        self._run_shutdown_sequence(name=name, gen=gen, pid=pid, sequence=sequence, step_index=0)

    def stop_all(
        self,
        *,
        emergency: bool = False,
        sig: Optional[int] = None,
    ) -> None:
        for name in list(self._processes):
            self.stop(name, emergency=emergency, sig=sig)

    def force_kill_all(self) -> None:
        for name, process in self._processes.items():
            if process.state() != QProcess.ProcessState.NotRunning:
                self._stopping.update(self._processes.keys())
                self._terminate_process_tree(
                    pid=process.processId(), sig=int(_signal.SIGKILL), name=name
                )
                process.kill()

    def cleanup_for_exit(self, timeout: float = 2.0) -> None:
        """同步清理所有托管进程树，用于 Qt 退出或异常退出兜底。"""
        alive = self._alive_processes_by_name()
        max_steps = max((len(self._shutdown_sequence_for(name)) for name in alive), default=0)
        for step_index in range(max_steps):
            for name, process in list(alive.items()):
                if process.state() == QProcess.ProcessState.NotRunning:
                    alive.pop(name, None)
                    continue
                self._stopping.add(name)
                sequence = self._shutdown_sequence_for(name)
                if step_index >= len(sequence):
                    continue
                step = sequence[step_index]
                self._terminate_process_tree(
                    pid=process.processId(), sig=int(step.signal), name=name
                )

            wait_seconds = min(self._max_shutdown_step_grace(alive, step_index), timeout)
            if wait_seconds <= 0:
                continue
            for name, process in list(alive.items()):
                process.waitForFinished(max(0, int(wait_seconds * 1000)))
                if process.state() == QProcess.ProcessState.NotRunning:
                    alive.pop(name, None)

        alive_processes = list(alive.values())
        for process in alive_processes:
            if process.state() != QProcess.ProcessState.NotRunning:
                process_name = ""
                for name, managed in self._processes.items():
                    if managed is process:
                        process_name = name
                        break
                self._terminate_process_tree(
                    pid=process.processId(), sig=int(_signal.SIGKILL), name=process_name
                )
                process.kill()
                process.waitForFinished(500)

    # ---- 查询 ----

    def is_alive(self, name: str) -> bool:
        process = self._processes.get(name)
        return process is not None and process.state() != QProcess.ProcessState.NotRunning

    def any_alive(self) -> bool:
        return any(p.state() != QProcess.ProcessState.NotRunning for p in self._processes.values())

    def pid(self, name: str) -> Optional[int]:
        process = self._processes.get(name)
        if process is None:
            return None
        result = process.processId()
        return result if result > 0 else None

    def quiet(self, name: str, quiet: bool = True) -> None:
        if quiet:
            self._quiet.add(name)
        else:
            self._quiet.discard(name)

    # ---- 内部回调 ----

    def _on_started(self, name: str, gen: int) -> None:
        if self._gen.get(name) != gen:
            return
        process = self._processes.get(name)
        if process is None:
            return
        pid = process.processId()
        self._remember_process_scope(name, pid)
        self.started.emit(name, process.processId())

    def _read(self, name: str, stream: str) -> None:
        process = self._processes.get(name)
        if process is None:
            return
        if stream == "stdout":
            raw = process.readAllStandardOutput()
        else:
            raw = process.readAllStandardError()
        chunk = bytes(raw.data()).decode("utf-8", errors="replace")
        if name in self._quiet:
            return
        for text in self._buffers[name][stream].feed(chunk):
            cleaned = text.strip()
            if cleaned:
                self.line.emit(name, stream, cleaned)

    def _on_finished(self, name: str, gen: int, exit_code: int) -> None:
        if self._gen.get(name) != gen:
            return
        expected_stop = name in self._stopping
        process = self._processes.get(name)
        if not expected_stop and process is not None:
            sig = self._primary_shutdown_signal(name)
            sid = self._session_ids.get(name)
            pgid = self._process_group_ids.get(name)
            force_kill_grace = self._force_kill_grace.get(name, 2.0)
            self._terminate_process_tree(pid=process.processId(), sig=sig, name=name)
            QTimer.singleShot(
                max(0, int(force_kill_grace * 1000)),
                lambda saved_sid=sid, saved_pgid=pgid: self._force_kill_saved_scope(
                    sid=saved_sid,
                    pgid=saved_pgid,
                ),
            )
        buffers = self._buffers.get(name, {})
        if name not in self._quiet:
            for stream, buf in buffers.items():
                for text in buf.flush():
                    cleaned = text.strip()
                    if cleaned:
                        self.line.emit(name, stream, cleaned)
        self._discard(name, keep_gen=False)
        self.finished.emit(name, exit_code, expected_stop)

    def _on_error(self, name: str, gen: int, err: QProcess.ProcessError) -> None:
        if self._gen.get(name) != gen:
            return
        self.error.emit(name, err.name, name in self._stopping)

    def _discard(self, name: str, *, keep_gen: bool) -> None:
        old = self._processes.pop(name, None)
        if old is not None:
            old.deleteLater()
        self._buffers.pop(name, None)
        self._shutdown_signal.pop(name, None)
        self._shutdown_sequences.pop(name, None)
        self._force_kill_grace.pop(name, None)
        self._session_ids.pop(name, None)
        self._process_group_ids.pop(name, None)
        self._stopping.discard(name)
        if not keep_gen:
            self._gen.pop(name, None)

    def _set_process_command(self, process: QProcess, argv: list[str]) -> None:
        """用 setsid 启动托管进程，使 ROS 子进程可按独立 session 统一清理。"""
        setsid_path = shutil.which("setsid")
        if setsid_path:
            process.setProgram(setsid_path)
            process.setArguments(argv)
            return

        process.setProgram(argv[0])
        process.setArguments(argv[1:])

    def _remember_process_scope(self, name: str, pid: int) -> None:
        if pid <= 0:
            return
        try:
            sid = os.getsid(pid)
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            return
        if sid == pid:
            self._session_ids[name] = sid
        if pgid == pid:
            self._process_group_ids[name] = pgid

    def _terminate_process_tree(self, *, pid: int, sig: int, name: str = "") -> None:
        if pid <= 0 and not name:
            return
        self._signal_process_session(pid=pid, sig=sig, name=name)
        self._signal_process_group(pid=pid, sig=sig, name=name)
        if pid <= 0:
            return
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            children = []

        for child in reversed(children):
            self._signal_pid(child.pid, sig)
        self._signal_pid(pid, sig)

    def _alive_processes_by_name(self) -> dict[str, QProcess]:
        return {
            name: process
            for name, process in self._processes.items()
            if process.state() != QProcess.ProcessState.NotRunning
        }

    def _max_shutdown_step_grace(self, processes: Mapping[str, QProcess], step_index: int) -> float:
        max_grace = 0.0
        for name in processes:
            sequence = self._shutdown_sequence_for(name)
            if step_index < len(sequence):
                max_grace = max(max_grace, float(sequence[step_index].grace))
        return max_grace

    def _shutdown_sequence_for(
        self, name: str, *, override_sig: int | None = None
    ) -> tuple[ShutdownStep, ...]:
        sequence = self._shutdown_sequences.get(name)
        if sequence:
            if override_sig is None:
                return sequence
            return (ShutdownStep(signal=int(override_sig), grace=float(sequence[0].grace)),)

        sig = override_sig or self._shutdown_signal.get(name, int(_signal.SIGTERM))
        return (ShutdownStep(signal=int(sig), grace=0.0),)

    def _primary_shutdown_signal(self, name: str) -> int:
        sequence = self._shutdown_sequences.get(name)
        if sequence:
            return int(sequence[0].signal)
        return self._shutdown_signal.get(name, int(_signal.SIGTERM))

    def _run_shutdown_sequence(
        self,
        *,
        name: str,
        gen: int,
        pid: int,
        sequence: tuple[ShutdownStep, ...],
        step_index: int,
    ) -> None:
        if self._gen.get(name) != gen:
            return
        process = self._processes.get(name)
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        if step_index >= len(sequence):
            self._terminate_process_tree(pid=pid, sig=int(_signal.SIGKILL), name=name)
            process.kill()
            return

        step = sequence[step_index]
        self._terminate_process_tree(pid=pid, sig=int(step.signal), name=name)
        QTimer.singleShot(
            max(0, int(float(step.grace) * 1000)),
            lambda n=name, saved_gen=gen, saved_pid=pid, seq=sequence, index=step_index + 1: (
                self._run_shutdown_sequence(
                    name=n,
                    gen=saved_gen,
                    pid=saved_pid,
                    sequence=seq,
                    step_index=index,
                )
            ),
        )

    def _signal_process_session(self, *, pid: int, sig: int, name: str = "") -> None:
        if name:
            sid = self._session_ids.get(name)
            if sid is not None:
                self._signal_session_id(sid=sid, sig=sig)
                return
        try:
            sid = os.getsid(pid)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        if sid != pid:
            return

        self._signal_session_id(sid=sid, sig=sig)

    def _signal_session_id(self, *, sid: int, sig: int) -> None:
        if sid <= 0:
            return

        for proc in psutil.process_iter(attrs=[]):
            try:
                if os.getsid(proc.pid) == sid:
                    self._signal_pid(proc.pid, sig)
            except (ProcessLookupError, PermissionError, psutil.NoSuchProcess):
                continue

    def _signal_process_group(self, *, pid: int, sig: int, name: str = "") -> None:
        if name:
            pgid = self._process_group_ids.get(name)
            if pgid is not None:
                self._signal_process_group_id(pgid=pgid, sig=sig)
                return
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        if pgid != pid:
            return

        self._signal_process_group_id(pgid=pgid, sig=sig)

    def _signal_process_group_id(self, *, pgid: int, sig: int) -> None:
        if pgid <= 0:
            return
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return

    def _force_kill_saved_scope(self, *, sid: int | None, pgid: int | None) -> None:
        if sid is not None:
            self._signal_session_id(sid=sid, sig=int(_signal.SIGKILL))
        if pgid is not None:
            self._signal_process_group_id(pgid=pgid, sig=int(_signal.SIGKILL))

    def _signal_pid(self, pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
