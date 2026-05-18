# src/core/sync_throttle.py
"""同步节流器"""

from datetime import datetime, timedelta, timezone


class SyncThrottle:
    """按用户独立的同步节流器"""

    def __init__(self, interval_seconds: int = 300):
        """初始化节流器

        Args:
            interval_seconds: 同步间隔（秒），默认 5 分钟
        """
        self.interval = timedelta(seconds=interval_seconds)
        self._last_sync: dict[int, datetime] = {}

    def should_sync(self, user_id: int) -> bool:
        """检查指定用户是否应该同步

        Args:
            user_id: 用户 ID

        Returns:
            是否应该同步
        """
        last = self._last_sync.get(user_id)
        if last is None:
            return True
        return datetime.now(timezone.utc) - last > self.interval

    def mark_synced(self, user_id: int) -> None:
        """标记指定用户已同步

        Args:
            user_id: 用户 ID
        """
        self._last_sync[user_id] = datetime.now(timezone.utc)

    def get_last_sync(self, user_id: int) -> datetime | None:
        """获取指定用户的最后同步时间

        Args:
            user_id: 用户 ID

        Returns:
            最后同步时间，未同步过返回 None
        """
        return self._last_sync.get(user_id)
