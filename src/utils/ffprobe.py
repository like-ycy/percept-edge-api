# src/utils/ffprobe.py
"""FFprobe 工具函数"""

import json
import subprocess
from pathlib import Path


def ffprobe_frames(path: Path) -> int | None:
    """使用 ffprobe 获取视频帧数

    Args:
        path: 视频文件路径

    Returns:
        视频帧数，失败返回 None
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-count_frames",
        "-show_entries",
        "stream=nb_read_frames,nb_frames",
        "-of",
        "json",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        data = json.loads(out)
        streams = data.get("streams", [])
        if not streams:
            return None
        s = streams[0]
        val = s.get("nb_read_frames") or s.get("nb_frames")
        return int(val) if val is not None else None
    except Exception:
        return None
