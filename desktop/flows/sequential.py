"""顺序执行流水线。

消费 Step 列表，驱动 ProcessManager / HealthChecker，按阶段推进，
通过 FlowEvent 回调通知 UI。并行分组暂不支持（parallel_group 字段保留）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from PySide6.QtCore import QObject, QTimer, Slot

from desktop.adapters.base import Adapter, BuildContext, HealthProbe, ProcessSpec
from desktop.flows.base import (
    FlowEvent,
    FlowEventCallback,
    OnFail,
    StageStatus,
    Step,
    StepKind,
)
from desktop.services.health_checker import HealthChecker
from desktop.services.process_manager import ProcessManager


@dataclass
class _WaitState:
    kind: str  # "spawn_started" | "run_once_finish" | "grace_check" | "wait_health" | "sleep"
    name: str = ""  # adapter / process name 相关时使用
    probe_generation: int = 0
    deadline_ts: float = 0.0


# 结构化遵循 FlowRunner Protocol（不通过继承，避免 QObject 元类冲突）
class SequentialFlowRunner(QObject):
    """串行 FlowRunner 实现。一次只推进一个 Step，信号触发继续。"""

    def __init__(
        self,
        steps: Sequence[Step],
        build_ctx: BuildContext,
        adapters: Mapping[str, Adapter],
        process_manager: ProcessManager,
        health_checker: HealthChecker,
        env_extra: Optional[Mapping[str, str]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.steps: Sequence[Step] = tuple(steps)
        self._ctx = build_ctx
        self._adapters = dict(adapters)
        self._pm = process_manager
        self._hc = health_checker
        self._env_extra = dict(env_extra or {})
        self._on_event: Optional[FlowEventCallback] = None
        self._step_index = 0
        self._wait: Optional[_WaitState] = None
        self._probe_gen_counter = 0
        self._stopped = False
        self._spawned_names: set[str] = set()

        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._tick_health)
        self._sleep_timer = QTimer(self)
        self._sleep_timer.setSingleShot(True)
        self._sleep_timer.timeout.connect(self._on_sleep_done)

        self._pm.started.connect(self._on_proc_started)
        self._pm.finished.connect(self._on_proc_finished)
        self._pm.error.connect(self._on_proc_error)
        self._hc.result.connect(self._on_probe_result)

    # ---- FlowRunner 接口 ----

    def start(self, on_event: FlowEventCallback) -> None:
        self._on_event = on_event
        self._step_index = 0
        self._stopped = False
        self._wait = None
        self._spawned_names.clear()
        self._advance()

    def stop(self, *, stop_processes: bool = True) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._cancel_waiters()
        self._disconnect_signals()
        if stop_processes:
            self._pm.stop_all(emergency=False)
        self._emit(
            FlowEvent(
                step_index=self._step_index,
                stage="flow",
                status=StageStatus.STOPPING,
                message="收到停止请求",
            )
        )

    def is_stopped(self) -> bool:
        return self._stopped

    def _disconnect_signals(self) -> None:
        for signal_obj, slot in (
            (self._pm.started, self._on_proc_started),
            (self._pm.finished, self._on_proc_finished),
            (self._pm.error, self._on_proc_error),
            (self._hc.result, self._on_probe_result),
        ):
            try:
                signal_obj.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    # ---- 推进主循环 ----

    def _advance(self) -> None:
        if self._stopped:
            return
        if self._step_index >= len(self.steps):
            self._emit(
                FlowEvent(
                    step_index=self._step_index,
                    stage="flow",
                    status=StageStatus.RUNNING,
                    message="所有步骤执行完毕",
                )
            )
            return

        step = self.steps[self._step_index]
        self._emit(
            FlowEvent(
                step_index=self._step_index,
                stage=step.stage_label or step.adapter or step.kind.value,
                status=StageStatus.STARTING,
            )
        )
        kind = step.kind
        if kind is StepKind.SPAWN:
            self._do_spawn(step, run_once=False)
        elif kind is StepKind.RUN_ONCE:
            self._do_spawn(step, run_once=True)
        elif kind is StepKind.SHELL_ONCE:
            self._do_shell_once(step)
        elif kind is StepKind.GRACE_CHECK:
            self._do_grace_check(step)
        elif kind is StepKind.WAIT_HEALTH:
            self._do_wait_health(step)
        elif kind is StepKind.SLEEP:
            self._wait = _WaitState(kind="sleep")
            self._sleep_timer.start(max(0, int(step.duration * 1000)))
        elif kind is StepKind.GATE:
            self._do_gate(step)
        else:
            self._fail(step, f"未支持的 StepKind: {kind}")

    # ---- Step 执行细节 ----

    def _do_spawn(self, step: Step, *, run_once: bool) -> None:
        adapter = self._require_adapter(step)
        if adapter is None:
            return
        spec = adapter.build_spec(self._ctx)
        self._wait = _WaitState(
            kind="run_once_finish" if run_once else "spawn_started",
            name=spec.name,
        )
        self._spawned_names.add(spec.name)
        self._pm.spawn(spec, env_extra=self._env_extra)

    def _do_shell_once(self, step: Step) -> None:
        extra = step.extra or {}
        name = str(extra.get("name") or f"shell_{self._step_index}")
        cmd = extra.get("cmd")
        cwd = extra.get("cwd")
        if not isinstance(cmd, str) or not cmd:
            self._fail(step, "SHELL_ONCE 步骤缺少 extra['cmd']")
            return
        if not isinstance(cwd, str) or not cwd:
            self._fail(step, "SHELL_ONCE 步骤缺少 extra['cwd']")
            return
        spec = ProcessSpec(name=name, cmd=cmd, cwd=cwd, shell=True)
        self._wait = _WaitState(kind="run_once_finish", name=name)
        self._spawned_names.add(name)
        self._pm.spawn(spec, env_extra=self._env_extra)

    def _do_grace_check(self, step: Step) -> None:
        if not step.adapter:
            self._fail(step, "GRACE_CHECK 步骤缺少 adapter 名")
            return
        self._wait = _WaitState(kind="grace_check", name=step.adapter)
        self._sleep_timer.start(max(0, int(step.duration * 1000)))

    def _do_wait_health(self, step: Step) -> None:
        adapter = self._require_adapter(step)
        if adapter is None:
            return
        probe = adapter.build_probe(self._ctx)
        if probe is None:
            self._fail(step, f"adapter {adapter.name} 未提供 HealthProbe")
            return
        self._probe_gen_counter += 1
        now = _now()
        self._wait = _WaitState(
            kind="wait_health",
            name=adapter.name,
            probe_generation=self._probe_gen_counter,
            deadline_ts=now + probe.deadline,
        )
        self._health_timer.start(max(50, int(probe.interval * 1000)))
        self._dispatch_probe(probe)

    def _do_gate(self, step: Step) -> None:
        fn = (step.extra or {}).get("fn")
        if not callable(fn):
            gate_name = (step.extra or {}).get("name")
            gate_handlers = self._ctx.extra.get("gate_handlers")
            if isinstance(gate_name, str) and isinstance(gate_handlers, Mapping):
                fn = gate_handlers.get(gate_name)
        if not callable(fn):
            self._fail(step, "GATE 步骤缺少 extra['fn'] 或已注册的 gate handler")
            return
        try:
            ok = bool(fn(self._ctx))
        except Exception as exc:
            self._fail(step, f"GATE 回调异常: {exc}")
            return
        if not ok:
            self._fail(step, "GATE 校验未通过")
            return
        self._on_step_done(step)

    # ---- 进程信号 ----

    @Slot(str, int)
    def _on_proc_started(self, name: str, pid: int) -> None:
        if self._stopped or self._wait is None:
            return
        if self._wait.kind == "spawn_started" and self._wait.name == name:
            self._on_step_done(self.steps[self._step_index], payload={"pid": pid})

    @Slot(str, int, bool)
    def _on_proc_finished(self, name: str, exit_code: int, expected_stop: bool = False) -> None:
        if self._stopped:
            return
        if expected_stop:
            return
        wait = self._wait
        if wait is not None and wait.kind == "run_once_finish" and wait.name == name:
            self._spawned_names.discard(name)
            step = self.steps[self._step_index]
            if exit_code == 0:
                self._on_step_done(step, payload={"exit_code": exit_code})
            else:
                self._fail(step, f"{name} 失败，exit_code={exit_code}")
            return

        if name in self._spawned_names:
            # 非等待期或后续步骤等待期：长驻进程意外退出。
            self._fail_running_process(name, f"{name} 意外退出，exit_code={exit_code}")
            return

        # 其他未跟踪进程死亡 = 当前步骤失败
        self._fail_unexpected_exit(name, exit_code)

    @Slot(str, str, bool)
    def _on_proc_error(self, name: str, error_name: str, expected_stop: bool = False) -> None:
        if self._stopped or expected_stop or self._wait is None:
            if not self._stopped and not expected_stop and name in self._spawned_names:
                self._fail_running_process(name, f"{name} 进程错误: {error_name}")
            return
        if self._wait.name == name:
            self._fail(self.steps[self._step_index], f"{name} 进程错误: {error_name}")

    # ---- 健康探测 ----

    def _tick_health(self) -> None:
        wait = self._wait
        if wait is None or wait.kind != "wait_health":
            self._health_timer.stop()
            return
        if not self._pm.is_alive(wait.name):
            self._health_timer.stop()
            self._fail(self.steps[self._step_index], f"{wait.name} 在 ready 前退出")
            return
        if _now() >= wait.deadline_ts:
            self._health_timer.stop()
            self._fail(self.steps[self._step_index], f"{wait.name} 未在限定时间内 ready")
            return
        adapter = self._adapters.get(wait.name)
        if adapter is None:
            return
        probe = adapter.build_probe(self._ctx)
        if probe is None:
            return
        self._dispatch_probe(probe)

    def _dispatch_probe(self, probe: HealthProbe) -> None:
        wait = self._wait
        if wait is None or wait.kind != "wait_health":
            return
        self._hc.check_once(probe, name=wait.name, generation=wait.probe_generation)

    @Slot(str, int, bool, object)
    def _on_probe_result(self, name: str, generation: int, ready: bool, payload: object) -> None:
        wait = self._wait
        if wait is None or wait.kind != "wait_health":
            return
        if wait.name != name or wait.probe_generation != generation:
            return
        if not ready:
            return  # 继续轮询，由 _tick_health 处理超时
        self._health_timer.stop()
        step = self.steps[self._step_index]
        self._on_step_done(step, payload={"probe": payload})

    # ---- 定时器回调 ----

    def _on_sleep_done(self) -> None:
        wait = self._wait
        if wait is None or self._stopped:
            return
        step = self.steps[self._step_index]
        if wait.kind == "sleep":
            self._on_step_done(step)
        elif wait.kind == "grace_check":
            if self._pm.is_alive(wait.name):
                self._on_step_done(step)
            else:
                self._fail(step, f"{wait.name} 在 grace 窗口内退出")

    # ---- 收敛路径 ----

    def _on_step_done(self, step: Step, payload: Optional[Mapping[str, Any]] = None) -> None:
        self._wait = None
        self._emit(
            FlowEvent(
                step_index=self._step_index,
                stage=step.stage_label or step.adapter or step.kind.value,
                status=StageStatus.RUNNING,
                payload=payload,
            )
        )
        self._step_index += 1
        self._advance()

    def _fail(self, step: Step, message: str) -> None:
        self._cancel_waiters()
        self._emit(
            FlowEvent(
                step_index=self._step_index,
                stage=step.stage_label or step.adapter or step.kind.value,
                status=StageStatus.FAILED,
                message=message,
            )
        )
        if step.on_fail is OnFail.CONTINUE:
            self._wait = None
            self._step_index += 1
            self._advance()
        else:
            self._stopped = True
            self._pm.stop_all(emergency=False)

    def _fail_unexpected_exit(self, name: str, exit_code: int) -> None:
        if self._step_index >= len(self.steps):
            return
        step = self.steps[self._step_index]
        self._fail(step, f"{name} 意外退出，exit_code={exit_code}")

    def _fail_running_process(self, name: str, message: str) -> None:
        if name not in self._spawned_names:
            return
        self._spawned_names.discard(name)
        self._cancel_waiters()
        self._emit(
            FlowEvent(
                step_index=self._step_index,
                stage=self._stage_for_process(name),
                status=StageStatus.FAILED,
                message=message,
            )
        )
        self._stopped = True
        self._pm.stop_all(emergency=False)

    def _stage_for_process(self, name: str) -> str:
        for step in self.steps:
            if step.adapter == name:
                return step.stage_label or name
            extra = step.extra or {}
            if extra.get("name") == name:
                return step.stage_label or name
        return name

    def _cancel_waiters(self) -> None:
        self._wait = None
        self._health_timer.stop()
        self._sleep_timer.stop()

    def _require_adapter(self, step: Step) -> Optional[Adapter]:
        if not step.adapter:
            self._fail(step, f"{step.kind.value} 步骤缺少 adapter 名")
            return None
        adapter = self._adapters.get(step.adapter)
        if adapter is None:
            self._fail(step, f"未找到 adapter: {step.adapter}")
            return None
        return adapter

    def _emit(self, event: FlowEvent) -> None:
        if self._on_event is not None:
            self._on_event(event)


def _now() -> float:
    import time

    return time.monotonic()
