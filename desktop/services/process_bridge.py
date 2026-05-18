class ProcessLineBuffer:
    """增量行缓冲：接受字节流片段，按 \\n / \\r 切行输出完整行。未结束的片段留存直到 flush。"""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> list[str]:
        self._buffer += chunk
        parts = self._buffer.splitlines(keepends=True)
        lines: list[str] = []

        for part in parts:
            if part.endswith(("\n", "\r")):
                lines.append(part.rstrip("\r\n"))
            else:
                self._buffer = part
                break
        else:
            self._buffer = ""

        return lines

    def flush(self) -> list[str]:
        if not self._buffer:
            return []
        remaining = self._buffer
        self._buffer = ""
        return [remaining]
