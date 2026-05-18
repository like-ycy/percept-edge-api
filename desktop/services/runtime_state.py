"""运行时状态机 + 持久化存储。

RuntimeStateMachine 不再内嵌 CR4C 专属阶段，初始阶段列表由调用方（profile / flow）提供。
RuntimeStateStore 原子写入 JSON 快照，UI 与外部观察者可读取。
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from desktop.models.runtime_health import RuntimeHealth
from desktop.models.runtime_snapshot import RuntimeSnapshot
from desktop.models.runtime_state import RuntimeState
from desktop.models.stage_state import StageState, StageStatus


@dataclass
class RuntimeStateMachine:
    environment: str = "test"
    runtime_state: RuntimeState = RuntimeState.IDLE
    global_status: str = "未启动"
    message: str = "等待启动"
    health: RuntimeHealth = field(default_factory=RuntimeHealth)
    initial_stages: Sequence[StageState] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._stages: dict[str, StageState] = self._build_stages()

    @property
    def stages(self) -> dict[str, StageState]:
        return self._stages

    def reset(self, environment: Optional[str] = None) -> None:
        if environment is not None:
            self.environment = environment
        self.runtime_state = RuntimeState.IDLE
        self.global_status = "未启动"
        self.message = "等待启动"
        self.health = RuntimeHealth()
        self._stages = self._build_stages()

    def update_health(self, health: RuntimeHealth) -> None:
        self.health = health

    def transition(
        self, state: RuntimeState, *, message: str, global_status: Optional[str] = None
    ) -> None:
        self.runtime_state = state
        self.message = message
        if global_status is not None:
            self.global_status = global_status

    def update_stage(
        self,
        stage_name: str,
        status: StageStatus,
        *,
        summary: str,
        pid: Optional[str] = None,
        last_error: Optional[str] = None,
        append_detail: Optional[str] = None,
    ) -> None:
        stage = self._stages[stage_name]
        stage.status = status
        stage.summary = summary
        if pid is not None:
            stage.pid = pid
        if last_error is not None:
            stage.last_error = last_error
        if append_detail and append_detail not in stage.details:
            stage.details.append(append_detail)

    def mark_all_active_stopping(self) -> None:
        for stage in self._stages.values():
            if stage.status in {StageStatus.STARTING, StageStatus.RUNNING}:
                stage.status = StageStatus.STOPPING
                stage.summary = "正在停止"

    def mark_all_stopped(self) -> None:
        for stage in self._stages.values():
            if stage.status != StageStatus.FAILED:
                stage.status = StageStatus.STOPPED
                stage.summary = "已停止"

    def fail_stage(self, stage_name: str, message: str) -> None:
        self.update_stage(stage_name, StageStatus.FAILED, summary="发生错误", last_error=message)
        self.transition(RuntimeState.ERROR, message=message, global_status="错误")

    def snapshot(self, *, running: bool, state_file: str) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            environment=self.environment,
            global_status=self.global_status,
            runtime_state=self.runtime_state.value,
            message=self.message,
            running=running,
            stages=[deepcopy(stage) for stage in self._stages.values()],
            health=deepcopy(self.health),
            state_file=state_file,
        )

    def _build_stages(self) -> dict[str, StageState]:
        return {stage.name: deepcopy(stage) for stage in self.initial_stages}


class RuntimeStateStore:
    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self._writable = True
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._writable = False

    def write_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        """原子写入快照：先写临时文件，再 replace 到目标路径。"""
        if not self._writable:
            return
        payload = asdict(snapshot)
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.state_file.parent),
                prefix=".state_",
                suffix=".tmp",
            )
            os.close(fd)
            tmp = Path(tmp_path)
            try:
                tmp.write_text(data, encoding="utf-8")
                tmp.replace(self.state_file)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        except OSError:
            return

    def read_snapshot(self) -> Optional[dict[str, Any]]:
        if not self.state_file.is_file():
            return None
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            return None
