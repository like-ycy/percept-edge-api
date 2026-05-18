from __future__ import annotations

import signal
from pathlib import Path

from desktop.adapters.api_adapter import ApiAdapter
from desktop.adapters.base import BuildContext, ProcessSpec, ShutdownStep
from desktop.adapters.robot_os_adapter import RobotOsAdapter
from desktop.adapters.ros_adapter import RosAdapter
from desktop.adapters.ros_command_adapter import RosCommandAdapter
from desktop.profiles import load_profile
from desktop.services.config_loader import RuntimeConfig
from desktop.services.process_manager import ProcessManager
from desktop.services.runtime_facade import _profile_supports_vr_ros


def _runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        repo_root=Path("/repo"),
        robot_name="robot-test",
        uv_bin="uv",
        ros_env_cwd="/ros",
        ros_setup_script="/ros/setup.bash",
        roscore_cmd="roscore",
        ros_slave_cmd="roslaunch slave.launch",
        ros_master_cmd="roslaunch master.launch",
        robot_os_cwd="/robot-os",
        robot_os_cmd="python robot_os.pyz",
        api_cwd="/api",
        api_cmd="uv run main.py",
        api_startup_timeout=15.0,
        api_gate_interval=1.0,
        status_poll_interval=0.2,
        runtime_health_interval=300.0,
        ros_startup_grace=3.0,
        roscore_startup_grace=2.0,
        process_shutdown_grace=5.0,
        ros_shutdown_grace=8.0,
        force_kill_grace=2.0,
        ready_timeout=30.0,
        monitor_timeout=0.5,
        probe_interval=1.0,
        rep_endpoint="ipc:///tmp/robot-monitor",
        server_port=8000,
        launch_mode="bilateral",
    )


def _ctx() -> BuildContext:
    cfg = _runtime_config()
    return BuildContext(
        repo_root=cfg.repo_root,
        robot_name=cfg.robot_name,
        uv_bin=cfg.uv_bin,
        extra={"config": cfg},
    )


def test_process_spec_keeps_single_signal_compatibility() -> None:
    spec = ProcessSpec(
        name="legacy", cmd="sleep 1", cwd="/tmp", shutdown_signal=15, shutdown_grace=3.0
    )

    assert spec.effective_shutdown_sequence() == (ShutdownStep(signal=15, grace=3.0),)


def test_api_uses_term_then_process_manager_force_kill() -> None:
    spec = ApiAdapter().build_spec(_ctx())

    assert spec.effective_shutdown_sequence() == (
        ShutdownStep(signal=int(signal.SIGTERM), grace=5.0),
    )


def test_robot_os_uses_int_then_term() -> None:
    spec = RobotOsAdapter().build_spec(_ctx())

    assert spec.effective_shutdown_sequence() == (
        ShutdownStep(signal=int(signal.SIGINT), grace=5.0),
        ShutdownStep(signal=int(signal.SIGTERM), grace=5.0),
    )


def test_ros_adapters_use_int_then_term() -> None:
    ctx = _ctx()

    ros_spec = RosAdapter("core").build_spec(ctx)
    command_spec = RosCommandAdapter(
        name="ros_master1",
        log_label="ROS_MASTER1",
        cwd="/ros",
        setup_script="/ros/setup.bash",
        cmd="roslaunch master.launch",
    ).build_spec(ctx)

    expected = (
        ShutdownStep(signal=int(signal.SIGINT), grace=8.0),
        ShutdownStep(signal=int(signal.SIGTERM), grace=5.0),
    )
    assert ros_spec.effective_shutdown_sequence() == expected
    assert command_spec.effective_shutdown_sequence() == expected


def test_shutdown_sequence_callback_ignores_reused_process_name() -> None:
    class RecordingProcessManager(ProcessManager):
        def __init__(self) -> None:
            super().__init__()
            self.signals: list[int] = []

        def _terminate_process_tree(self, *, pid: int, sig: int, name: str = "") -> None:
            del pid, name
            self.signals.append(sig)

    manager = RecordingProcessManager()
    manager._gen["robot_os"] = 2

    manager._run_shutdown_sequence(
        name="robot_os",
        gen=1,
        pid=1234,
        sequence=(ShutdownStep(signal=int(signal.SIGTERM), grace=0.0),),
        step_index=0,
    )

    assert manager.signals == []


def test_cr4_vr_flow_includes_managed_vr_ros_before_robot_os() -> None:
    cfg = _runtime_config()
    cfg.launch_mode = "vr"
    cfg.vr_ros_enabled = True
    profile = load_profile("robot-cr4c")

    steps = profile.flow_factory(profile, cfg)
    stage_labels = [step.stage_label for step in steps]
    adapters = [step.adapter for step in steps]

    assert _profile_supports_vr_ros(profile)
    assert "VR ROS" in stage_labels
    assert "VR 准备确认" in stage_labels
    assert adapters.index("vr_ros_arm") < adapters.index("robot_os")
    assert adapters.index("vr_ros_serial") < adapters.index("robot_os")


def test_w1_does_not_support_managed_vr_ros_even_if_config_enabled() -> None:
    cfg = _runtime_config()
    cfg.launch_mode = "vr"
    cfg.vr_ros_enabled = True
    profile = load_profile("robot-w1")

    steps = profile.flow_factory(profile, cfg)

    assert not _profile_supports_vr_ros(profile)
    assert [step.adapter for step in steps if step.adapter] == [
        "robot_os",
        "robot_os",
        "api",
        "api",
    ]
