"""采集全局锁解锁 CLI。"""

from __future__ import annotations

import argparse
import asyncio
import sys

import src.core.logging as _logging_setup  # noqa: F401  触发日志 sink 注册
from loguru import logger

from src.config import get_settings
from src.models.database import init_database
from src.services.collection_lock_service import CollectionLockService

_ = _logging_setup  # 防止静态检查器误报未使用


def _format_state(state) -> str:
    return (
        f"locked={state.locked}\n"
        f"reason={state.reason}\n"
        f"triggered_record_id={state.triggered_record_id}\n"
        f"triggered_at={state.triggered_at}\n"
        f"released_at={state.released_at}\n"
        f"released_by={state.released_by}\n"
        f"release_note={state.release_note}\n"
    )


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings(env_name=args.env, robot_name=args.robot)
    session_maker = await init_database(settings.database)
    svc = CollectionLockService(session_maker)
    await svc.ensure_row()

    state = await svc.get_state()
    if args.status:
        print(_format_state(state))
        return 0

    if not state.locked:
        print("当前未锁定，无需解锁。")
        return 1

    print("当前锁状态:")
    print(_format_state(state))

    if not args.force:
        confirm = input("确认解锁？[y/N] ").strip().lower()
        if confirm != "y":
            print("已取消。")
            return 2

    released = await svc.release(operator=args.operator, note=args.note)
    if not released:
        print("解锁失败（可能并发已被解锁）。")
        return 3

    logger.warning(
        "采集全局锁已解除，已恢复采集: operator={}, note={}, prev_reason={}",
        args.operator,
        args.note,
        state.reason,
    )

    new_state = await svc.get_state()
    print("已解锁。新状态:")
    print(_format_state(new_state))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Release collection global lock")
    parser.add_argument("--robot", help="机器人名称；不传则读取 PERCEPT_ROBOT/APP_ROBOT")
    parser.add_argument("--env", help="运行环境 test/prod；不传则读取 PERCEPT_ENV/APP_ENV")
    parser.add_argument("--operator", help="操作者标识（解锁时必填）")
    parser.add_argument("--note", default=None, help="解锁备注")
    parser.add_argument("--status", action="store_true", help="仅查询状态")
    parser.add_argument("--force", action="store_true", help="跳过交互确认")
    args = parser.parse_args()

    if not args.status and not args.operator:
        parser.error("--operator 是解锁操作的必填参数")

    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
