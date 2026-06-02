"""清理已上传且已通知云端的本地采集目录 CLI。"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import Sequence

import src.core.logging as _logging_setup  # noqa: F401  触发日志 sink 注册
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.core.path_validator import PathValidationError, validate_safe_path
from src.models.database import CollectionRecord, init_database, now_shanghai
from src.schemas.upload import UploadStatus

_ = _logging_setup  # 防止静态检查器误报未使用


class CleanupBucket(str, Enum):
    """清理计划分类。"""

    ELIGIBLE = "eligible"
    MISSING = "missing"
    UNSAFE = "unsafe"
    FAILED = "failed"
    DELETED = "deleted"


@dataclass(frozen=True)
class CleanupItem:
    """单条清理计划项。"""

    record: CollectionRecord
    bucket: CleanupBucket
    path: Path | None
    reason: str
    size_bytes: int = 0
    error: str | None = None


@dataclass(frozen=True)
class CleanupPlan:
    """清理计划。"""

    eligible: list[CleanupItem] = field(default_factory=list)
    missing: list[CleanupItem] = field(default_factory=list)
    unsafe: list[CleanupItem] = field(default_factory=list)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(item.size_bytes for item in self.eligible)


@dataclass(frozen=True)
class CleanupResult:
    """执行结果。"""

    plan: CleanupPlan
    deleted: list[CleanupItem] = field(default_factory=list)
    failed: list[CleanupItem] = field(default_factory=list)


async def _load_candidate_records(
    session_factory: async_sessionmaker[AsyncSession],
    cutoff,
    limit: int | None,
) -> list[CollectionRecord]:
    """加载数据库硬条件满足的清理候选记录。"""
    async with session_factory() as db:
        query = (
            select(CollectionRecord)
            .where(
                CollectionRecord.end_time.is_not(None),
                CollectionRecord.end_time < cutoff,
                CollectionRecord.upload_status == UploadStatus.COMPLETED.value,
                CollectionRecord.cloud_id.is_not(None),
                CollectionRecord.output_dir.is_not(None),
            )
            .order_by(CollectionRecord.end_time.asc(), CollectionRecord.id.asc())
        )
        if limit is not None:
            query = query.limit(limit)

        result = await db.execute(query)
        return list(result.scalars().all())


def _build_cleanup_plan(records: Sequence[CollectionRecord], storage_root: Path) -> CleanupPlan:
    """根据路径安全和文件存在性构建清理计划。"""
    resolved_storage_root = storage_root.resolve()
    eligible: list[CleanupItem] = []
    missing: list[CleanupItem] = []
    unsafe: list[CleanupItem] = []

    for record in records:
        item = _classify_record(record, resolved_storage_root)
        if item.bucket == CleanupBucket.ELIGIBLE:
            eligible.append(item)
            continue
        if item.bucket == CleanupBucket.MISSING:
            missing.append(item)
            continue
        unsafe.append(item)

    return CleanupPlan(eligible=eligible, missing=missing, unsafe=unsafe)


def _classify_record(record: CollectionRecord, storage_root: Path) -> CleanupItem:
    if not record.output_dir:
        return CleanupItem(record, CleanupBucket.UNSAFE, None, "output_dir_empty")

    try:
        cleanup_path = validate_safe_path(record.output_dir, allowed_base=storage_root)
    except PathValidationError as exc:
        return CleanupItem(
            record, CleanupBucket.UNSAFE, None, "path_outside_storage_root", error=str(exc)
        )

    if cleanup_path == storage_root:
        return CleanupItem(record, CleanupBucket.UNSAFE, cleanup_path, "unsafe_storage_root")

    try:
        cleanup_path.relative_to(storage_root)
    except ValueError as exc:
        return CleanupItem(
            record,
            CleanupBucket.UNSAFE,
            cleanup_path,
            "path_outside_storage_root",
            error=str(exc),
        )

    if not cleanup_path.exists():
        return CleanupItem(record, CleanupBucket.MISSING, cleanup_path, "output_dir_not_found")
    if not cleanup_path.is_dir():
        return CleanupItem(record, CleanupBucket.UNSAFE, cleanup_path, "output_dir_not_directory")

    return CleanupItem(
        record,
        CleanupBucket.ELIGIBLE,
        cleanup_path,
        "eligible",
        size_bytes=_calculate_directory_size(cleanup_path),
    )


def _calculate_directory_size(path: Path) -> int:
    """计算目录内普通文件大小总和。"""
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def _execute_cleanup(plan: CleanupPlan, *, execute: bool) -> CleanupResult:
    """按计划执行清理；dry-run 时不删除。"""
    if not execute:
        return CleanupResult(plan=plan)

    deleted: list[CleanupItem] = []
    failed: list[CleanupItem] = []
    for item in plan.eligible:
        if item.path is None:
            failed.append(
                CleanupItem(
                    item.record,
                    CleanupBucket.FAILED,
                    None,
                    "path_missing",
                    item.size_bytes,
                )
            )
            continue
        try:
            shutil.rmtree(item.path)
        except OSError as exc:
            failed.append(
                CleanupItem(
                    item.record,
                    CleanupBucket.FAILED,
                    item.path,
                    "delete_failed",
                    item.size_bytes,
                    error=str(exc),
                )
            )
            continue
        deleted.append(
            CleanupItem(
                item.record,
                CleanupBucket.DELETED,
                item.path,
                "deleted",
                item.size_bytes,
            )
        )

    return CleanupResult(plan=plan, deleted=deleted, failed=failed)


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
    cutoff = now_shanghai() - timedelta(days=args.older_than_days)
    records = await _load_candidate_records(session_factory, cutoff, args.limit)
    plan = _build_cleanup_plan(records, Path(settings.storage.base_path))
    result = _execute_cleanup(plan, execute=args.execute)
    _print_summary(result, execute=args.execute, cutoff=cutoff)
    if args.execute and result.failed:
        return 3
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
