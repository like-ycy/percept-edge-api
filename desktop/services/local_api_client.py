"""本地 API JSON 客户端。"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


class LocalApiClient:
    """向本机 API 发送 JSON 请求。"""

    def __init__(self, root_url: str, timeout: float = 5.0) -> None:
        self._root_url = root_url.rstrip("/") + "/"
        self._timeout = timeout

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = urljoin(self._root_url, path.lstrip("/"))
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            raise RuntimeError(f"本地 API 请求失败: HTTP {exc.code} {url}") from exc
        except URLError as exc:
            raise RuntimeError(f"本地 API 请求失败: {url}") from exc

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"本地 API 响应不是合法 JSON: {url}") from exc

        if not isinstance(decoded, dict):
            raise RuntimeError(f"本地 API 响应必须是 JSON object: {url}")
        return decoded
