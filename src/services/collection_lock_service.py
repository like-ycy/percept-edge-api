"""采集全局锁服务（单行 collection_lock 表）"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select, update

from src.models.database import CollectionLock, now_shanghai

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker


class CollectionLockService:
    """读写 collection_lock 单行表"""

    def __init__(self, session_maker: "async_sessionmaker") -> None:
        self._session_maker = session_maker

    async def ensure_row(self) -> None:
        """启动时保证 id=1 行存在；幂等。"""
        async with self._session_maker() as db:
            result = await db.execute(select(CollectionLock).where(CollectionLock.id == 1))
            if result.scalar_one_or_none() is None:
                db.add(CollectionLock(id=1, locked=False))
                await db.commit()

    async def get_state(self) -> CollectionLock:
        async with self._session_maker() as db:
            result = await db.execute(select(CollectionLock).where(CollectionLock.id == 1))
            row = result.scalar_one_or_none()
            if row is None:
                raise RuntimeError("collection_lock row missing; call ensure_row() first")
            return row

    async def is_locked(self) -> bool:
        state = await self.get_state()
        return bool(state.locked)

    async def lock(self, *, record_id: int, reason: str) -> None:
        """覆盖式锁定，每次更新触发信息为最新值。"""
        async with self._session_maker() as db:
            await db.execute(
                update(CollectionLock)
                .where(CollectionLock.id == 1)
                .values(
                    locked=True,
                    reason=reason,
                    triggered_record_id=record_id,
                    triggered_at=now_shanghai(),
                    released_at=None,
                    released_by=None,
                    release_note=None,
                )
            )
            await db.commit()

    async def release(self, *, operator: str, note: str | None) -> bool:
        """解锁；返回是否实际释放。"""
        async with self._session_maker() as db:
            result = await db.execute(select(CollectionLock).where(CollectionLock.id == 1))
            row = result.scalar_one_or_none()
            if row is None or not row.locked:
                return False
            await db.execute(
                update(CollectionLock)
                .where(CollectionLock.id == 1)
                .values(
                    locked=False,
                    released_at=now_shanghai(),
                    released_by=operator,
                    release_note=note,
                )
            )
            await db.commit()
            return True
