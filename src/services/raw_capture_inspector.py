"""原始 raw spool (.capture) 数据查看工具

读取 manifest 与采样若干 segment 帧，统计摄像头与帧数。
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path

from src.schemas.collection import RawCaptureInfo
from src.services.zeromq_consumer import parse_zmq_message

_RECORD_MAGIC = b"PFRM"
_HEADER_STRUCT = struct.Struct("<4sH I Q")
_DEFAULT_SAMPLE_FRAMES = 20


def _iter_payloads(segment_paths: list[Path], max_frames: int):
    """按 segment 顺序 yield payload bytes，直到读取够 max_frames 帧

    单个 segment 损坏时跳到下一个 segment，而不是整体终止迭代。
    """
    yielded = 0
    for seg in segment_paths:
        if yielded >= max_frames:
            return
        try:
            with open(seg, "rb") as fh:
                while yielded < max_frames:
                    header = fh.read(_HEADER_STRUCT.size)
                    if not header:
                        break
                    if len(header) != _HEADER_STRUCT.size:
                        break
                    magic, version, payload_len, _ = _HEADER_STRUCT.unpack(header)
                    if magic != _RECORD_MAGIC or version != 1:
                        break
                    payload = fh.read(payload_len)
                    if len(payload) != payload_len:
                        break
                    yield payload
                    yielded += 1
        except OSError:
            continue


def _inspect_blocking(capture_dir: Path, sample_frames: int) -> tuple[dict, list[str], int]:
    manifest_path = capture_dir / "manifest.json"
    manifest_raw = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_raw)

    segment_names = manifest.get("segments") or []
    segment_paths: list[Path] = []
    for name in segment_names:
        candidate = capture_dir / name
        if candidate.exists():
            segment_paths.append(candidate)
    if not segment_paths:
        for candidate in sorted(capture_dir.glob("segment-*.bin")):
            segment_paths.append(candidate)

    cameras: list[str] = []
    seen: set[str] = set()
    sampled = 0
    for payload in _iter_payloads(segment_paths, sample_frames):
        sampled += 1
        try:
            frame = parse_zmq_message(payload)
        except Exception:
            continue
        for cam in frame.cameras:
            if cam.component_id not in seen:
                seen.add(cam.component_id)
                cameras.append(cam.component_id)

    return manifest, cameras, sampled


async def inspect_raw_capture(
    *,
    record_id: int,
    output_dir: str | None,
    capture_dir: Path,
    sample_frames: int = _DEFAULT_SAMPLE_FRAMES,
) -> RawCaptureInfo:
    """读取 .capture 目录信息（异步包装阻塞 IO）"""
    manifest, cameras, sampled = await asyncio.to_thread(
        _inspect_blocking, capture_dir, sample_frames
    )
    sealed = (capture_dir / "SEALED").exists()
    segments = manifest.get("segments") or []
    return RawCaptureInfo(
        record_id=record_id,
        output_dir=output_dir,
        capture_dir=str(capture_dir),
        sealed=sealed,
        frame_count=int(manifest.get("raw_frame_count") or 0),
        raw_bytes=int(manifest.get("raw_bytes") or 0),
        start_time=manifest.get("start_time"),
        end_time=manifest.get("end_time"),
        segment_count=len(segments),
        segments=list(segments),
        cameras=cameras,
        camera_count=len(cameras),
        sampled_frames=sampled,
    )
