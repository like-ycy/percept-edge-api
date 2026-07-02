"""Robot OS 封装：启动 robot_os pyz，提供 ZeroMQ command monitor 就绪探测。"""

from __future__ import annotations

import shlex
import signal as _signal
from typing import Optional

from desktop.adapters._common import get_config
from desktop.adapters.base import Adapter, BuildContext, HealthProbe, ProcessSpec, ShutdownStep


def _robot_os_mode(robot_name: str, launch_mode: str) -> Optional[str]:
    if robot_name == "robot-w1":
        return "vr"
    if robot_name in {"robot-cr1", "robot-cr5"}:
        return "homologous"
    if robot_name in {"robot-cr4a", "robot-cr4c"}:
        return "vr" if launch_mode == "vr" else "homologous"
    return None


def _replace_mode(cmd: str, robot_name: str, launch_mode: str) -> str:
    """根据 desktop launch_mode 替换 ontology-core runtime 的 --mode 值。"""
    tokens = shlex.split(cmd)
    if not tokens:
        return cmd

    target_mode = _robot_os_mode(robot_name, launch_mode)
    if target_mode is None:
        return cmd

    normalized: list[str] = []
    skip_next = False
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if token == "--mode":
            skip_next = index + 1 < len(tokens)
            continue
        if token.startswith("--mode="):
            continue
        normalized.append(token)

    normalized.append(f"--mode={target_mode}")
    return shlex.join(normalized)


class RobotOsAdapter(Adapter):
    """Robot OS 进程 + ZMQ command ready 探测。

    参数：
        name: ProcessSpec / adapter 名称，默认 "robot_os"
        source_ros: 是否 source ROS setup 脚本（让子进程拿到 rospy / ROS 消息包的
                    PYTHONPATH）。这只注入环境，不代表 desktop 托管 ROS 节点。
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
        source_prefix = ""
        if self._source_ros:
            setup = self._ros_setup_override or cfg.ros_setup_script
            source_prefix = f"[ -f '{setup}' ] && source '{setup}'; "
        robot_cmd = _replace_mode(cfg.robot_os_cmd, cfg.robot_name, cfg.launch_mode)
        return ProcessSpec(
            name=self.name,
            cmd=f"{source_prefix}exec {robot_cmd}",
            cwd=cfg.robot_os_cwd,
            shell=True,
            shutdown_signal=int(_signal.SIGINT),
            shutdown_grace=cfg.process_shutdown_grace,
            force_kill_grace=cfg.force_kill_grace,
            shutdown_sequence=(
                ShutdownStep(signal=int(_signal.SIGINT), grace=cfg.ros_shutdown_grace),
                ShutdownStep(signal=int(_signal.SIGTERM), grace=cfg.sigterm_shutdown_grace),
                ShutdownStep(signal=int(_signal.SIGKILL), grace=cfg.force_kill_grace),
            ),
        )

    def build_probe(self, ctx: BuildContext) -> Optional[HealthProbe]:
        cfg = get_config(ctx)
        return HealthProbe(
            kind="zmq_monitor",
            target=cfg.command_endpoint,
            timeout=cfg.monitor_timeout,
            interval=cfg.probe_interval,
            deadline=cfg.ready_timeout,
        )
