"""Runtime 门面：把 FlowRunner + ProcessManager + HealthChecker + RuntimeStateMachine
串起来，对外暴露与旧 RuntimeController 等价的信号/方法，供 UI 消费。

职责：
- 根据 profile + RuntimeConfig 构建 BuildContext / adapter 映射 / Step 列表 / FlowRunner
- 订阅 FlowEvent → 更新状态机 → 发 snapshot_changed
- 订阅 ProcessManager.line → 合成 LogEntry → 发 log_emitted
- 运行中阶段周期性拉取 RuntimeHealth 填充快照
- 启停编排、代次隔离
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.request import urlopen

import psutil
from PySide6.QtCore import QObject, QTimer, Signal

from desktop.adapters.base import Adapter, BuildContext, ProcessSpec
from desktop.flows.base import FlowEvent, StageStatus, Step
from desktop.flows.sequential import SequentialFlowRunner
from desktop.models.log_entry import LogEntry
from desktop.models.runtime_state import RuntimeState
from desktop.models.stage_state import StageState
from desktop.profiles.base import RobotProfile
from desktop.services.config_loader import LiftControlConfig, RuntimeConfig
from desktop.services.health_checker import HealthChecker, RuntimeHealthCollector
from desktop.services.process_manager import ProcessManager
from desktop.services.runtime_state import RuntimeStateMachine, RuntimeStateStore


class RuntimeFacade(QObject):
    log_emitted = Signal(object)  # LogEntry
    snapshot_changed = Signal(object)  # RuntimeSnapshot

    _QUIET_PROCESS_NAMES = frozenset(
        {
            "ros_slave",
            "ros_master",
            "ros_master1",
            "ros_pos_follow1",
            "ros_master2",
            "ros_pos_follow2",
        }
    )
    _UPLOAD_STOP_MAX_WAIT_SECONDS = 300.0

    def __init__(
        self,
        profile: RobotProfile,
        repo_root: Path,
        environment: str = "test",
        launch_mode: str | None = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._profile = profile
        self._repo_root = repo_root
        self._environment = environment
        self._launch_modes = tuple(profile.launch_modes)
        self._launch_mode = _normalize_launch_mode(
            launch_mode or os.getenv("PERCEPT_LAUNCH_MODE"), self._launch_modes
        )
        self._config = self._load_config(environment)

        self._pm = ProcessManager(parent=self)
        for name in self._QUIET_PROCESS_NAMES:
            self._pm.quiet(name, True)
        self._hc = HealthChecker(repo_root, parent=self)

        self._adapters_map: dict[str, Adapter] = {a.name: a for a in profile.adapters}
        self._steps: tuple[Step, ...] = tuple(profile.flow_factory(profile, self._config))
        self._label_map: dict[str, str] = _build_label_map(profile.adapters, self._steps)

        initial_stages = _build_initial_stages(self._steps)
        self._state_machine = RuntimeStateMachine(
            environment=environment, initial_stages=initial_stages
        )
        self._state_store = RuntimeStateStore(
            Path(tempfile.gettempdir()) / "percept_runtime" / f"state_{os.getpid()}.json"
        )

        self._runner: Optional[SequentialFlowRunner] = None
        self._health_collector: Optional[RuntimeHealthCollector] = None
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(max(1000, int(self._config.runtime_health_interval * 1000)))
        self._health_timer.timeout.connect(self._refresh_health)
        self._health_gen: int = 0
        self._started_at: Optional[datetime] = None
        self._close_pending = False
        self._restart_pending = False
        self._finalized = True  # 初始未启动视为已收敛
        self._upload_stop_wait_started_at: float | None = None
        self._vr_ready_confirmation_handler: Callable[[int], bool] | None = None

        self._pm.line.connect(self._on_process_line)
        self._pm.started.connect(self._on_process_started)
        self._pm.finished.connect(self._on_process_finished)
        self._pm.error.connect(self._on_process_error)

    # ---- 对外属性 ----

    @property
    def environment(self) -> str:
        return self._environment

    @property
    def launch_mode(self) -> str:
        return self._launch_mode

    @property
    def launch_modes(self) -> tuple[str, ...]:
        return self._launch_modes

    @property
    def robot_name(self) -> str:
        return self._profile.robot_name

    @property
    def lift_config(self) -> LiftControlConfig:
        return self._config.lift

    @property
    def vr_ready_countdown_seconds(self) -> int:
        return self._config.vr_ros_prepare_countdown_seconds

    @property
    def requires_ros_for_non_vr(self) -> bool:
        return self._profile.ros_required

    @property
    def started_at(self) -> Optional[datetime]:
        return self._started_at

    @property
    def display_title(self) -> str:
        return f"Percept Edge Runtime Console - {self._profile.display_name}"

    def is_running(self) -> bool:
        return self._pm.any_alive()

    def is_busy(self) -> bool:
        return self.is_running() or (self._runner is not None and not self._runner.is_stopped())

    @property
    def uses_managed_vr_ros(self) -> bool:
        return self._requires_vr_ros()

    def set_vr_ready_confirmation_handler(self, handler: Callable[[int], bool]) -> None:
        self._vr_ready_confirmation_handler = handler

    # ---- 控制 ----

    def set_environment(self, environment: str) -> None:
        if self.is_busy():
            return
        self._discard_runner()
        self._environment = environment
        self._rebuild_runtime_plan()
        self._emit_snapshot()

    def set_launch_mode(self, launch_mode: str) -> None:
        if self.is_busy() or launch_mode == self._launch_mode:
            return
        normalized = _normalize_launch_mode(launch_mode, self._launch_modes)
        if normalized == self._launch_mode:
            return
        self._discard_runner()
        self._launch_mode = normalized
        self._rebuild_runtime_plan()
        self._emit_snapshot()

    def start_runtime(self) -> None:
        if self.is_busy():
            return
        if not self._validate_preconditions():
            return

        self._discard_runner()
        self._state_machine.reset(self._environment)
        self._started_at = datetime.now()
        self._finalized = False
        self._state_machine.transition(
            RuntimeState.STARTING,
            message="正在按顺序启动运行时",
            global_status="启动中",
        )
        self._emit_log(
            "RUNTIME",
            "INFO",
            f"开始启动，环境={self._environment}，启动方式={_launch_mode_label(self._launch_mode)}",
        )
        self._emit_snapshot()

        ctx = BuildContext(
            repo_root=self._repo_root,
            robot_name=self._config.robot_name,
            uv_bin=self._config.uv_bin,
            extra={
                "config": self._config,
                "vr_ready_confirmation": self._confirm_vr_ready,
            },
        )
        self._runner = SequentialFlowRunner(
            steps=self._steps,
            build_ctx=ctx,
            adapters=self._adapters_map,
            process_manager=self._pm,
            health_checker=self._hc,
            env_extra=self._env_for_processes(),
            parent=self,
        )
        self._runner.start(self._on_flow_event)

    def stop_runtime(self, emergency: bool = False) -> None:
        if not self.is_busy():
            return

        if not emergency:
            self._upload_stop_wait_started_at = time.monotonic()
            QTimer.singleShot(0, self._check_uploads_then_stop)
            return

        self._do_stop_runtime(emergency=True)

    def _check_uploads_then_stop(self) -> None:
        api_url = f"http://127.0.0.1:{self._config.server_port}/api/upload/active"
        try:
            with urlopen(api_url, timeout=1.0) as resp:
                data = json.loads(resp.read())
                if data.get("code") == 200:
                    records = data.get("data", {}).get("records", [])
                    if records:
                        elapsed = time.monotonic() - (
                            self._upload_stop_wait_started_at or time.monotonic()
                        )
                        if elapsed >= self._UPLOAD_STOP_MAX_WAIT_SECONDS:
                            self._emit_log(
                                "RUNTIME",
                                "WARN",
                                "等待上传超过 300 秒，继续停止运行时",
                            )
                            self._do_stop_runtime(emergency=False)
                            return

                        count = len(records)
                        self._emit_log(
                            "RUNTIME",
                            "WARN",
                            f"检测到 {count} 个上传任务正在运行，等待完成后停止",
                        )
                        QTimer.singleShot(5000, self._check_uploads_then_stop)
                        return
        except Exception:
            pass

        self._do_stop_runtime(emergency=False)

    def _do_stop_runtime(self, emergency: bool = False) -> None:
        self._upload_stop_wait_started_at = None
        self._health_timer.stop()
        self._state_machine.transition(
            RuntimeState.STOPPING,
            message="正在停止运行时",
            global_status="停止中",
        )
        self._state_machine.mark_all_active_stopping()
        self._emit_log("RUNTIME", "WARN" if emergency else "INFO", "收到停止请求")
        if self._runner is not None:
            self._runner.stop()
        if emergency:
            self._pm.stop_all(emergency=True)
        self._stop_nginx_service()
        grace = int(self._shutdown_sequence_grace() * 1000)
        QTimer.singleShot(grace, self._force_kill_remaining)
        QTimer.singleShot(grace + 300, self._finalize_stop)
        self._emit_snapshot()

    def restart_runtime(self) -> None:
        if self.is_busy():
            self._restart_pending = True
            self.stop_runtime(False)
            return
        self.start_runtime()

    def reset_ui_state(self) -> None:
        if self.is_busy():
            return
        self._started_at = None
        self._state_machine.reset(self._environment)
        self._emit_snapshot()

    def cleanup_for_exit(self) -> None:
        """Qt 退出或未捕获异常时的同步兜底清理。"""
        self._health_timer.stop()
        if self._runner is not None and not self._runner.is_stopped():
            self._runner.stop()
        self._pm.cleanup_for_exit(
            timeout=max(self._config.process_shutdown_grace, self._config.ros_shutdown_grace)
        )

    # ---- FlowEvent 桥接 ----

    def _on_flow_event(self, event: FlowEvent) -> None:
        stage = event.stage
        status = event.status
        if stage == "flow":
            if status is StageStatus.RUNNING:
                self._transition_to_running()
            elif status is StageStatus.STOPPING:
                pass
            return
        if stage not in self._state_machine.stages:
            return
        if status is StageStatus.STARTING:
            self._state_machine.update_stage(stage, StageStatus.STARTING, summary="进行中")
            self._maybe_waiting_state(stage, event)
        elif status is StageStatus.RUNNING:
            pid_val = None
            if event.payload is not None:
                raw_pid = event.payload.get("pid")
                if raw_pid is not None:
                    pid_val = str(raw_pid)
            self._state_machine.update_stage(
                stage,
                StageStatus.RUNNING,
                summary="运行中",
                pid=pid_val,
            )
        elif status is StageStatus.FAILED:
            self._state_machine.fail_stage(stage, event.message or "失败")
            self._emit_log("RUNTIME", "ERROR", f"{stage}: {event.message}")
            self._schedule_finalize_after_fail()
        self._emit_snapshot()

    def _schedule_finalize_after_fail(self) -> None:
        """Flow 内部 _fail 只把进程停了，facade 需要自己收敛：停健康轮询 + 兜底强杀 + 触发 finalize。"""
        self._health_timer.stop()
        grace = int(self._shutdown_sequence_grace() * 1000)
        QTimer.singleShot(grace, self._force_kill_remaining)
        QTimer.singleShot(grace + 300, self._finalize_stop)

    def _maybe_waiting_state(self, stage: str, event: FlowEvent) -> None:
        """若当前 Step 在 extra 中声明了 waiting_state，进入对应全局状态。"""
        del stage
        step = self._safe_step(event.step_index)
        if step is None or step.kind.value != "wait_health":
            return
        raw = (step.extra or {}).get("waiting_state")
        if not isinstance(raw, str):
            return
        try:
            target = RuntimeState(raw)
        except ValueError:
            return
        message = _WAITING_STATE_MESSAGES.get(target, f"等待 {step.stage_label or step.adapter}")
        self._state_machine.transition(target, message=message, global_status="启动中")

    def _safe_step(self, index: int) -> Optional[Step]:
        if 0 <= index < len(self._steps):
            return self._steps[index]
        return None

    def _transition_to_running(self) -> None:
        self._state_machine.transition(
            RuntimeState.RUNNING,
            message="运行时已全部启动",
            global_status="运行中",
        )
        self._health_collector = RuntimeHealthCollector(self._config.api_url, parent=self)
        self._health_collector.health_ready.connect(self._on_health_ready)
        self._health_timer.start()
        self._refresh_health()
        self._emit_snapshot()

    # ---- ProcessManager 桥接 ----

    def _on_process_line(self, name: str, _stream: str, text: str) -> None:
        source = self._source_label(name)
        level = "WARN" if _stream == "stderr" else "INFO"
        self._emit_log(source, level, text)

    def _on_process_started(self, name: str, pid: int) -> None:
        source = self._source_label(name)
        self._emit_log(source, "INFO", f"进程已启动，PID={pid}")

    def _on_process_finished(self, name: str, exit_code: int, expected_stop: bool = False) -> None:
        source = self._source_label(name)
        suffix = "（主动停止）" if expected_stop else ""
        self._emit_log(source, "INFO", f"进程已停止，exit_code={exit_code}{suffix}")

    def _on_process_error(self, name: str, error_name: str, expected_stop: bool = False) -> None:
        if expected_stop and error_name == "Crashed":
            return
        source = self._source_label(name)
        self._emit_log(source, "ERROR", f"进程错误: {error_name}")

    def _source_label(self, name: str) -> str:
        return self._label_map.get(name, name.upper())

    # ---- 停止收敛 ----

    _STOP_OR_ERROR = frozenset({RuntimeState.STOPPING, RuntimeState.ERROR})

    def _force_kill_remaining(self) -> None:
        if self._state_machine.runtime_state not in self._STOP_OR_ERROR:
            return
        self._pm.force_kill_all()

    def _shutdown_sequence_grace(self) -> float:
        """等待最长优雅停止序列完成，再进入统一强杀兜底。"""
        ros_sequence_grace = self._config.ros_shutdown_grace + self._config.process_shutdown_grace
        managed_sequence_grace = self._config.process_shutdown_grace * 2
        return max(ros_sequence_grace, managed_sequence_grace) + self._config.force_kill_grace

    def _stop_nginx_service(self) -> None:
        spec = ProcessSpec(
            name="stop_nginx",
            cmd="sudo -n systemctl stop nginx",
            cwd=str(self._repo_root),
            shell=True,
            shutdown_grace=1.0,
            force_kill_grace=1.0,
        )
        try:
            self._pm.spawn(spec, env_extra=self._env_for_processes())
        except Exception as exc:
            self._emit_log("PERCEPT", "WARN", f"nginx 停止命令启动失败: {exc}")

    def _finalize_stop(self) -> None:
        if self._pm.any_alive():
            QTimer.singleShot(500, self._finalize_stop)
            return
        if self._state_machine.runtime_state not in self._STOP_OR_ERROR:
            return
        if self._finalized:
            return
        self._finalized = True
        restart = self._restart_pending
        self._restart_pending = False
        self._state_machine.mark_all_stopped()
        if self._state_machine.runtime_state == RuntimeState.STOPPING:
            self._state_machine.transition(
                RuntimeState.TERMINATED, message="运行时已停止", global_status="已停止"
            )
        self._started_at = None
        self._discard_runner()
        self._discard_health_collector()
        self._emit_log("RUNTIME", "INFO", "运行时已完成停止")
        self._emit_snapshot()
        if restart:
            QTimer.singleShot(0, self.start_runtime)

    def _discard_health_collector(self) -> None:
        collector = self._health_collector
        if collector is None:
            return
        try:
            collector.health_ready.disconnect(self._on_health_ready)
        except (RuntimeError, TypeError):
            pass
        collector.deleteLater()
        self._health_collector = None

    def _discard_runner(self) -> None:
        runner = self._runner
        if runner is None:
            return
        if not runner.is_stopped():
            runner.stop()
        runner.deleteLater()
        self._runner = None

    # ---- 健康采集 ----

    def _refresh_health(self) -> None:
        if self._state_machine.runtime_state != RuntimeState.RUNNING:
            return
        if self._health_collector is None:
            return
        self._health_gen += 1
        self._health_collector.collect_async(self._health_gen)

    def _on_health_ready(self, generation: int, health: object) -> None:
        if generation != self._health_gen:
            return
        from desktop.models.runtime_health import RuntimeHealth

        if not isinstance(health, RuntimeHealth):
            return
        self._state_machine.update_health(health)
        self._emit_snapshot()

    # ---- 前置校验 ----

    def _validate_preconditions(self) -> bool:
        cfg = self._config
        checks: list[tuple[bool, str]] = []
        if self._requires_ros():
            checks.extend(
                [
                    (Path(cfg.ros_env_cwd).is_dir(), f"ROS_ENV_CWD 不存在: {cfg.ros_env_cwd}"),
                    (
                        Path(cfg.ros_setup_script).is_file(),
                        f"ROS setup 脚本不存在: {cfg.ros_setup_script}",
                    ),
                ]
            )
            checks.extend(_profile_required_path_checks(self._profile))
            stale_ros = _find_stale_ros_processes()
            if stale_ros:
                detail = "; ".join(stale_ros[:5])
                if len(stale_ros) > 5:
                    detail = f"{detail}; ..."
                checks.append(
                    (
                        False,
                        "检测到未由当前桌面端托管的 ROS 残留进程，已拒绝启动以保护机械臂安全: "
                        f"{detail}",
                    )
                )
        if self._requires_vr_ros():
            checks.extend(
                [
                    (Path(cfg.ros_env_cwd).is_dir(), f"ROS_ENV_CWD 不存在: {cfg.ros_env_cwd}"),
                    (
                        Path(cfg.ros_setup_script).is_file(),
                        f"ROS setup 脚本不存在: {cfg.ros_setup_script}",
                    ),
                    (
                        Path(cfg.vr_ros_arm_cwd).is_dir(),
                        f"VR_ROS_ARM_CWD 不存在: {cfg.vr_ros_arm_cwd}",
                    ),
                    (
                        Path(cfg.vr_ros_arm_setup_script).is_file(),
                        f"VR ROS arm setup 脚本不存在: {cfg.vr_ros_arm_setup_script}",
                    ),
                    (
                        Path(cfg.vr_ros_serial_cwd).is_dir(),
                        f"VR_ROS_SERIAL_CWD 不存在: {cfg.vr_ros_serial_cwd}",
                    ),
                    (
                        Path(cfg.vr_ros_serial_setup_script).is_file(),
                        f"VR ROS serial setup 脚本不存在: {cfg.vr_ros_serial_setup_script}",
                    ),
                    (bool(cfg.vr_ros_arm_cmd), "VR_ROS_ARM_CMD 不能为空"),
                    (bool(cfg.vr_ros_serial_cmd), "VR_ROS_SERIAL_CMD 不能为空"),
                ]
            )
            stale_ros = _find_stale_ros_processes()
            if stale_ros:
                detail = "; ".join(stale_ros[:5])
                if len(stale_ros) > 5:
                    detail = f"{detail}; ..."
                checks.append(
                    (
                        False,
                        "检测到未由当前桌面端托管的 ROS 残留进程，已拒绝启动以保护机械臂安全: "
                        f"{detail}",
                    )
                )
        checks.extend(
            [
                (Path(cfg.api_cwd).is_dir(), f"API_CWD 不存在: {cfg.api_cwd}"),
                (Path(cfg.robot_os_cwd).is_dir(), f"ROBOT_OS_CWD 不存在: {cfg.robot_os_cwd}"),
                (
                    (self._repo_root / "scripts" / "debug" / "wait_robot_os_ready.py").is_file(),
                    "缺少 wait_robot_os_ready.py",
                ),
            ]
        )
        for passed, message in checks:
            if passed:
                continue
            self._state_machine.transition(
                RuntimeState.ERROR, message=message, global_status="错误"
            )
            self._emit_log("RUNTIME", "ERROR", message)
            self._emit_snapshot()
            return False
        return True

    # ---- 辅助 ----

    def _env_for_processes(self) -> dict[str, str]:
        return {
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PERCEPT_ENV": self._environment,
            "APP_ENV": self._environment,
            "PERCEPT_ROBOT": self._config.robot_name,
            "APP_ROBOT": self._config.robot_name,
            "PERCEPT_LAUNCH_MODE": self._launch_mode,
        }

    def _load_config(self, environment: str) -> RuntimeConfig:
        return replace(
            RuntimeConfig.load(
                self._repo_root,
                environment=environment,
                robot_name=self._profile.robot_name,
            ),
            launch_mode=self._launch_mode,
        )

    def _rebuild_runtime_plan(self) -> None:
        self._config = self._load_config(self._environment)
        self._steps = tuple(self._profile.flow_factory(self._profile, self._config))
        self._label_map = _build_label_map(self._profile.adapters, self._steps)
        initial_stages = _build_initial_stages(self._steps)
        self._state_machine = RuntimeStateMachine(
            environment=self._environment, initial_stages=initial_stages
        )

    def _requires_ros(self) -> bool:
        return self._profile.ros_required and self._launch_mode != "vr"

    def _requires_vr_ros(self) -> bool:
        return (
            self._launch_mode == "vr"
            and self._config.vr_ros_enabled
            and _profile_supports_vr_ros(self._profile)
        )

    def _confirm_vr_ready(self, _ctx: object) -> bool:
        countdown = self._config.vr_ros_prepare_countdown_seconds
        self._emit_log("VR_ROS", "WARN", f"等待用户确认 VR 准备完成，倒计时={countdown}s")
        if self._vr_ready_confirmation_handler is None:
            self._emit_log("VR_ROS", "ERROR", "未注册 VR 准备确认弹窗处理器")
            return False
        return self._vr_ready_confirmation_handler(countdown)

    def _emit_log(self, source: str, level: str, message: str) -> None:
        entry = LogEntry(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            source=source,
            level=level,
            message=message,
        )
        self.log_emitted.emit(entry)

    def _emit_snapshot(self) -> None:
        snapshot = self._state_machine.snapshot(
            running=self.is_running(),
            state_file=str(self._state_store.state_file),
        )
        self._state_store.write_snapshot(snapshot)
        self.snapshot_changed.emit(snapshot)


# ---- 工具 ----


def _build_label_map(adapters: Sequence[Adapter], steps: tuple[Step, ...]) -> dict[str, str]:
    """根据 adapter.log_label + SHELL_ONCE step 的 extra['log_label'] 组装日志标签映射。"""
    label_map: dict[str, str] = {}
    for adapter in adapters:
        label_map[adapter.name] = getattr(adapter, "log_label", adapter.name.upper())
    for step in steps:
        extra = step.extra or {}
        name = extra.get("name")
        if isinstance(name, str) and name:
            label = extra.get("log_label")
            label_map[name] = label if isinstance(label, str) and label else name.upper()
    return label_map


def _build_initial_stages(steps: tuple[Step, ...]) -> list[StageState]:
    """从 Step 列表中抽取唯一 stage_label 并构造初始 StageState（保留顺序）。"""
    seen: list[str] = []
    for step in steps:
        label = step.stage_label
        if label and label not in seen:
            seen.append(label)
    stages: list[StageState] = []
    prev: Optional[str] = None
    for label in seen:
        stages.append(StageState(name=label, dependency=prev, summary="等待启动"))
        prev = label
    return stages


def _profile_required_path_checks(profile: RobotProfile) -> list[tuple[bool, str]]:
    """从 profile.extra['required_paths'] 读取额外文件/目录前置校验。"""
    raw_items = profile.extra.get("required_paths", ())
    checks: list[tuple[bool, str]] = []
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        return checks

    for raw_item in raw_items:
        if not isinstance(raw_item, Sequence) or isinstance(raw_item, (str, bytes)):
            continue
        if len(raw_item) != 3:
            continue
        kind, path_value, message = raw_item
        if (
            not isinstance(kind, str)
            or not isinstance(path_value, str)
            or not isinstance(message, str)
        ):
            continue
        path = Path(path_value)
        if kind == "dir":
            checks.append((path.is_dir(), message))
        elif kind == "file":
            checks.append((path.is_file(), message))
    return checks


def _profile_supports_vr_ros(profile: RobotProfile) -> bool:
    adapter_names = {adapter.name for adapter in profile.adapters}
    return {"vr_ros_arm", "vr_ros_serial"}.issubset(adapter_names)


def _find_stale_ros_processes() -> list[str]:
    """启动 CR 主动臂链路前拦截疑似残留 ROS 进程，避免新旧控制节点并存。"""
    current_pid = os.getpid()
    stale: list[str] = []
    for proc in psutil.process_iter(attrs=["pid", "cmdline", "name"]):
        try:
            pid = int(proc.info.get("pid") or 0)
            if pid <= 0 or pid == current_pid:
                continue
            raw_cmdline = proc.info.get("cmdline")
            if isinstance(raw_cmdline, list):
                cmdline = " ".join(str(part) for part in raw_cmdline)
            else:
                cmdline = ""
            raw_name = proc.info.get("name")
            name = str(raw_name or "")
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, ValueError):
            continue

        marker = f"{name} {cmdline}"
        if _is_ros_stale_process(marker):
            stale.append(f"PID={pid} {cmdline or name}".strip())
    return stale


def _is_ros_stale_process(marker: str) -> bool:
    return any(
        pattern in marker
        for pattern in (
            "roscore",
            "roslaunch arm_control L5.launch",
            "roslaunch arx_x5_controller open_remote_slave.launch",
            "roslaunch arx_x5_controller open_remote_master.launch",
            "roslaunch arx_x5_controller open_vr_double_arm.launch",
            "rosrun serial_port serial_port",
        )
    )


def _normalize_launch_mode(raw: str | None, supported: Sequence[str]) -> str:
    if not supported:
        return "bilateral"
    if raw in supported:
        return str(raw)
    return str(supported[0])


def _launch_mode_label(launch_mode: str) -> str:
    return {
        "bilateral": "同构臂",
        "vr": "VR",
    }.get(launch_mode, launch_mode)


_WAITING_STATE_MESSAGES: dict[RuntimeState, str] = {
    RuntimeState.WAITING_ROBOT_READY: "等待 Robot OS 就绪",
    RuntimeState.WAITING_API_READY: "等待 API startup gate",
}
