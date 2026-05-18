# src/services/ffmpeg_writer.py
"""FFmpeg MJPEG 写入器"""

from __future__ import annotations

import atexit
import subprocess
import weakref
from pathlib import Path
from time import perf_counter

from loguru import logger

# 全局跟踪所有活跃的 FFmpeg 进程，用于异常退出时清理
_active_writers: weakref.WeakSet["FFmpegMJPEGWriter"] = weakref.WeakSet()


def _cleanup_all_writers():
    """清理所有活跃的 FFmpeg 写入器（atexit 回调）"""
    for writer in list(_active_writers):
        writer.terminate()


# 注册 atexit 回调，确保程序退出时清理子进程
atexit.register(_cleanup_all_writers)


class FFmpegMJPEGWriter:
    """使用 FFmpeg 直接写入 JPEG 字节到 MJPEG 容器

    零解码开销：直接将 JPEG 字节封装到 AVI 容器，无需解码/编码。
    """

    def __init__(self, output_path: str | Path, fps: int = 30):
        """初始化 FFmpeg 写入器

        Args:
            output_path: 输出文件路径
            fps: 帧率，默认 30
        """
        self.output_path = str(output_path)
        self.fps = fps
        self._process: subprocess.Popen | None = None
        self._frame_count = 0
        # 注册到全局跟踪集合
        _active_writers.add(self)

    def _ensure_started(self) -> None:
        """确保 FFmpeg 进程已启动"""
        if self._process is None:
            self._process = subprocess.Popen(
                [
                    "ffmpeg",
                    "-y",  # 覆盖输出文件
                    "-f",
                    "mjpeg",  # 输入格式：MJPEG 流
                    "-framerate",
                    str(self.fps),  # 帧率
                    "-i",
                    "-",  # 从 stdin 读取
                    "-c:v",
                    "copy",  # 直接复制，不重新编码
                    self.output_path,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def write(self, jpeg_bytes: bytes) -> None:
        """写入 JPEG 字节

        Args:
            jpeg_bytes: JPEG 图像字节数据
        """
        self._ensure_started()
        if self._process and self._process.stdin:
            self._process.stdin.write(jpeg_bytes)
            self._frame_count += 1

    def release(self) -> None:
        """关闭写入器并等待 FFmpeg 完成"""
        if self._process:
            release_started_at = perf_counter()
            if self._process.stdin:
                self._process.stdin.close()
            self._process.wait()
            logger.info(
                "FFmpeg 写入器已释放: output_path={}, frames={}, duration_ms={:.1f}",
                self.output_path,
                self._frame_count,
                (perf_counter() - release_started_at) * 1000,
            )
            self._process = None

    def terminate(self) -> None:
        """强制终止 FFmpeg 进程（用于异常退出时清理）"""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None

    def __del__(self):
        """析构函数，确保进程被清理"""
        self.terminate()

    @property
    def frame_count(self) -> int:
        """已写入的帧数"""
        return self._frame_count

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False
