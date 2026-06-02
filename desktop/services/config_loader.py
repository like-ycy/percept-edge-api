"""运行时配置加载器。

从环境变量 + TOML 配置（src.config）解析运行时所需的命令、路径、超时。
机型差异通过 PERCEPT_ROBOT / 环境变量注入。
"""

from __future__ import annotations

import logging
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import DesktopRuntimeSettings, Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeRosCommand:
    name: str
    log_label: str
    cwd: str
    setup_script: str
    cmd: str
    path_prefix: str = ""


def _env_or_default(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw


def _read_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in {None, ""}:
        return default
    return float(str(raw))


def _read_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in {None, ""}:
        return default
    return int(str(raw))


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in {None, ""}:
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"环境变量 {name} 必须是布尔值，当前为: {raw}")


def _load_app_settings(environment: str, repo_root: Path, robot_name: str) -> "Settings":
    root_str = str(repo_root)
    if root_str not in sys.path:
        sys.path.append(root_str)

    from src.config import get_settings

    return get_settings(env_name=environment, robot_name=robot_name)


def _load_legacy_runtime_settings() -> "DesktopRuntimeSettings":
    from src.config import DesktopRuntimeSettings

    return DesktopRuntimeSettings()


def _replace_leading_uv_command(command: str, uv_bin: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return command
    if not tokens or tokens[0] != "uv":
        return command
    tokens[0] = uv_bin
    return shlex.join(tokens)


def _resolve_command_endpoint(environment: str, repo_root: Path, robot_name: str) -> str:
    from_env = os.getenv("ROBOT_OS_COMMAND_ENDPOINT")
    if from_env:
        return from_env

    try:
        return _load_app_settings(environment, repo_root, robot_name).zeromq.command_endpoint
    except Exception:
        fallback = "ipc:///tmp/robotos_command"
        logger.warning(
            "无法从配置加载 command_endpoint (env=%s)，回退到默认值: %s",
            environment,
            fallback,
            exc_info=True,
        )
        return fallback


@dataclass
class RuntimeConfig:
    repo_root: Path
    robot_name: str
    uv_bin: str
    ros_env_cwd: str
    ros_setup_script: str
    roscore_cmd: str
    ros_slave_cmd: str
    ros_master_cmd: str
    ros_commands: tuple[RuntimeRosCommand, ...]
    robot_os_cwd: str
    robot_os_cmd: str
    api_cwd: str
    api_cmd: str
    api_startup_timeout: float
    api_gate_interval: float
    status_poll_interval: float
    runtime_health_interval: float
    ros_startup_grace: float
    roscore_startup_grace: float
    process_shutdown_grace: float
    ros_shutdown_grace: float
    sigterm_shutdown_grace: float
    api_shutdown_grace: float
    force_kill_grace: float
    ready_timeout: float
    monitor_timeout: float
    probe_interval: float
    command_endpoint: str
    server_port: int
    vr_ros_enabled: bool = False
    vr_ros_prepare_countdown_seconds: int = 30
    vr_ros_arm_cwd: str = ""
    vr_ros_arm_setup_script: str = ""
    vr_ros_arm_cmd: str = ""
    vr_ros_serial_cwd: str = ""
    vr_ros_serial_setup_script: str = ""
    vr_ros_serial_cmd: str = ""
    # Lift control
    lift_enabled: bool = False
    lift_transport: str = "script"
    lift_api_path: str = "/api/desktop/lift/height"
    lift_python_bin: str = ""
    lift_script_path: str = ""
    lift_min_height: int = 0
    lift_max_height: int = 600
    lift_step: int = 100
    launch_mode: str = "bilateral"

    @classmethod
    def load(
        cls,
        repo_root: Path,
        environment: str = "test",
        robot_name: str | None = None,
    ) -> "RuntimeConfig":
        uv_bin = os.getenv("UV_BIN", "uv")
        effective_robot_name = robot_name or os.getenv(
            "PERCEPT_ROBOT", os.getenv("APP_ROBOT", "robot-cr4c")
        )
        app_settings = _load_app_settings(environment, repo_root, effective_robot_name)
        runtime = app_settings.desktop.runtime
        if os.getenv("PERCEPT_DESKTOP_LEGACY_RUNTIME_FALLBACK") == "1":
            logger.warning(
                "PERCEPT_DESKTOP_LEGACY_RUNTIME_FALLBACK=1，使用 legacy desktop runtime 默认值 "
                "(env=%s, robot=%s)",
                environment,
                effective_robot_name,
            )
            runtime = _load_legacy_runtime_settings()
        command_endpoint = _resolve_command_endpoint(environment, repo_root, effective_robot_name)
        server_port = _read_int_env("SERVER__PORT", app_settings.server.port)

        uv_bin = _env_or_default("UV_BIN", runtime.uv_bin)
        api_cmd_default = runtime.api_cmd
        if os.getenv("UV_BIN") and "uv" in api_cmd_default:
            api_cmd_default = _replace_leading_uv_command(api_cmd_default, uv_bin)

        # Lift control configuration
        lift_section = getattr(app_settings.desktop, "lift", None)
        lift_enabled = False
        lift_transport = "script"
        lift_api_path = "/api/desktop/lift/height"
        lift_python_bin = ""
        lift_script_path = ""
        lift_min_height = 0
        lift_max_height = 600
        lift_step = 100
        if lift_section is not None:
            lift_enabled = bool(getattr(lift_section, "enabled", False))
            lift_transport = str(getattr(lift_section, "transport", "script"))
            lift_api_path = str(getattr(lift_section, "api_path", "/api/desktop/lift/height"))
            lift_python_bin = str(getattr(lift_section, "python_bin", ""))
            lift_script_path = str(getattr(lift_section, "script_path", ""))
            lift_min_height = int(getattr(lift_section, "min_height", 0))
            lift_max_height = int(getattr(lift_section, "max_height", 600))
            lift_step = int(getattr(lift_section, "step", 100))

        return cls(
            repo_root=repo_root,
            robot_name=effective_robot_name,
            uv_bin=uv_bin,
            ros_env_cwd=_env_or_default("ROS_ENV_CWD", runtime.ros_env_cwd),
            ros_setup_script=_env_or_default("ROS_SETUP_SCRIPT", runtime.ros_setup_script),
            roscore_cmd=_env_or_default("ROSCORE_CMD", runtime.roscore_cmd),
            ros_slave_cmd=_env_or_default("ROS_SLAVE_CMD", runtime.ros_slave_cmd),
            ros_master_cmd=_env_or_default("ROS_MASTER_CMD", runtime.ros_master_cmd),
            ros_commands=tuple(
                RuntimeRosCommand(
                    name=command.name,
                    log_label=command.log_label,
                    cwd=command.cwd,
                    setup_script=command.setup_script,
                    cmd=command.cmd,
                    path_prefix=command.path_prefix,
                )
                for command in runtime.ros_commands
            ),
            robot_os_cwd=_env_or_default("ROBOT_OS_CWD", runtime.robot_os_cwd),
            robot_os_cmd=_env_or_default("ROBOT_OS_CMD", runtime.robot_os_cmd),
            api_cwd=_env_or_default("API_CWD", runtime.api_cwd or str(repo_root)),
            api_cmd=_env_or_default("API_CMD", api_cmd_default),
            api_startup_timeout=_read_float_env("API_STARTUP_TIMEOUT", runtime.api_startup_timeout),
            api_gate_interval=_read_float_env("API_GATE_INTERVAL", runtime.api_gate_interval),
            status_poll_interval=_read_float_env(
                "STATUS_POLL_INTERVAL", runtime.status_poll_interval
            ),
            runtime_health_interval=_read_float_env(
                "RUNTIME_HEALTH_INTERVAL", runtime.runtime_health_interval
            ),
            ros_startup_grace=_read_float_env("ROS_STARTUP_GRACE", runtime.ros_startup_grace),
            roscore_startup_grace=_read_float_env(
                "ROSCORE_STARTUP_GRACE", runtime.roscore_startup_grace
            ),
            process_shutdown_grace=_read_float_env(
                "PROCESS_SHUTDOWN_GRACE", runtime.process_shutdown_grace
            ),
            ros_shutdown_grace=_read_float_env("ROS_SHUTDOWN_GRACE", runtime.ros_shutdown_grace),
            sigterm_shutdown_grace=_read_float_env(
                "SIGTERM_SHUTDOWN_GRACE", runtime.sigterm_shutdown_grace
            ),
            api_shutdown_grace=_read_float_env("API_SHUTDOWN_GRACE", runtime.api_shutdown_grace),
            force_kill_grace=_read_float_env("FORCE_KILL_GRACE", runtime.force_kill_grace),
            ready_timeout=_read_float_env("ROBOT_OS_READY_TIMEOUT", runtime.ready_timeout),
            monitor_timeout=_read_float_env("ROBOT_OS_MONITOR_TIMEOUT", runtime.monitor_timeout),
            probe_interval=_read_float_env("ROBOT_OS_PROBE_INTERVAL", runtime.probe_interval),
            command_endpoint=command_endpoint,
            server_port=server_port,
            vr_ros_enabled=_read_bool_env("VR_ROS_ENABLED", runtime.vr_ros_enabled),
            vr_ros_prepare_countdown_seconds=_read_int_env(
                "VR_ROS_PREPARE_COUNTDOWN_SECONDS",
                runtime.vr_ros_prepare_countdown_seconds,
            ),
            vr_ros_arm_cwd=_env_or_default("VR_ROS_ARM_CWD", runtime.vr_ros_arm_cwd),
            vr_ros_arm_setup_script=_env_or_default(
                "VR_ROS_ARM_SETUP_SCRIPT",
                runtime.vr_ros_arm_setup_script,
            ),
            vr_ros_arm_cmd=_env_or_default("VR_ROS_ARM_CMD", runtime.vr_ros_arm_cmd),
            vr_ros_serial_cwd=_env_or_default(
                "VR_ROS_SERIAL_CWD",
                runtime.vr_ros_serial_cwd,
            ),
            vr_ros_serial_setup_script=_env_or_default(
                "VR_ROS_SERIAL_SETUP_SCRIPT",
                runtime.vr_ros_serial_setup_script,
            ),
            vr_ros_serial_cmd=_env_or_default(
                "VR_ROS_SERIAL_CMD",
                runtime.vr_ros_serial_cmd,
            ),
            launch_mode=os.getenv("PERCEPT_LAUNCH_MODE", "bilateral"),
            lift_enabled=lift_enabled,
            lift_transport=lift_transport,
            lift_api_path=lift_api_path,
            lift_python_bin=lift_python_bin,
            lift_script_path=lift_script_path,
            lift_min_height=lift_min_height,
            lift_max_height=lift_max_height,
            lift_step=lift_step,
        )

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/"
