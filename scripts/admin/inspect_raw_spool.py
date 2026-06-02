"""巡检 storage 内 raw spool (.capture) 目录状态的只读 CLI。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

import src.core.logging as _logging_setup  # noqa: F401  触发日志 sink 注册
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.core.path_validator import PathValidationError, validate_safe_path
from src.models.database import CollectionRecord, init_database
from src.schemas.collection import CollectionRecordStatusEnum

_ = _logging_setup  # 防止静态检查器误报未使用


class SpoolCategory(str, Enum):
    HEALTHY_LINKED = "healthy_linked"
    SEALED_LINKED_ABORTED = "sealed_linked_aborted"
    UNSEALED_LINKED = "unsealed_linked"
    LINKED_BROKEN = "linked_broken"
    ORPHAN_SEALED = "orphan_sealed"
    ORPHAN_UNSEALED = "orphan_unsealed"
    ORPHAN_BROKEN = "orphan_broken"
    INVALID_LINKED_RECORD = "invalid_linked_record"


@dataclass(frozen=True)
class LinkedRecordInfo:
    record_id: int
    task_id: int | None
    collection_status: str
    upload_status: str
    output_dir: str | None
    raw_capture_dir: str | None


@dataclass(frozen=True)
class InvalidLinkedRecord:
    category: str
    record_id: int
    reason: str
    output_dir: str | None
    raw_capture_dir: str | None


@dataclass(frozen=True)
class SpoolInspectionItem:
    category: SpoolCategory
    capture_dir: str
    linked_records: list[LinkedRecordInfo] = field(default_factory=list)
    sealed: bool = False
    manifest_exists: bool = False
    manifest_valid: bool = False
    segment_count: int = 0
    missing_segments: list[str] = field(default_factory=list)
    raw_frame_count: int = 0
    raw_bytes: int = 0
    start_time: str | None = None
    end_time: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class SpoolInspectionReport:
    storage_root: str
    total_capture_dirs: int
    categories: dict[str, int]
    items: list[SpoolInspectionItem]
    invalid_linked_records: list[InvalidLinkedRecord]


def _record_info(record: CollectionRecord) -> LinkedRecordInfo:
    return LinkedRecordInfo(
        record_id=record.id,
        task_id=record.task_id,
        collection_status=record.collection_status,
        upload_status=record.upload_status,
        output_dir=record.output_dir,
        raw_capture_dir=record.raw_capture_dir,
    )


def _resolve_capture_dir_for_record(
    record: CollectionRecord,
    storage_root: Path,
) -> tuple[Path | None, str | None]:
    if record.raw_capture_dir:
        try:
            return (
                validate_safe_path(
                    record.raw_capture_dir,
                    allowed_base=storage_root,
                    allow_relative=False,
                ),
                None,
            )
        except PathValidationError as exc:
            return None, f"raw_capture_dir 非法: {exc}"

    if record.output_dir:
        try:
            output_dir = validate_safe_path(
                record.output_dir,
                allowed_base=storage_root,
                allow_relative=False,
            )
        except PathValidationError as exc:
            return None, f"output_dir 非法: {exc}"
        return output_dir / ".capture", None

    return None, "缺少 output_dir/raw_capture_dir"


def _load_manifest(capture_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    manifest_path = capture_dir / "manifest.json"
    if not manifest_path.exists():
        return None, "manifest_missing"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"manifest_invalid:{exc}"
    if not isinstance(manifest, dict):
        return None, "manifest_not_object"
    return manifest, None


def _resolve_manifest_segments(
    capture_dir: Path,
    manifest: dict[str, Any],
) -> tuple[list[str], list[str], str | None]:
    segments_raw = manifest.get("segments")
    if not isinstance(segments_raw, list):
        return [], [], "segments_missing"
    if not segments_raw:
        return [], [], "segments_empty"

    segments: list[str] = []
    missing_segments: list[str] = []
    for segment_name in segments_raw:
        if not isinstance(segment_name, str) or not segment_name:
            return [], [], "segment_name_invalid"
        segment_path = capture_dir / segment_name
        if not segment_path.exists() or not segment_path.is_file():
            missing_segments.append(segment_name)
        segments.append(segment_name)
    if missing_segments:
        return segments, missing_segments, "segments_missing_files"
    return segments, missing_segments, None


def _classify_capture_dir(
    capture_dir: Path,
    linked_records: Sequence[CollectionRecord],
) -> SpoolInspectionItem:
    sealed = (capture_dir / "SEALED").exists()
    manifest_exists = (capture_dir / "manifest.json").exists()
    manifest, manifest_error = _load_manifest(capture_dir)
    manifest_valid = manifest is not None
    segments: list[str] = []
    missing_segments: list[str] = []
    segment_error: str | None = None
    raw_frame_count = 0
    raw_bytes = 0
    start_time = None
    end_time = None

    if manifest is not None:
        segments, missing_segments, segment_error = _resolve_manifest_segments(
            capture_dir, manifest
        )
        raw_frame_count = int(manifest.get("raw_frame_count") or 0)
        raw_bytes = int(manifest.get("raw_bytes") or 0)
        start_time = (
            manifest.get("start_time") if isinstance(manifest.get("start_time"), str) else None
        )
        end_time = manifest.get("end_time") if isinstance(manifest.get("end_time"), str) else None

    linked_infos = [_record_info(record) for record in linked_records]
    has_aborted_link = any(
        record.collection_status == CollectionRecordStatusEnum.ABORTED.value
        for record in linked_records
    )

    resumable = sealed and manifest_valid and segment_error is None
    if linked_records:
        if resumable:
            category = (
                SpoolCategory.SEALED_LINKED_ABORTED
                if has_aborted_link
                else SpoolCategory.HEALTHY_LINKED
            )
            reason = "resumable"
        elif not sealed:
            category = SpoolCategory.UNSEALED_LINKED
            reason = manifest_error or segment_error or "unsealed"
        else:
            category = SpoolCategory.LINKED_BROKEN
            reason = manifest_error or segment_error or "sealed_but_unresumable"
    else:
        if resumable:
            category = SpoolCategory.ORPHAN_SEALED
            reason = "sealed_without_db_record"
        elif not sealed:
            category = SpoolCategory.ORPHAN_UNSEALED
            reason = manifest_error or segment_error or "unsealed_without_db_record"
        else:
            category = SpoolCategory.ORPHAN_BROKEN
            reason = manifest_error or segment_error or "sealed_but_unresumable"

    return SpoolInspectionItem(
        category=category,
        capture_dir=str(capture_dir),
        linked_records=linked_infos,
        sealed=sealed,
        manifest_exists=manifest_exists,
        manifest_valid=manifest_valid,
        segment_count=len(segments),
        missing_segments=missing_segments,
        raw_frame_count=raw_frame_count,
        raw_bytes=raw_bytes,
        start_time=start_time,
        end_time=end_time,
        reason=reason,
    )


async def _load_records(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[CollectionRecord]:
    async with session_factory() as db:
        result = await db.execute(select(CollectionRecord).order_by(CollectionRecord.id.asc()))
        return list(result.scalars().all())


def _scan_capture_dirs(storage_root: Path) -> set[Path]:
    return {path.resolve() for path in storage_root.rglob(".capture") if path.is_dir()}


def build_report(
    records: Sequence[CollectionRecord],
    storage_root: Path,
) -> SpoolInspectionReport:
    resolved_storage_root = storage_root.resolve()
    linked_map: dict[Path, list[CollectionRecord]] = {}
    invalid_linked_records: list[InvalidLinkedRecord] = []

    for record in records:
        capture_dir, invalid_reason = _resolve_capture_dir_for_record(record, resolved_storage_root)
        if invalid_reason is not None or capture_dir is None:
            invalid_linked_records.append(
                InvalidLinkedRecord(
                    category=SpoolCategory.INVALID_LINKED_RECORD.value,
                    record_id=record.id,
                    reason=invalid_reason or "capture_dir_missing",
                    output_dir=record.output_dir,
                    raw_capture_dir=record.raw_capture_dir,
                )
            )
            continue
        linked_map.setdefault(capture_dir, []).append(record)

    capture_dirs = _scan_capture_dirs(resolved_storage_root)
    capture_dirs.update(linked_map.keys())

    items = [
        _classify_capture_dir(capture_dir, linked_map.get(capture_dir, []))
        for capture_dir in sorted(capture_dirs)
    ]

    category_counts: dict[str, int] = {}
    for item in items:
        category_counts[item.category.value] = category_counts.get(item.category.value, 0) + 1
    if invalid_linked_records:
        category_counts[SpoolCategory.INVALID_LINKED_RECORD.value] = len(invalid_linked_records)

    return SpoolInspectionReport(
        storage_root=str(resolved_storage_root),
        total_capture_dirs=len(items),
        categories=category_counts,
        items=items,
        invalid_linked_records=invalid_linked_records,
    )


def _print_report(report: SpoolInspectionReport, *, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "storage_root": report.storage_root,
                    "total_capture_dirs": report.total_capture_dirs,
                    "categories": report.categories,
                    "items": [asdict(item) for item in report.items],
                    "invalid_linked_records": [
                        asdict(item) for item in report.invalid_linked_records
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    print(f"storage_root={report.storage_root}")
    print(f"total_capture_dirs={report.total_capture_dirs}")
    print("categories:")
    for category, count in sorted(report.categories.items()):
        print(f"  {category}={count}")

    if report.invalid_linked_records:
        print("\ninvalid_linked_records:")
        for item in report.invalid_linked_records:
            print(
                "  "
                f"record_id={item.record_id} reason={item.reason} output_dir={item.output_dir} "
                f"raw_capture_dir={item.raw_capture_dir}"
            )

    for item in report.items:
        linked_ids = [linked.record_id for linked in item.linked_records]
        linked_statuses = [linked.collection_status for linked in item.linked_records]
        print(
            "\nitem: "
            f"category={item.category.value} capture_dir={item.capture_dir} sealed={item.sealed} "
            f"manifest_exists={item.manifest_exists} manifest_valid={item.manifest_valid} "
            f"segment_count={item.segment_count} linked_record_ids={linked_ids} "
            f"linked_collection_statuses={linked_statuses} reason={item.reason} "
            f"missing_segments={item.missing_segments} raw_frame_count={item.raw_frame_count} raw_bytes={item.raw_bytes}"
        )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读巡检 storage 下的 raw spool (.capture) 目录")
    parser.add_argument("--robot", help="机器人名称；不传则读取 PERCEPT_ROBOT/APP_ROBOT")
    parser.add_argument("--env", help="运行环境 test/prod；不传则读取 PERCEPT_ENV/APP_ENV")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings(env_name=args.env, robot_name=args.robot)
    session_factory = await init_database(settings.database)
    report = build_report(
        await _load_records(session_factory),
        Path(settings.storage.base_path),
    )
    _print_report(report, as_json=args.json)
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
