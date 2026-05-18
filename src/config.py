# src/config.py
"""配置管理模块"""

from __future__ import annotations

import os
import sys
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    InitSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# 项目根目录（config.py 在 src/ 下，上两级即为项目根目录）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "config"
_ROBOTS_CONFIG_DIR = _CONFIG_DIR / "robots"
_BASE_CONFIG_FILE = _CONFIG_DIR / "base.toml"
_ENV_NAME_ENV_VARS = ("PERCEPT_ENV", "APP_ENV")
_ROBOT_NAME_ENV_VARS = ("PERCEPT_ROBOT", "APP_ROBOT")
_ACTIVE_CONFIG_FILES: ContextVar[tuple[str, ...] | None] = ContextVar(
    "active_config_files",
    default=None,
)


def _read_toml_file(file_path: Path) -> dict[str, Any]:
    with file_path.open("rb") as toml_file:
        if sys.version_info < (3, 11):
            import tomli

            return tomli.load(toml_file)
        import tomllib

        return tomllib.load(toml_file)


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(current, value)
            continue
        merged[key] = value
    return merged


def _resolve_env_config_file(env_name: str) -> Path:
    normalized_env = env_name.strip().lower()
    if normalized_env not in {"test", "prod"}:
        msg = f"不支持的环境: {env_name}"
        raise ValueError(msg)
    return Path(f"{normalized_env}.toml")


def _normalize_robot_name(robot_name: str) -> str:
    normalized_robot = robot_name.strip().lower()
    if not normalized_robot:
        msg = "机器人名称不能为空"
        raise ValueError(msg)
    return normalized_robot


def _get_selected_env_name() -> str | None:
    for env_var in _ENV_NAME_ENV_VARS:
        value = os.getenv(env_var)
        if value:
            return value
    return None


def _get_selected_robot_name() -> str | None:
    for env_var in _ROBOT_NAME_ENV_VARS:
        value = os.getenv(env_var)
        if value:
            return value
    return None


def get_selected_env_name() -> str | None:
    selected_env = _get_selected_env_name()
    if not selected_env:
        return None
    return selected_env.strip().lower()


def get_selected_robot_name() -> str | None:
    selected_robot = _get_selected_robot_name()
    if not selected_robot:
        return None
    return _normalize_robot_name(selected_robot)


def _resolve_robot_config_file(robot_name: str, env_name: str) -> Path:
    normalized_robot = _normalize_robot_name(robot_name)
    env_config_file = _resolve_env_config_file(env_name)
    return (_ROBOTS_CONFIG_DIR / normalized_robot / env_config_file).resolve()


def _resolve_robot_base_config_file(robot_name: str) -> Path:
    normalized_robot = _normalize_robot_name(robot_name)
    return (_ROBOTS_CONFIG_DIR / normalized_robot / "base.toml").resolve()


def _resolve_config_files(
    env_name: str | None = None, robot_name: str | None = None
) -> tuple[Path, ...]:
    selected_env = env_name or _get_selected_env_name()
    if not selected_env:
        msg = "未指定运行环境，请通过 --env 参数或 PERCEPT_ENV/APP_ENV 环境变量指定"
        raise EnvironmentError(msg)

    selected_robot = robot_name or _get_selected_robot_name()
    if not selected_robot:
        msg = "未指定机器人，请通过 --robot 参数或 PERCEPT_ROBOT/APP_ROBOT 环境变量指定"
        raise EnvironmentError(msg)

    robot_base_file = _resolve_robot_base_config_file(selected_robot)
    selected_file = _resolve_robot_config_file(selected_robot, selected_env)
    if not selected_file.is_file():
        msg = f"配置文件不存在: {selected_file}"
        raise FileNotFoundError(msg)

    config_files: list[Path] = []
    if _BASE_CONFIG_FILE.is_file():
        config_files.append(_BASE_CONFIG_FILE)
    if robot_base_file.is_file():
        config_files.append(robot_base_file)
    config_files.append(selected_file)
    return tuple(config_files)


class MergedTomlConfigSettingsSource(InitSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings]):
        self.config_files = (
            tuple(Path(config_file) for config_file in (_ACTIVE_CONFIG_FILES.get() or ()))
            or _resolve_config_files()
        )
        merged_data: dict[str, Any] = {}
        for config_file in self.config_files:
            if not config_file.is_file():
                msg = f"配置文件不存在: {config_file}"
                raise FileNotFoundError(msg)
            merged_data = _deep_merge_dict(merged_data, _read_toml_file(config_file))
        super().__init__(settings_cls, merged_data)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(config_files={self.config_files!r})"


class ServerSettings(BaseModel):
    """服务器配置"""

    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False


class DatabaseSettings(BaseModel):
    """数据库配置"""

    path: str = "data/percept.db"


class CloudSettings(BaseModel):
    """云端服务配置"""

    base_url: str = "https://api.example.com"
    timeout: int = 30


class ZeromqSettings(BaseModel):
    """ZeroMQ 配置"""

    endpoint: str = "ipc:///tmp/robotos_collection"
    rep_endpoint: str = "ipc:///tmp/robotos_monitor"


class WebrtcPreviewSettings(BaseModel):
    """WebRTC 预览优化配置。"""

    enabled: bool = True
    max_width: int = Field(default=1920, gt=0, le=1920)
    max_height: int = Field(default=540, gt=0, le=1080)
    max_fps: float = Field(default=15.0, gt=0.0, le=30.0)
    fallback_width: int = Field(default=640, gt=0, le=1920)
    fallback_height: int = Field(default=360, gt=0, le=1080)
    max_encoded_frame_bytes: int = Field(default=16 * 1024 * 1024, gt=0, le=64 * 1024 * 1024)
    adaptive: "WebrtcAdaptiveSettings" = Field(default_factory=lambda: WebrtcAdaptiveSettings())


class WebrtcCodecSettings(BaseModel):
    """WebRTC 编码协商配置。"""

    enabled: bool = True
    preferred: list[str] = Field(default_factory=lambda: ["video/H264", "video/VP8"])


class WebrtcAdaptiveProfileSettings(BaseModel):
    """WebRTC 预览自适应档位。"""

    name: str
    max_width: int = Field(gt=0, le=1920)
    max_height: int = Field(gt=0, le=1080)
    max_fps: float = Field(gt=0.0, le=30.0)


class WebrtcAdaptiveSettings(BaseModel):
    """WebRTC 预览自适应降级配置。"""

    enabled: bool = False
    check_interval_seconds: float = Field(default=5.0, ge=0.0)
    cooldown_seconds: float = Field(default=15.0, ge=0.0)
    decode_ms_high: float = Field(default=30.0, ge=0.0)
    recv_ms_high: float = Field(default=50.0, ge=0.0)
    reuse_ratio_high: float = Field(default=0.5, ge=0.0, le=1.0)
    profiles: list[WebrtcAdaptiveProfileSettings] = Field(
        default_factory=lambda: [
            WebrtcAdaptiveProfileSettings(
                name="high", max_width=1920, max_height=540, max_fps=15.0
            ),
            WebrtcAdaptiveProfileSettings(
                name="medium", max_width=1280, max_height=360, max_fps=12.0
            ),
            WebrtcAdaptiveProfileSettings(name="low", max_width=960, max_height=270, max_fps=10.0),
        ]
    )


class WebrtcSettings(BaseModel):
    """WebRTC 配置"""

    stun_server: str = "stun:stun.l.google.com:19302"
    preview: WebrtcPreviewSettings = Field(default_factory=WebrtcPreviewSettings)
    codec: WebrtcCodecSettings = Field(default_factory=WebrtcCodecSettings)


class StorageSettings(BaseModel):
    """存储配置"""

    base_path: str = "data/collections"


class UploadSettings(BaseModel):
    """上传配置"""

    remote_user: str = ""
    remote_host: str = ""
    remote_port: int = 22
    remote_path: str = ""
    ssh_key_path: str = ""
    max_retries: int = 3
    # 上传完成通知配置
    notify_endpoint: str = "/data/upload"
    notify_timeout: int = 10
    notify_retries: int = 3


class IAMSettings(BaseModel):
    """IAM 服务配置"""

    verify_endpoint: str = "/auth/me"
    timeout: int = 10


class AuthSettings(BaseModel):
    """认证配置"""

    enabled: bool = True
    cache_ttl: int = 60
    whitelist_paths: list[str] = ["/health", "/docs", "/openapi.json", "/redoc"]
    iam: IAMSettings = Field(default_factory=IAMSettings)


class TaskSettings(BaseModel):
    """任务同步配置"""

    interval: int = 300  # 同步间隔（秒），默认 5 分钟
    detail_concurrency: int = 5  # 任务详情抓取并发度


class MonitorSettings(BaseModel):
    """Monitor 缓存刷新配置"""

    interval: int = 300  # monitor 缓存刷新间隔（秒）


class HeartbeatSettings(BaseModel):
    """心跳配置"""

    enabled: bool = True
    interval: int = 60


class CollectionSettings(BaseModel):
    """采集相关配置（启动期生效，修改后需重启服务）"""

    frame_drop_threshold: float = Field(default=0.10, ge=0.0, le=1.0)
    video_only: bool = False


class ProcessMonitorSettings(BaseModel):
    """进程监控配置"""

    enabled: bool = True
    sample_interval_seconds: float = Field(default=60.0, gt=0.0)
    refresh_window_seconds: float = Field(default=0.5, gt=0.0)
    cmdline_max_length: int = Field(default=200, gt=0)


class DesktopRuntimeSettings(BaseModel):
    """桌面端多进程运行时配置。"""

    uv_bin: str = "uv"
    ros_env_cwd: str = "/home/ai/workspaces/ros_env/X5_ws"
    ros_setup_script: str = "/home/ai/workspaces/ros_env/X5_ws/devel/setup.bash"
    roscore_cmd: str = "roscore"
    ros_slave_cmd: str = "roslaunch arx_x5_controller open_remote_slave.launch"
    ros_master_cmd: str = "roslaunch arx_x5_controller open_remote_master.launch"
    robot_os_cwd: str = "/home/ai/workspaces/percept-edge/ontology-core"
    robot_os_cmd: str = (
        "/usr/local/bin/python3.10 "
        "/home/ai/workspaces/percept-edge/ontology-core/robot_os.pyz core "
        "--run-mode mode1 --log-level INFO"
    )
    api_cwd: str = ""
    api_cmd: str = "uv run main.py"
    api_startup_timeout: float = Field(default=15.0, gt=0.0)
    api_gate_interval: float = Field(default=1.0, gt=0.0)
    status_poll_interval: float = Field(default=0.2, gt=0.0)
    runtime_health_interval: float = Field(default=300.0, gt=0.0)
    ros_startup_grace: float = Field(default=3.0, ge=0.0)
    roscore_startup_grace: float = Field(default=2.0, ge=0.0)
    process_shutdown_grace: float = Field(default=5.0, ge=0.0)
    ros_shutdown_grace: float = Field(default=10.0, ge=0.0)
    force_kill_grace: float = Field(default=2.0, ge=0.0)
    ready_timeout: float = Field(default=30.0, gt=0.0)
    monitor_timeout: float = Field(default=0.5, gt=0.0)
    probe_interval: float = Field(default=1.0, gt=0.0)
    vr_ros_enabled: bool = False
    vr_ros_prepare_countdown_seconds: int = Field(default=30, ge=0)
    vr_ros_arm_cwd: str = ""
    vr_ros_arm_setup_script: str = ""
    vr_ros_arm_cmd: str = "roslaunch arx_x5_controller open_vr_double_arm.launch"
    vr_ros_serial_cwd: str = ""
    vr_ros_serial_setup_script: str = ""
    vr_ros_serial_cmd: str = "rosrun serial_port serial_port"


class DesktopLiftSettings(BaseModel):
    """桌面端升降台控制配置。"""

    enabled: bool = False
    python_bin: str = "/usr/local/bin/python3.10"
    script_path: str = ""
    min_height: int = Field(default=0, ge=0)
    max_height: int = Field(default=600, ge=0)
    step: int = Field(default=100, gt=0)


class DesktopSettings(BaseModel):
    """桌面端配置。"""

    runtime: DesktopRuntimeSettings = Field(default_factory=DesktopRuntimeSettings)
    lift: DesktopLiftSettings = Field(default_factory=DesktopLiftSettings)


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
    )

    server: ServerSettings = Field(default_factory=ServerSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    cloud: CloudSettings = Field(default_factory=CloudSettings)
    zeromq: ZeromqSettings = Field(default_factory=ZeromqSettings)
    webrtc: WebrtcSettings = Field(default_factory=WebrtcSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    upload: UploadSettings = Field(default_factory=UploadSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    task: TaskSettings = Field(default_factory=TaskSettings)
    monitor: MonitorSettings = Field(default_factory=MonitorSettings)
    heartbeat: HeartbeatSettings = Field(default_factory=HeartbeatSettings)
    collection: CollectionSettings = Field(default_factory=CollectionSettings)
    process_monitor: ProcessMonitorSettings = Field(default_factory=ProcessMonitorSettings)
    desktop: DesktopSettings = Field(default_factory=DesktopSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """自定义配置源优先级：初始化参数 > 环境变量 > TOML 文件"""
        return (
            init_settings,
            env_settings,
            MergedTomlConfigSettingsSource(settings_cls),
        )


@lru_cache(maxsize=None)
def _build_settings(config_files: tuple[str, ...]) -> Settings:
    token = _ACTIVE_CONFIG_FILES.set(config_files)
    try:
        return Settings()
    finally:
        _ACTIVE_CONFIG_FILES.reset(token)


def get_settings(env_name: str | None = None, robot_name: str | None = None) -> Settings:
    config_files = tuple(str(path) for path in _resolve_config_files(env_name, robot_name))
    return _build_settings(config_files)
