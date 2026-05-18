# src/services/task_converter.py
"""任务数据转换函数"""

import json
from typing import Any, Optional

from src.models.database import Task
from src.schemas.task import CloudTaskDetail, StepResponse, TaskResponse


def serialize_instructions(instructions: list[str]) -> str:
    """将任务指令列表序列化为数据库中的 JSON 字符串。"""
    return json.dumps(instructions, ensure_ascii=False)


def deserialize_instructions(raw_instructions: Optional[str]) -> list[str]:
    """从数据库 JSON 字符串解析任务指令列表，兼容历史纯字符串数据。"""
    if not raw_instructions:
        return []

    try:
        parsed: Any = json.loads(raw_instructions)
    except (json.JSONDecodeError, TypeError):
        stripped = raw_instructions.strip()
        return [stripped] if stripped else []

    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, str)]
    if isinstance(parsed, str):
        stripped = parsed.strip()
        return [stripped] if stripped else []
    return []


def task_to_response(task: Task) -> TaskResponse:
    """将数据库模型转换为 API 响应"""
    # 解析 steps JSON
    steps = []
    if task.steps:
        try:
            steps_data = json.loads(task.steps)
            steps = [StepResponse(**s) for s in steps_data]
        except (json.JSONDecodeError, TypeError):
            pass

    return TaskResponse(
        id=task.id,
        template_name=task.template_name,
        template_desc=task.template_desc or None,
        device_type_name=task.device_type_name,
        purpose_name=task.purpose_name,
        plan_name=task.plan_name,
        task_id=task.task_id,
        task_name=task.task_name or None,
        initial_state=task.initial_state or "",
        repeat=task.repeat,
        progress=task.progress,
        steps=steps,
        case=task.case or "",
        instructions=deserialize_instructions(task.instructions),
        status=task.status,
        collector_name=task.collector_name,
        task_created_user_name=task.task_created_user_name,
        task_updated_user_name=task.task_updated_user_name,
    )


def cloud_detail_to_model(detail: CloudTaskDetail) -> dict:
    """将云端任务详情转换为数据库模型字段"""
    template = detail.template

    # 序列化 steps 为 JSON
    steps_json = json.dumps([s.model_dump() for s in detail.step]) if detail.step else None

    return {
        "user_id": detail.collector.id if detail.collector else 0,
        # 模板字段
        "template_name": template.name,
        "template_desc": template.desc or None,
        "device_type_name": template.device_type.name if template.device_type else None,
        "plan_name": template.plan.name if template.plan else None,
        "purpose_name": template.purpose.name if template.purpose else None,
        "initial_state": template.initial_state or "",
        # 任务字段
        "task_id": detail.id,
        "task_name": detail.name or None,
        "repeat": detail.repeat,
        "progress": detail.progress,
        "steps": steps_json,
        "case": detail.case,
        "instructions": serialize_instructions(detail.instructions),
        "status": detail.status,
        "collector_id": detail.collector.id if detail.collector else None,
        "collector_name": detail.collector.username if detail.collector else None,
        "task_created_user_name": detail.created_user.username if detail.created_user else None,
        "task_updated_user_name": detail.updated_user.username if detail.updated_user else None,
        # 云端时间戳
        "created_at": detail.created_time or 0,
        "updated_at": detail.updated_time or 0,
    }
