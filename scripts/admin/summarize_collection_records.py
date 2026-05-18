"""统计 collection_records 并按月份、日期、用户名输出字典。"""

from __future__ import annotations

import argparse
import asyncio
import pprint
import sys
from datetime import date, datetime, time, timedelta
from typing import cast

import src.core.logging as _logging_setup  # noqa: F401  触发日志 sink 注册
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.models.database import CollectionRecord, init_database

_ = _logging_setup  # 防止静态检查器误报未使用


def _parse_date_or_datetime(value: str, *, is_end: bool) -> datetime:
    """解析 YYYY-MM-DD 或 ISO datetime。"""
    normalized = value.strip()
    if not normalized:
        msg = "日期参数不能为空"
        raise argparse.ArgumentTypeError(msg)

    try:
        parsed_date = date.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.fromisoformat(normalized)
        except ValueError as exc:
            msg = f"日期格式无效: {value}，请使用 YYYY-MM-DD 或 ISO datetime"
            raise argparse.ArgumentTypeError(msg) from exc

    if is_end:
        parsed_date += timedelta(days=1)
    return datetime.combine(parsed_date, time.min)


async def _load_records(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    start_at: datetime | None,
    end_before: datetime | None,
    collection_status: str | None,
) -> list[CollectionRecord]:
    async with session_factory() as db:
        query = select(CollectionRecord).order_by(CollectionRecord.created_at.asc())
        if start_at is not None:
            query = query.where(CollectionRecord.created_at >= start_at)
        if end_before is not None:
            query = query.where(CollectionRecord.created_at < end_before)
        if collection_status is not None:
            query = query.where(CollectionRecord.collection_status == collection_status)

        result = await db.execute(query)
        return list(result.scalars().all())


def _build_summary(records: list[CollectionRecord]) -> dict[str, object]:
    summary: dict[str, object] = {
        "total_count": 0,
        "total_duration": 0,
        "months": {},
    }
    months = _ensure_dict(summary["months"])

    for record in records:
        created_at = record.created_at
        month_key = created_at.strftime("%Y-%m")
        day_key = created_at.strftime("%Y-%m-%d")
        username = record.user_name or f"user_id:{record.user_id}"
        duration = int(record.duration or 0)

        summary["total_count"] = _as_int(summary["total_count"]) + 1
        summary["total_duration"] = _as_int(summary["total_duration"]) + duration

        month = _ensure_month(months, month_key)
        month["count"] = _as_int(month["count"]) + 1
        month["duration"] = _as_int(month["duration"]) + duration

        days = _ensure_dict(month["days"])
        day = _ensure_day(days, day_key)
        day["count"] = _as_int(day["count"]) + 1
        day["duration"] = _as_int(day["duration"]) + duration

        usernames = _ensure_dict(day["usernames"])
        user_summary = _ensure_user(usernames, username)
        user_summary["count"] = _as_int(user_summary["count"]) + 1
        user_summary["duration"] = _as_int(user_summary["duration"]) + duration

    return summary


def _ensure_month(months: dict[str, object], month_key: str) -> dict[str, object]:
    existing = months.get(month_key)
    if isinstance(existing, dict):
        return cast(dict[str, object], existing)
    month: dict[str, object] = {"count": 0, "duration": 0, "days": {}}
    months[month_key] = month
    return month


def _ensure_day(days: dict[str, object], day_key: str) -> dict[str, object]:
    existing = days.get(day_key)
    if isinstance(existing, dict):
        return cast(dict[str, object], existing)
    day: dict[str, object] = {"count": 0, "duration": 0, "usernames": {}}
    days[day_key] = day
    return day


def _ensure_user(usernames: dict[str, object], username: str) -> dict[str, object]:
    existing = usernames.get(username)
    if isinstance(existing, dict):
        return cast(dict[str, object], existing)
    user_summary: dict[str, object] = {"count": 0, "duration": 0}
    usernames[username] = user_summary
    return user_summary


def _ensure_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def _as_int(value: object) -> int:
    return value if isinstance(value, int) else 0


async def _run(args: argparse.Namespace) -> int:
    if args.start and not args.end:
        print("传入 --start 时必须同时传入 --end")
        return 2
    if args.end and not args.start:
        print("传入 --end 时必须同时传入 --start")
        return 2

    start_at = _parse_date_or_datetime(args.start, is_end=False) if args.start else None
    end_before = _parse_date_or_datetime(args.end, is_end=True) if args.end else None
    if start_at is not None and end_before is not None and start_at >= end_before:
        print("--start 必须早于 --end")
        return 2

    settings = get_settings(env_name=args.env, robot_name=args.robot)
    session_factory = await init_database(settings.database)
    records = await _load_records(
        session_factory,
        start_at=start_at,
        end_before=end_before,
        collection_status=args.status,
    )
    pprint.pp(_build_summary(records), sort_dicts=True)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按月份、日期、用户名统计 collection_records 条数与时长"
    )
    parser.add_argument("--robot", help="机器人名称；不传则读取 PERCEPT_ROBOT/APP_ROBOT")
    parser.add_argument("--env", help="运行环境 test/prod；不传则读取 PERCEPT_ENV/APP_ENV")
    parser.add_argument("--start", help="可选：开始日期，包含；格式 YYYY-MM-DD 或 ISO datetime")
    parser.add_argument("--end", help="可选：结束日期，包含整天；格式 YYYY-MM-DD 或 ISO datetime")
    parser.add_argument("--status", help="可选：按 collection_status 过滤，例如 completed")
    args = parser.parse_args()

    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
