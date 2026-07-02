"""本地已上传采集目录清理服务。"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import BusinessError
from src.core.path_validator import PathValidationError, validate_safe_path
from src.models.database import CollectionRecord, now_shanghai
from src.schemas.cleanup import CLEANUP_CONFIRM_TEXT
from src.schemas.upload import UploadStatus


class CleanupBucket(str, Enum):
    ELIGIBLE = "eligible"
    MISSING = "missing"
    UNSAFE = "unsafe"
    FAILED = "failed"
    DELETED = "deleted"


@dataclass(frozen=True)
class CleanupItem:
    record: CollectionRecord
    bucket: CleanupBucket
    path: Path | None
    reason: str
    size_bytes: int = 0
    error: str | None = None


@dataclass(frozen=True)
class CleanupPlan:
    eligible: list[CleanupItem] = field(default_factory=list)
    missing: list[CleanupItem] = field(default_factory=list)
    unsafe: list[CleanupItem] = field(default_factory=list)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(item.size_bytes for item in self.eligible)


@dataclass(frozen=True)
class CleanupResult:
    plan: CleanupPlan
    deleted: list[CleanupItem] = field(default_factory=list)
    failed: list[CleanupItem] = field(default_factory=list)
    skipped_ids: list[int] = field(default_factory=list)


class CleanupService:
    """已上传且已通知云端的本地目录清理服务；只删除目录，不删除数据库记录。"""

    def __init__(self, db: AsyncSession | None, storage_root: Path) -> None:
        self.db = db
        self.storage_root = storage_root.resolve()

    async def preview(
        self, older_than_days: int, limit: int | None
    ) -> tuple[datetime, CleanupPlan]:
        cutoff = now_shanghai() - timedelta(days=older_than_days)
        records = await self.load_candidate_records(cutoff=cutoff, limit=limit, record_ids=None)
        plan = await asyncio.to_thread(self.build_plan, records)
        return cutoff, plan

    async def execute(
        self,
        *,
        record_ids: Sequence[int],
        older_than_days: int,
        confirm_text: str,
    ) -> tuple[datetime, CleanupResult]:
        if confirm_text != CLEANUP_CONFIRM_TEXT:
            raise BusinessError(f"确认文本必须为 {CLEANUP_CONFIRM_TEXT}")

        cutoff = now_shanghai() - timedelta(days=older_than_days)
        records = await self.load_candidate_records(
            cutoff=cutoff, limit=None, record_ids=record_ids
        )
        plan = await asyncio.to_thread(self.build_plan, records)
        result = await asyncio.to_thread(self.execute_plan, plan, execute=True)
        loaded_ids = {record.id for record in records}
        requested_ids = list(dict.fromkeys(record_ids))
        skipped_ids = [record_id for record_id in requested_ids if record_id not in loaded_ids]
        return cutoff, CleanupResult(
            plan=result.plan,
            deleted=result.deleted,
            failed=result.failed,
            skipped_ids=skipped_ids,
        )

    async def load_candidate_records(
        self,
        *,
        cutoff: datetime,
        limit: int | None,
        record_ids: Sequence[int] | None,
    ) -> list[CollectionRecord]:
        if self.db is None:
            raise RuntimeError("CleanupService.load_candidate_records requires a database session")

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
        if record_ids is not None:
            query = query.where(CollectionRecord.id.in_(list(record_ids)))
        if limit is not None:
            query = query.limit(limit)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    def build_plan(self, records: Sequence[CollectionRecord]) -> CleanupPlan:
        eligible: list[CleanupItem] = []
        missing: list[CleanupItem] = []
        unsafe: list[CleanupItem] = []

        for record in records:
            item = self.classify_record(record)
            if item.bucket == CleanupBucket.ELIGIBLE:
                eligible.append(item)
            elif item.bucket == CleanupBucket.MISSING:
                missing.append(item)
            else:
                unsafe.append(item)

        return CleanupPlan(eligible=eligible, missing=missing, unsafe=unsafe)

    def classify_record(self, record: CollectionRecord) -> CleanupItem:
        if not record.output_dir:
            return CleanupItem(record, CleanupBucket.UNSAFE, None, "output_dir_empty")

        try:
            cleanup_path = validate_safe_path(record.output_dir, allowed_base=self.storage_root)
        except PathValidationError as exc:
            return CleanupItem(
                record, CleanupBucket.UNSAFE, None, "path_outside_storage_root", error=str(exc)
            )

        if cleanup_path == self.storage_root:
            return CleanupItem(record, CleanupBucket.UNSAFE, cleanup_path, "unsafe_storage_root")

        try:
            cleanup_path.relative_to(self.storage_root)
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
            return CleanupItem(
                record, CleanupBucket.UNSAFE, cleanup_path, "output_dir_not_directory"
            )

        return CleanupItem(
            record,
            CleanupBucket.ELIGIBLE,
            cleanup_path,
            "eligible",
            size_bytes=self.calculate_directory_size(cleanup_path),
        )

    def calculate_directory_size(self, path: Path) -> int:
        total = 0
        for child in path.rglob("*"):
            if child.is_file():
                total += child.stat().st_size
        return total

    def execute_plan(self, plan: CleanupPlan, *, execute: bool) -> CleanupResult:
        if not execute:
            return CleanupResult(plan=plan)

        deleted: list[CleanupItem] = []
        failed: list[CleanupItem] = []
        for item in plan.eligible:
            if item.path is None:
                failed.append(
                    CleanupItem(
                        item.record, CleanupBucket.FAILED, None, "path_missing", item.size_bytes
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
                    item.record, CleanupBucket.DELETED, item.path, "deleted", item.size_bytes
                )
            )

        return CleanupResult(plan=plan, deleted=deleted, failed=failed)
