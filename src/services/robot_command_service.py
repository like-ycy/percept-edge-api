from __future__ import annotations

import asyncio
from typing import Any

import msgpack
import zmq
import zmq.asyncio

from src.core.exceptions import BusinessError, ExternalServiceError


class RobotCommandService:
    def __init__(self, command_endpoint: str, context: zmq.asyncio.Context | None = None) -> None:
        self._command_endpoint = command_endpoint
        self._context = context or zmq.asyncio.Context.instance()

    def _get_socket(self):
        socket = self._context.socket(zmq.REQ)
        socket.setsockopt(zmq.LINGER, 0)
        socket.setsockopt(zmq.RCVTIMEO, 5000)
        socket.setsockopt(zmq.SNDTIMEO, 1000)
        socket.connect(self._command_endpoint)
        return socket

    async def _send_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        socket = self._get_socket()
        try:
            await socket.send(msgpack.packb(payload, use_bin_type=True))
            raw = await asyncio.wait_for(socket.recv(), timeout=5.0)
        except (TimeoutError, asyncio.TimeoutError, zmq.error.Again) as exc:
            raise ExternalServiceError("RobotOS Command", "command timeout") from exc
        except Exception as exc:
            raise ExternalServiceError("RobotOS Command", str(exc)) from exc
        finally:
            socket.close()

        try:
            response = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise ExternalServiceError("RobotOS Command", "invalid response") from exc

        if not isinstance(response, dict) or "success" not in response or "data" not in response:
            raise ExternalServiceError("RobotOS Command", "invalid response")
        if not response.get("success"):
            raise BusinessError(str(response.get("message") or "命令执行失败"))
        data = response.get("data")
        if not isinstance(data, dict):
            raise ExternalServiceError("RobotOS Command", "invalid response")
        return data

    async def query_monitor(self) -> dict[str, Any]:
        return await self._send_request({"cmd": "monitor", "params": {}})

    async def execute_command(
        self,
        component_id: str,
        action: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "cmd": "command_execute",
            "params": {
                "component_id": component_id,
                "action": action,
                "args": args or {},
            },
        }
        return await self._send_request(payload)
