"""SQLAlchemy 数据库模型"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import (
    Boolean,
    Column,
    ColumnDefault,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    literal,
    text,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.schemas.status import (
    CloudNotifyStatus,
    CollectionRecordStatus,
    TaskLocalStatus,
    TaskStatus,
    UploadStatus,
)

# 上海时区 (UTC+8)
SHANGHAI_TZ = timezone(timedelta(hours=8))
_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "sql"


def now_shanghai() -> datetime:
    """获取当前上海时间"""
    return datetime.now(SHANGHAI_TZ)


class Base(AsyncAttrs, DeclarativeBase):
    """模型基类"""

    pass


class Task(Base):
    """任务模型（合并模板和任务数据）"""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("idx_task_user_created", "user_id", text("created_at DESC")),
        Index("idx_task_user_status", "user_id", "status"),
        UniqueConstraint("user_id", "task_id", name="uq_task_user_task_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    template_name: Mapped[str] = mapped_column(String(255))
    template_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_type_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    purpose_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    case: Mapped[str | None] = mapped_column(String(255), nullable=True)
    initial_state: Mapped[str | None] = mapped_column(String(255), nullable=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    task_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    repeat: Mapped[int] = mapped_column(Integer, default=1)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.PENDING.value)
    collector_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    collector_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    task_created_user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    task_updated_user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    local_status: Mapped[str] = mapped_column(String(32), default=TaskLocalStatus.IDLE.value)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[int] = mapped_column(Integer, default=0)


class CollectionRecord(Base):
    """采集记录模型（用于状态持久化和掉电恢复）"""

    __tablename__ = "collection_records"
    __table_args__ = (
        Index("idx_record_user_created", "user_id", text("created_at DESC")),
        Index("idx_record_user_status", "user_id", "upload_status"),
        Index("idx_record_user_task", "user_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[int] = mapped_column(Integer, default=0)
    frame_count: Mapped[int] = mapped_column(Integer, default=0)
    start_time: Mapped[datetime | None] = mapped_column(nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(nullable=True)
    collection_status: Mapped[str] = mapped_column(
        String(32), default=CollectionRecordStatus.COLLECTING.value
    )
    validation_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    validation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    upload_status: Mapped[str] = mapped_column(String(32), default=UploadStatus.PENDING.value)
    upload_progress: Mapped[int] = mapped_column(Integer, default=0)
    upload_started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    upload_finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    materialize_progress: Mapped[int] = mapped_column(Integer, default=0)
    materialize_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    materialized_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cloud_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cloud_notify_status: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=CloudNotifyStatus.PENDING.value
    )
    cloud_notify_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cloud_notify_attempts: Mapped[int] = mapped_column(Integer, default=0)
    cloud_notified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    output_dir: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_capture_dir: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_bytes: Mapped[int] = mapped_column(Integer, default=0)
    raw_frame_count: Mapped[int] = mapped_column(Integer, default=0)
    spool_sealed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    task_progress_counted: Mapped[bool] = mapped_column(Boolean, default=False)
    files: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=now_shanghai)
    updated_at: Mapped[datetime] = mapped_column(default=now_shanghai, onupdate=now_shanghai)


async def init_database(settings) -> async_sessionmaker[AsyncSession]:
    """初始化数据库连接"""
    os.makedirs(os.path.dirname(settings.path) or ".", exist_ok=True)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{settings.path}",
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.run_sync(Base.metadata.create_all)
        await _apply_sql_migrations(conn)
        await _sync_missing_columns(conn)

    return async_sessionmaker(engine, expire_on_commit=False)


async def _sync_missing_columns(conn: AsyncConnection) -> None:
    """根据模型定义自动补充 SQLite 中缺失的安全字段。"""
    added_columns = 0

    for table in Base.metadata.sorted_tables:
        existing_columns = await _get_existing_columns(conn, table.name)
        for column in table.columns:
            if column.name in existing_columns:
                continue

            add_column_sql = _build_add_column_sql(conn, table.name, column)
            await conn.execute(text(add_column_sql))
            added_columns += 1
            logger.info(
                "自动补充数据库字段: table={}, column={}, sql={}",
                table.name,
                column.name,
                add_column_sql,
            )

    if added_columns:
        logger.info("数据库字段自动补充完成: added_columns={}", added_columns)


async def _get_existing_columns(conn: AsyncConnection, table_name: str) -> set[str]:
    """读取 SQLite 表中的现有字段名。"""
    quoted_table_name = _quote_identifier(conn, table_name)
    result = await conn.execute(text(f"PRAGMA table_info({quoted_table_name})"))
    return {row[1] for row in result.fetchall()}


def _build_add_column_sql(conn: AsyncConnection, table_name: str, column: Column[object]) -> str:
    """构造安全的 SQLite ADD COLUMN 语句。"""
    rejection_reason = _get_auto_add_rejection_reason(column)
    if rejection_reason is not None:
        raise RuntimeError(
            f"无法自动补充数据库字段 {table_name}.{column.name}: {rejection_reason}，请编写 sql 迁移文件"
        )

    sync_connection = conn.sync_connection
    if sync_connection is None:
        raise RuntimeError("数据库连接未初始化，无法生成 ADD COLUMN 语句")

    dialect = sync_connection.dialect
    column_type = column.type.compile(dialect=dialect)
    quoted_table_name = _quote_identifier(conn, table_name)
    quoted_column_name = _quote_identifier(conn, column.name)
    clauses = [quoted_column_name, column_type]
    default_sql = _get_sqlite_default_sql(conn, column)

    if default_sql is not None:
        clauses.append(f"DEFAULT {default_sql}")
    if not column.nullable:
        clauses.append("NOT NULL")

    return f"ALTER TABLE {quoted_table_name} ADD COLUMN {' '.join(clauses)}"


def _get_auto_add_rejection_reason(column: Column[object]) -> str | None:
    """判断字段是否适合自动 ADD COLUMN。"""
    if column.primary_key:
        return "主键字段不支持自动补充"
    if column.unique:
        return "唯一约束字段不支持自动补充"
    if column.foreign_keys:
        return "外键字段不支持自动补充"
    if column.computed is not None:
        return "计算字段不支持自动补充"
    if column.server_default is not None:
        return "服务端默认值字段不支持自动补充"
    if not column.nullable and column.default is None and column.server_default is None:
        return "非空字段缺少 SQLite 默认值"
    if column.default is not None and (column.default.is_callable or column.default.is_sequence):
        return "动态默认值字段不支持自动补充"
    if column.default is not None and not isinstance(column.default, ColumnDefault):
        return "未知 Python 默认值字段不支持自动补充"
    return None


def _get_sqlite_default_sql(conn: AsyncConnection, column: Column[object]) -> str | None:
    """获取可写入 SQLite DDL 的常量默认值。"""
    if column.server_default is not None:
        raise RuntimeError(f"字段 {column.name} 的服务端默认值不支持自动补充")
    if column.default is None:
        return None
    if not isinstance(column.default, ColumnDefault):
        raise RuntimeError(f"字段 {column.name} 的 Python 默认值不支持自动补充")

    sync_connection = conn.sync_connection
    if sync_connection is None:
        raise RuntimeError("数据库连接未初始化，无法生成默认值语句")

    dialect = sync_connection.dialect
    return str(
        literal(column.default.arg).compile(
            dialect=dialect,
            compile_kwargs={"literal_binds": True},
        )
    )


def _quote_identifier(conn: AsyncConnection, identifier: str) -> str:
    """使用 SQLAlchemy 方言安全引用表名或字段名。"""
    sync_connection = conn.sync_connection
    if sync_connection is None:
        raise RuntimeError("数据库连接未初始化，无法引用标识符")
    return sync_connection.dialect.identifier_preparer.quote(identifier)


async def _apply_sql_migrations(conn: AsyncConnection) -> None:
    """执行 sql 目录中的手工迁移。"""
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMP NOT NULL
            )
            """
        )
    )

    if not _MIGRATIONS_DIR.exists():
        return

    applied_result = await conn.execute(text("SELECT filename FROM schema_migrations"))
    applied = {row[0] for row in applied_result.fetchall()}

    for migration_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if migration_file.name in applied:
            continue

        sql_content = migration_file.read_text(encoding="utf-8").strip()
        statements = [
            statement.strip() for statement in sql_content.split(";") if statement.strip()
        ]

        for statement in statements:
            try:
                await conn.execute(text(statement))
            except OperationalError as exc:
                error_text = str(exc).lower()
                if "duplicate column name" not in error_text:
                    raise
                logger.warning(
                    "迁移语句已生效，跳过重复列: file={}, sql={}", migration_file.name, statement
                )

        await conn.execute(
            text(
                "INSERT INTO schema_migrations (filename, applied_at) VALUES (:filename, :applied_at)"
            ),
            {"filename": migration_file.name, "applied_at": now_shanghai()},
        )
        logger.info("数据库迁移已应用: {}", migration_file.name)


def get_db_engine(session_factory: async_sessionmaker[AsyncSession]) -> AsyncEngine:
    """从异步会话工厂中提取数据库 engine"""
    bind = session_factory.kw.get("bind")
    if not isinstance(bind, AsyncEngine):
        raise RuntimeError("数据库会话工厂未绑定 AsyncEngine")
    return bind


class CollectionLock(Base):
    """全局采集锁（单行表，id=1）"""

    __tablename__ = "collection_lock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_record_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    release_note: Mapped[str | None] = mapped_column(Text, nullable=True)
