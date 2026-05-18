# src/services/heartbeat_client.py
"""心跳客户端 - 定期向云端报告在线状态"""

import asyncio
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path

import httpx
from loguru import logger

from src.config import Settings


class HeartbeatClient:
    """心跳客户端"""

    _IMMEDIATE_HEARTBEAT_DEBOUNCE_SECONDS = 5

    def __init__(self, settings: Settings):
        self.cloud_url = settings.cloud.base_url
        self.device_id = ""
        self.interval = settings.heartbeat.interval
        self.is_activated = False
        self._running = False
        self._task: asyncio.Task | None = None
        # 跨 worker 协调文件：同一实例共享一个心跳发送槽位
        coordinator_seed = f"{settings.database.path}|{settings.cloud.base_url}"
        coordinator_hash = hashlib.sha1(coordinator_seed.encode("utf-8")).hexdigest()[:12]
        self._coordination_file = Path("/tmp") / f"percept_edge_heartbeat_{coordinator_hash}.json"

    def update_device_status(self, is_activated: bool, device_id: str | None) -> None:
        """更新设备激活状态和云端 UID"""
        self.is_activated = is_activated
        self.device_id = (device_id or "").strip()

    async def send_heartbeat_now(self) -> None:
        """立即尝试发送一次心跳，不占用周期心跳的发送槽位。"""
        await self._send_heartbeat(use_periodic_slot=False)

    async def start(self) -> None:
        """启动心跳任务"""
        if self._running and self._task and not self._task.done():
            logger.warning("心跳服务已在运行，忽略重复启动")
            return
        self._running = True
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"心跳服务已启动，设备ID: {self.device_id}，间隔: {self.interval}秒")

    async def stop(self) -> None:
        """停止心跳任务"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None
        logger.info("心跳服务已停止")

    async def _heartbeat_loop(self) -> None:
        """心跳循环"""
        while self._running:
            await self._send_heartbeat()
            await asyncio.sleep(self.interval)

    async def _send_heartbeat(self, *, use_periodic_slot: bool = True) -> None:
        """发送单次心跳"""
        if not self.is_activated or not self.device_id:
            logger.debug("设备未激活或 device_id 缺失，跳过心跳发送")
            return
        if use_periodic_slot:
            if not self._claim_periodic_slot():
                logger.debug("当前心跳周期已由其他 worker 处理，跳过本轮发送")
                return
        elif not self._claim_immediate_slot():
            logger.debug("激活后的立即心跳已在短时间内发送过，跳过本轮补发")
            return

        for attempt in (1, 2):
            if await self._post_heartbeat_once():
                return
            if attempt == 1:
                logger.warning("心跳发送失败，将重试 1 次")
                await asyncio.sleep(1)

    async def _post_heartbeat_once(self) -> bool:
        """发送一次心跳请求，返回是否成功"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{self.cloud_url}/equipment/heartbeat",
                    json={"device_uid": self.device_id},
                )
                if response.status_code == 200:
                    payload = response.json()
                    if payload.get("code") == 200:
                        logger.debug(f"心跳发送成功: {self.device_id}")
                        return True
                    logger.warning(f"心跳响应异常: {payload.get('msg', '未知错误')}")
                    return False
                logger.warning(f"心跳响应异常: {response.status_code}")
                return False
        except httpx.TimeoutException:
            logger.warning("心跳发送超时")
            return False
        except httpx.ConnectError:
            logger.warning("心跳发送失败: 无法连接云端")
            return False
        except Exception as e:
            logger.error(f"心跳发送失败: {e}")
            return False

    def _claim_periodic_slot(self) -> bool:
        """跨进程抢占周期心跳槽位，确保同一周期只发送一次。"""
        now = time.time()
        return self._claim_slot("periodic_next_allowed_at", now + self.interval)

    def _claim_immediate_slot(self) -> bool:
        """跨进程抢占激活后立即心跳槽位，避免短时间内重复补发。"""
        now = time.time()
        return self._claim_slot(
            "immediate_next_allowed_at",
            now + self._IMMEDIATE_HEARTBEAT_DEBOUNCE_SECONDS,
        )

    def _claim_slot(self, key: str, next_allowed_at: float) -> bool:
        """跨进程抢占发送槽位。"""
        now = time.time()

        self._coordination_file.parent.mkdir(parents=True, exist_ok=True)
        with self._coordination_file.open("a+", encoding="utf-8") as state_file:
            fcntl.flock(state_file.fileno(), fcntl.LOCK_EX)
            state_file.seek(0)
            raw = state_file.read().strip()

            claimed_until = 0.0
            if raw:
                try:
                    state = json.loads(raw)
                    claimed_until = float(state.get(key, 0.0))
                except (ValueError, TypeError):
                    claimed_until = 0.0

            if now < claimed_until:
                return False

            next_state = {}
            if raw:
                try:
                    next_state = json.loads(raw)
                except (ValueError, TypeError):
                    next_state = {}

            next_state[key] = next_allowed_at
            state_file.seek(0)
            state_file.truncate()
            json.dump(next_state, state_file)
            state_file.flush()
            os.fsync(state_file.fileno())
            return True
