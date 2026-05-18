"""Robot OS 封装：启动 robot_os pyz，提供 ZeroMQ monitor 就绪探测。"""

from __future__ import annotations

import signal as _signal
import shlex
from typing import Optional

from desktop.adapters._common import get_config
from desktop.adapters.base import Adapter, BuildContext, HealthProbe, ProcessSpec, ShutdownStep


class RobotOsAdapter(Adapter):
    """Robot OS 进程 + ZMQ ready 探测。

    参数：
        name: ProcessSpec / adapter 名称，默认 "robot_os"
        source_ros: 是否 source ROS setup 脚本（让子进程拿到 rospy 的 PYTHONPATH）。
                    CR 系列机型需要设 True；W1 等无 ROS 机型应设 False。
        ros_setup_override: 显式指定 setup 脚本路径；None 则使用
                            RuntimeConfig.ros_setup_script。仅在 source_ros=True 时生效。
                            拼接前会做 `[ -f ... ]` 防御，路径不存在时自动跳过 source。
    """

    def __init__(
        self,
        name: str = "robot_os",
        *,
        source_ros: bool = True,
        ros_setup_override: Optional[str] = None,
    ) -> None:
        self.name: str = name
        self.log_label: str = "ROBOT_OS"
        self._source_ros = source_ros
        self._ros_setup_override = ros_setup_override

    def build_spec(self, ctx: BuildContext) -> ProcessSpec:
        cfg = get_config(ctx)
        robot_os_cmd = _normalize_robot_os_cmd(
            cfg.robot_os_cmd,
            robot_name=cfg.robot_name,
            launch_mode=cfg.launch_mode,
        )
        source_prefix = ""
        if self._source_ros:
            setup = self._ros_setup_override or cfg.ros_setup_script
            source_prefix = f"[ -f '{setup}' ] && source '{setup}'; "
        return ProcessSpec(
            name=self.name,
            cmd=f"{source_prefix}exec {robot_os_cmd}",
            cwd=cfg.robot_os_cwd,
            shell=True,
            shutdown_signal=int(_signal.SIGINT),
            shutdown_grace=cfg.process_shutdown_grace,
            shutdown_sequence=(
                ShutdownStep(signal=int(_signal.SIGINT), grace=cfg.process_shutdown_grace),
                ShutdownStep(signal=int(_signal.SIGTERM), grace=cfg.process_shutdown_grace),
            ),
            force_kill_grace=cfg.force_kill_grace,
        )

    def build_probe(self, ctx: BuildContext) -> Optional[HealthProbe]:
        cfg = get_config(ctx)
        return HealthProbe(
            kind="zmq_monitor",
            target=cfg.rep_endpoint,
            timeout=cfg.monitor_timeout,
            interval=cfg.probe_interval,
            deadline=cfg.ready_timeout,
        )


def _normalize_robot_os_cmd(command: str, *, robot_name: str, launch_mode: str) -> str:
    run_mode = _robot_os_run_mode(robot_name, launch_mode)
    try:
        tokens = shlex.split(command)
    except ValueError:
        return command

    if run_mode is None:
        return shlex.join(_remove_run_mode_args(tokens))

    normalized: list[str] = []
    replaced = False
    skip_next = False
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if token == "--run-mode":
            if not replaced:
                normalized.append(f"--run-mode={run_mode}")
                replaced = True
            skip_next = index + 1 < len(tokens)
            continue
        if token.startswith("--run-mode="):
            if not replaced:
                normalized.append(f"--run-mode={run_mode}")
                replaced = True
            continue
        normalized.append(token)

    if not replaced:
        normalized.append(f"--run-mode={run_mode}")

    return shlex.join(normalized)


def _robot_os_run_mode(robot_name: str, launch_mode: str) -> str | None:
    if robot_name == "robot-w1":
        return None
    if robot_name == "robot-cr1":
        return "mode1"
    if robot_name in {"robot-cr4a", "robot-cr4c"}:
        return "mode2" if launch_mode == "vr" else "mode1"
    return None


def _remove_run_mode_args(tokens: list[str]) -> list[str]:
    normalized: list[str] = []
    skip_next = False
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if token == "--run-mode":
            skip_next = index + 1 < len(tokens)
            continue
        if token.startswith("--run-mode="):
            continue
        normalized.append(token)
    return normalized
