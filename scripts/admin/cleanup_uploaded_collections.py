"""清理已上传且已通知云端的本地采集目录 CLI。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Sequence

import src.core.logging as _logging_setup  # noqa: F401  触发日志 sink 注册

from src.config import get_settings
from src.models.database import init_database
from src.services.cleanup_service import (
    CleanupItem,
    CleanupResult,
    CleanupService,
)

_ = _logging_setup  # 防止静态检查器误报未使用


def _print_summary(result: CleanupResult, *, execute: bool, cutoff) -> None:
    mode = "execute" if execute else "dry-run"
    plan = result.plan
    print(f"mode={mode}")
    print(f"cutoff={cutoff.isoformat()}")
    print(f"eligible_count={len(plan.eligible)}")
    print(f"missing_count={len(plan.missing)}")
    print(f"unsafe_count={len(plan.unsafe)}")
    print(f"deleted_count={len(result.deleted)}")
    print(f"failed_count={len(result.failed)}")
    print(f"reclaimable_bytes={plan.reclaimable_bytes}")
    _print_items("eligible", plan.eligible)
    _print_items("missing", plan.missing)
    _print_items("unsafe", plan.unsafe)
    _print_items("deleted", result.deleted)
    _print_items("failed", result.failed)


def _print_items(title: str, items: Sequence[CleanupItem]) -> None:
    if not items:
        return
    print(f"\n{title}:")
    for item in items:
        fields = [
            f"record_id={item.record.id}",
            f"path={item.path}",
            f"reason={item.reason}",
            f"size={item.size_bytes}",
        ]
        if item.record.end_time is not None:
            fields.insert(1, f"end_time={item.record.end_time.isoformat()}")
        if item.error:
            fields.append(f"error={item.error}")
        print("  " + " ".join(fields))


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是正整数") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return parsed


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="清理已上传且已通知云端的本地采集目录")
    parser.add_argument("--robot", help="机器人名称；不传则读取 PERCEPT_ROBOT/APP_ROBOT")
    parser.add_argument("--env", help="运行环境 test/prod；不传则读取 PERCEPT_ENV/APP_ENV")
    parser.add_argument("--older-than-days", type=_positive_int, default=3, help="保留天数，默认 3")
    parser.add_argument("--limit", type=_positive_int, help="最多处理候选条数")
    parser.add_argument("--execute", action="store_true", help="实际删除；不传则只 dry-run")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings(env_name=args.env, robot_name=args.robot)
    session_factory = await init_database(settings.database)
    async with session_factory() as db:
        service = CleanupService(db=db, storage_root=Path(settings.storage.base_path))
        cutoff, plan = await service.preview(args.older_than_days, args.limit)
        result = service.execute_plan(plan, execute=args.execute)
    _print_summary(result, execute=args.execute, cutoff=cutoff)
    if args.execute and result.failed:
        return 3
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
