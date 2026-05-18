"""Profile 层契约。

RobotProfile 声明一个机型的能力组合：
- adapters：本机型用到的 Adapter 实例
- flow_factory：根据 profile + 运行时配置构造 FlowRunner
- launch_modes：桌面端允许选择的启动方式（bilateral=同构臂，vr=VR）
- extra：透传给 BuildContext.extra 的机型定制字段
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from desktop.adapters.base import Adapter
from desktop.flows.base import Step
from desktop.services.config_loader import RuntimeConfig

# flow_factory 只负责产出 Step 列表；FlowRunner 由 app 层组装
# （注入 process_manager / health_checker / build_ctx）。
FlowFactory = Callable[["RobotProfile", RuntimeConfig], Sequence[Step]]


@dataclass(frozen=True)
class RobotProfile:
    robot_name: str
    display_name: str
    adapters: Sequence[Adapter]
    flow_factory: FlowFactory
    ros_required: bool = True
    can_required: bool = False
    launch_modes: Sequence[str] = ("bilateral",)
    extra: Mapping[str, Any] = field(default_factory=dict)

    def adapter(self, name: str) -> Adapter:
        for a in self.adapters:
            if a.name == name:
                return a
        raise KeyError(f"profile {self.robot_name} 无 adapter: {name}")
