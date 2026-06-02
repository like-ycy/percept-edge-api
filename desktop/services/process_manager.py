"""通用进程管理器：消费 ProcessSpec，维护 QProcess 生命周期。

不关心业务语义（stage / ros 顺序），仅提供：
- spawn / stop / stop_all / force_kill_all
- 代次隔离，过滤旧进程的延迟回调
- 按行缓冲 stdout / stderr，通过 line 信号发出
- quiet(name) 抑制高噪音进程的日志输出
"""

from __future__ import annotations

import os
import re
import shlex
import signal as _signal
from collections.abc import Sequence
from typing import Mapping, Optional

import psutil
from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from desktop.adapters.base import ProcessSpec, ShutdownStep
from desktop.services.process_bridge import ProcessLineBuffer

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


class ProcessManager(QObject):
    started = Signal(str, int)  # name, pid
    line = Signal(str, str, str)  # name, stream("stdout"|"stderr"), text
    finished = Signal(str, int, bool)  # name, exit_code, expected_stop
    error = Signal(str, str, bool)  # name, error_name, expected_stop
    serial_stop_finished = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._processes: dict[str, QProcess] = {}
        self._gen: dict[str, int] = {}
        self._buffers: dict[str, dict[str, ProcessLineBuffer]] = {}
        self._quiet: set[str] = set()
        self._shutdown_signal: dict[str, int] = {}
        self._shutdown_sequences: dict[str, tuple[ShutdownStep, ...]] = {}
        self._stopping: set[str] = set()
        self._gen_counter = 0
        self._serial_stop_queue: list[str] = []
        self._serial_stop_current: tuple[str, int] | None = None

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
            process.setProgram("bash")
            process.setArguments(["-lc", spec.cmd])
        else:
            parts = shlex.split(spec.cmd)
            if not parts:
                raise ValueError(f"空命令: spec.name={spec.name}")
            process.setProgram(parts[0])
            process.setArguments(parts[1:])

        name = spec.name
        self._processes[name] = process
        self._shutdown_signal[name] = int(spec.shutdown_signal)
        self._shutdown_sequences[name] = spec.effective_shutdown_sequence()
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
            self._terminate_process_tree(pid=process.processId(), sig=int(_signal.SIGKILL))
            process.kill()
            return
        pid = process.processId()
        if pid <= 0:
            return
        effective_sig = (
            sig if sig is not None else self._shutdown_signal.get(name, int(_signal.SIGTERM))
        )
        self._stopping.add(name)
        self._terminate_process_tree(pid=pid, sig=effective_sig)

    def stop_all(
        self,
        *,
        emergency: bool = False,
        sig: Optional[int] = None,
        order: Optional[Sequence[str]] = None,
    ) -> None:
        if emergency:
            self.force_kill_all()
            self.serial_stop_finished.emit()
            return
        if sig is not None:
            for name in self._resolve_stop_order(order):
                self.stop(name, emergency=False, sig=sig)
            self.serial_stop_finished.emit()
            return
        self.stop_all_serial(order=order)

    def stop_all_serial(self, *, order: Optional[Sequence[str]] = None) -> None:
        """按指定顺序串行停止进程；缺省为启动顺序的逆序。"""
        if self._serial_stop_current is not None or self._serial_stop_queue:
            return
        self._serial_stop_queue = self._resolve_stop_order(order)
        self._serial_stop_current = None
        self._advance_serial_stop()

    def force_kill_all(self) -> None:
        for process in self._processes.values():
            if process.state() != QProcess.ProcessState.NotRunning:
                self._stopping.update(self._processes.keys())
                self._terminate_process_tree(pid=process.processId(), sig=int(_signal.SIGKILL))
                process.kill()

    def cleanup_for_exit(self, timeout: float = 2.0) -> None:
        """同步清理所有托管进程树，用于 Qt 退出或异常退出兜底。"""
        alive_processes = [
            process
            for process in self._processes.values()
            if process.state() != QProcess.ProcessState.NotRunning
        ]
        for process in alive_processes:
            for name, managed in self._processes.items():
                if managed is process:
                    self._stopping.add(name)
            self._terminate_process_tree(pid=process.processId(), sig=int(_signal.SIGTERM))
        for process in alive_processes:
            process.waitForFinished(max(0, int(timeout * 1000)))
        for process in alive_processes:
            if process.state() != QProcess.ProcessState.NotRunning:
                self._terminate_process_tree(pid=process.processId(), sig=int(_signal.SIGKILL))
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
            cleaned = _strip_ansi(text.strip())
            if cleaned:
                self.line.emit(name, stream, cleaned)

    def _on_finished(self, name: str, gen: int, exit_code: int) -> None:
        if self._gen.get(name) != gen:
            return
        expected_stop = name in self._stopping
        buffers = self._buffers.get(name, {})
        if name not in self._quiet:
            for stream, buf in buffers.items():
                for text in buf.flush():
                    cleaned = _strip_ansi(text.strip())
                    if cleaned:
                        self.line.emit(name, stream, cleaned)
        self._discard(name, keep_gen=False)
        self.finished.emit(name, exit_code, expected_stop)
        if self._serial_stop_current == (name, gen):
            self._advance_serial_stop()

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
        self._stopping.discard(name)
        if not keep_gen:
            self._gen.pop(name, None)

    def _resolve_stop_order(self, order: Optional[Sequence[str]]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        raw_order = tuple(order) if order is not None else tuple(reversed(self._processes))
        for name in raw_order:
            if name in seen or not self.is_alive(name):
                continue
            ordered.append(name)
            seen.add(name)
        for name in reversed(tuple(self._processes)):
            if name in seen or not self.is_alive(name):
                continue
            ordered.append(name)
            seen.add(name)
        return ordered

    def _advance_serial_stop(self) -> None:
        self._serial_stop_current = None
        while self._serial_stop_queue:
            name = self._serial_stop_queue.pop(0)
            process = self._processes.get(name)
            if process is None or process.state() == QProcess.ProcessState.NotRunning:
                continue
            gen = self._gen.get(name)
            pid = process.processId()
            if gen is None or pid <= 0:
                continue
            self._serial_stop_current = (name, gen)
            sequence = self._shutdown_sequences.get(
                name,
                (
                    ShutdownStep(
                        signal=self._shutdown_signal.get(name, int(_signal.SIGTERM)), grace=0.0
                    ),
                ),
            )
            self._run_shutdown_sequence(name=name, gen=gen, pid=pid, sequence=sequence)
            return
        self.serial_stop_finished.emit()

    def _run_shutdown_sequence(
        self,
        *,
        name: str,
        gen: int,
        pid: int,
        sequence: Sequence[ShutdownStep],
        step_index: int = 0,
    ) -> None:
        if self._gen.get(name) != gen:
            return
        process = self._processes.get(name)
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            if self._serial_stop_current == (name, gen):
                self._advance_serial_stop()
            return
        if step_index >= len(sequence):
            if self._serial_stop_current == (name, gen):
                self._advance_serial_stop()
            return

        step = sequence[step_index]
        self._stopping.add(name)
        self._terminate_process_tree(pid=pid, sig=int(step.signal))
        if int(step.signal) == int(_signal.SIGKILL):
            process.kill()

        delay_ms = max(0, int(step.grace * 1000))
        QTimer.singleShot(
            delay_ms,
            lambda n=name,
            g=gen,
            p=pid,
            s=tuple(sequence),
            i=step_index + 1: self._run_shutdown_sequence(
                name=n,
                gen=g,
                pid=p,
                sequence=s,
                step_index=i,
            ),
        )

    def _terminate_process_tree(self, *, pid: int, sig: int) -> None:
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

    def _signal_pid(self, pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
