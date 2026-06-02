"""基于 raw spool 巡检结果执行恢复或清理动作的 CLI。"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

import src.core.logging as _logging_setup  # noqa: F401  触发日志 sink 注册
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scripts.admin.inspect_raw_spool import (
    SpoolCategory,
    SpoolInspectionItem,
    build_report,
)
from src.config import get_settings
from src.core.exceptions import BusinessError
from src.core.path_validator import PathValidationError, validate_safe_path
from src.models.database import CollectionRecord, init_database
from src.schemas.collection import CollectionRecordStatusEnum

_ = _logging_setup  # 防止静态检查器误报未使用


@dataclass(frozen=True)
class CleanupCandidate:
    category: str
    capture_dir: str
    reason: str
    modified_at: str | None = None


@dataclass(frozen=True)
class CleanupPlan:
    category: str
    older_than_days: int | None
    candidates: list[CleanupCandidate] = field(default_factory=list)


async def _load_records(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[CollectionRecord]:
    async with session_factory() as db:
        result = await db.execute(select(CollectionRecord).order_by(CollectionRecord.id.asc()))
        return list(result.scalars().all())


def _resolve_report_item(
    *,
    report_items: Sequence[SpoolInspectionItem],
    capture_dir: Path,
) -> SpoolInspectionItem | None:
    resolved_capture_dir = capture_dir.resolve()
    for item in report_items:
        if Path(item.capture_dir).resolve() == resolved_capture_dir:
            return item
    return None


def _find_report_item_for_record(
    *,
    report_items: Sequence[SpoolInspectionItem],
    record_id: int,
) -> SpoolInspectionItem | None:
    for item in report_items:
        if any(linked.record_id == record_id for linked in item.linked_records):
            return item
    return None


def _load_resumable_manifest(capture_dir: Path) -> tuple[dict[str, object], datetime]:
    import json

    manifest_path = capture_dir / "manifest.json"
    manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest_raw, dict):
        raise BusinessError("manifest 不是对象，无法恢复")
    end_time_raw = manifest_raw.get("end_time")
    if not isinstance(end_time_raw, str):
        raise BusinessError("manifest 缺少 end_time，无法恢复")
    try:
        sealed_at = datetime.fromisoformat(end_time_raw)
    except ValueError as exc:
        raise BusinessError("manifest end_time 非法，无法恢复") from exc
    return manifest_raw, sealed_at


async def _recover_record(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    record_id: int,
    storage_root: Path,
) -> int:
    async with session_factory() as db:
        record = await db.get(CollectionRecord, record_id)
        if record is None:
            raise BusinessError(f"记录不存在: record_id={record_id}")
        if record.collection_status != CollectionRecordStatusEnum.ABORTED.value:
            raise BusinessError(f"当前记录不是 aborted，无法恢复: record_id={record_id}")
        if not record.output_dir:
            raise BusinessError(f"记录缺少 output_dir，无法恢复: record_id={record_id}")

        validate_safe_path(
            record.output_dir,
            allowed_base=storage_root,
            allow_relative=False,
        )
        report = build_report([record], storage_root)
        item = _find_report_item_for_record(report_items=report.items, record_id=record_id)
        if item is None or item.category != SpoolCategory.SEALED_LINKED_ABORTED:
            raise BusinessError(
                f"记录 {record_id} 的 raw spool 当前不属于 sealed_linked_aborted，无法恢复"
            )
        capture_dir = Path(item.capture_dir)

        manifest, sealed_at = _load_resumable_manifest(capture_dir)
        record.collection_status = CollectionRecordStatusEnum.FINALIZING.value
        record.raw_capture_dir = str(capture_dir)
        raw_bytes = manifest.get("raw_bytes")
        raw_frame_count = manifest.get("raw_frame_count")
        if raw_bytes is not None and not isinstance(raw_bytes, (int, float, str)):
            raise BusinessError("manifest raw_bytes 非法，无法恢复")
        if raw_frame_count is not None and not isinstance(raw_frame_count, (int, float, str)):
            raise BusinessError("manifest raw_frame_count 非法，无法恢复")
        record.raw_bytes = int(raw_bytes or 0)
        record.raw_frame_count = int(raw_frame_count or 0)
        record.spool_sealed_at = sealed_at
        record.end_time = sealed_at
        await db.commit()
        await db.refresh(record)
        return record.id


def _build_cleanup_plan(
    *,
    report_items: Sequence[SpoolInspectionItem],
    category: SpoolCategory,
    older_than_days: int | None,
) -> CleanupPlan:
    cutoff = (
        None
        if older_than_days is None
        else datetime.now().astimezone() - timedelta(days=older_than_days)
    )
    candidates: list[CleanupCandidate] = []
    for item in report_items:
        if item.category != category:
            continue
        capture_dir = Path(item.capture_dir)
        modified_at = None
        if capture_dir.exists():
            modified_at_dt = datetime.fromtimestamp(capture_dir.stat().st_mtime).astimezone()
            modified_at = modified_at_dt.isoformat()
            if cutoff is not None and modified_at_dt >= cutoff:
                continue
        candidates.append(
            CleanupCandidate(
                category=item.category.value,
                capture_dir=item.capture_dir,
                reason=item.reason,
                modified_at=modified_at,
            )
        )
    return CleanupPlan(
        category=category.value, older_than_days=older_than_days, candidates=candidates
    )


def _execute_cleanup(
    *,
    plan: CleanupPlan,
    storage_root: Path,
    execute: bool,
) -> tuple[list[str], list[str]]:
    deleted: list[str] = []
    failed: list[str] = []
    for candidate in plan.candidates:
        capture_dir = Path(candidate.capture_dir)
        try:
            validated_capture_dir = validate_safe_path(
                capture_dir,
                allowed_base=storage_root,
                allow_relative=False,
            )
        except PathValidationError:
            failed.append(candidate.capture_dir)
            continue
        if not execute:
            continue
        try:
            shutil.rmtree(validated_capture_dir)
        except OSError:
            failed.append(candidate.capture_dir)
            continue
        deleted.append(candidate.capture_dir)
    return deleted, failed


def _print_cleanup_plan(
    plan: CleanupPlan, *, execute: bool, deleted: Sequence[str], failed: Sequence[str]
) -> None:
    mode = "execute" if execute else "dry-run"
    print(f"mode={mode}")
    print(f"category={plan.category}")
    print(f"candidate_count={len(plan.candidates)}")
    if plan.older_than_days is not None:
        print(f"older_than_days={plan.older_than_days}")
    if plan.candidates:
        print("candidates:")
        for candidate in plan.candidates:
            print(
                f"  capture_dir={candidate.capture_dir} reason={candidate.reason} modified_at={candidate.modified_at}"
            )
    if execute:
        print(f"deleted_count={len(deleted)}")
        print(f"failed_count={len(failed)}")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于 raw spool 巡检结果执行恢复或清理动作")
    parser.add_argument("--robot", help="机器人名称；不传则读取 PERCEPT_ROBOT/APP_ROBOT")
    parser.add_argument("--env", help="运行环境 test/prod；不传则读取 PERCEPT_ENV/APP_ENV")
    parser.add_argument(
        "--action",
        required=True,
        choices=["recover", "cleanup"],
        help="执行恢复或清理",
    )
    parser.add_argument("--record-id", type=int, help="恢复动作的目标记录 ID")
    parser.add_argument(
        "--category",
        choices=[
            SpoolCategory.ORPHAN_UNSEALED.value,
            SpoolCategory.ORPHAN_BROKEN.value,
            SpoolCategory.ORPHAN_SEALED.value,
        ],
        help="cleanup 动作的目标类别",
    )
    parser.add_argument("--older-than-days", type=int, help="cleanup 预留参数，当前仅展示")
    parser.add_argument("--dry-run", action="store_true", help="cleanup 仅展示，不实际删除")
    parser.add_argument("--execute", action="store_true", help="cleanup 实际删除")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    if args.action == "recover":
        if args.record_id is None:
            raise BusinessError("recover 动作必须传 --record-id")
        settings = get_settings(env_name=args.env, robot_name=args.robot)
        session_factory = await init_database(settings.database)
        storage_root = Path(settings.storage.base_path).resolve()
        record_id = await _recover_record(
            session_factory=session_factory,
            record_id=args.record_id,
            storage_root=storage_root,
        )
        logger.warning("raw spool 运维恢复成功，已转为 finalizing: record_id={}", record_id)
        print(f"recovered_record_id={record_id}")
        return 0

    if args.category is None:
        raise BusinessError("cleanup 动作必须传 --category")
    if args.dry_run and args.execute:
        raise BusinessError("--dry-run 与 --execute 不能同时传")
    if not args.dry_run and not args.execute:
        raise BusinessError("cleanup 动作必须显式传 --dry-run 或 --execute")

    settings = get_settings(env_name=args.env, robot_name=args.robot)
    session_factory = await init_database(settings.database)
    storage_root = Path(settings.storage.base_path).resolve()
    report = build_report(await _load_records(session_factory), storage_root)
    plan = _build_cleanup_plan(
        report_items=report.items,
        category=SpoolCategory(args.category),
        older_than_days=args.older_than_days,
    )
    deleted, failed = _execute_cleanup(
        plan=plan,
        storage_root=storage_root,
        execute=args.execute,
    )
    _print_cleanup_plan(plan, execute=args.execute, deleted=deleted, failed=failed)
    return 3 if args.execute and failed else 0


def main(argv: Sequence[str] | None = None) -> None:
    try:
        exit_code = asyncio.run(_run(_parse_args(argv)))
    except BusinessError as exc:
        print(str(exc))
        exit_code = 2
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
