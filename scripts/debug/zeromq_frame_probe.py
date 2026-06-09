#!/usr/bin/env python3
"""ZeroMQ frame probe

Receive frames from ZeroMQ and log per-frame diagnostics:
- timestamp + delta
- camera set
- JPEG crc32 (color/depth)
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import zlib
from pathlib import Path
from typing import Any, Dict, Iterable

import msgpack
import zmq


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _camera_component_to_view_base(component_id: str) -> str:
    sys.path.append(str(_repo_root()))
    from src.services.collection_output_naming import camera_component_to_view_base

    return camera_component_to_view_base(component_id)


def _load_default_endpoint() -> str:
    sys.path.append(str(_repo_root()))
    from src.config import get_settings

    return get_settings().zeromq.endpoint


def _parse_bytes_field(value: Any) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value if value else None
    if isinstance(value, bytearray):
        return bytes(value) if value else None
    if isinstance(value, memoryview):
        return value.tobytes() if value else None
    if isinstance(value, str):
        if not value:
            return None
        if (value.startswith("b'") or value.startswith('b"')) and len(value) > 2:
            try:
                result = ast.literal_eval(value)
                return result if result else None
            except (ValueError, SyntaxError):
                pass
        return value.encode() if value else None
    return None


def _crc32(value: bytes | None) -> int | None:
    if not value:
        return None
    return zlib.crc32(value) & 0xFFFFFFFF


def _collect_camera_ids(frames: Iterable[dict]) -> list[str]:
    camera_ids = set()
    for frame in frames:
        frame_data = frame.get("data") or {}
        if "color_data" in frame_data or "depth_data" in frame_data:
            component_id = frame.get("component_id")
            if component_id:
                camera_ids.add(component_id)
    return sorted(camera_ids)


def _collect_camera_views(frames: Iterable[dict]) -> Dict[str, str]:
    camera_views: Dict[str, str] = {}
    for frame in frames:
        frame_data = frame.get("data") or {}
        if "color_data" not in frame_data and "depth_data" not in frame_data:
            continue
        component_id = frame.get("component_id")
        if not component_id:
            continue
        try:
            camera_views[component_id] = _camera_component_to_view_base(component_id)
        except ValueError:
            camera_views[component_id] = "invalid-camera-component"
    return camera_views


def _collect_crc32(frames: Iterable[dict]) -> Dict[str, Dict[str, int | None]]:
    crc_map: Dict[str, Dict[str, int | None]] = {}
    for frame in frames:
        frame_data = frame.get("data") or {}
        if "color_data" not in frame_data and "depth_data" not in frame_data:
            continue
        component_id = frame.get("component_id")
        if not component_id:
            continue
        color_bytes = _parse_bytes_field(frame_data.get("color_data"))
        depth_bytes = _parse_bytes_field(frame_data.get("depth_data"))
        crc_map[component_id] = {
            "color": _crc32(color_bytes),
            "depth": _crc32(depth_bytes),
        }
    return crc_map


def _diff_crc32(
    current: Dict[str, Dict[str, int | None]],
    previous: Dict[str, Dict[str, int | None]] | None,
) -> Dict[str, Dict[str, bool]]:
    if previous is None:
        return {cam: {"color": True, "depth": True} for cam in current}
    diff: Dict[str, Dict[str, bool]] = {}
    for cam, crc_pair in current.items():
        prev_pair = previous.get(cam, {})
        diff[cam] = {
            "color": crc_pair.get("color") != prev_pair.get("color"),
            "depth": crc_pair.get("depth") != prev_pair.get("depth"),
        }
    return diff


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ZeroMQ frame probe")
    parser.add_argument(
        "--endpoint",
        default=None,
        help="ZeroMQ endpoint（默认从当前选中的配置文件读取）",
    )
    parser.add_argument(
        "--show-raw-timestamp",
        action="store_true",
        help="Include per-frame raw timestamp from each subframe",
    )
    return parser.parse_args()


def _collect_raw_timestamps(frames: Iterable[dict]) -> Dict[str, float | None]:
    raw_ts: Dict[str, float | None] = {}
    for frame in frames:
        component_id = frame.get("component_id")
        if component_id:
            raw_ts[component_id] = frame.get("timestamp")
    return raw_ts


def main() -> None:
    args = _parse_args()
    endpoint = args.endpoint or _load_default_endpoint()

    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.setsockopt(zmq.RCVHWM, 1)
    socket.connect(endpoint)
    socket.setsockopt_string(zmq.SUBSCRIBE, "")

    last_timestamp: float | None = None
    last_camera_set: list[str] | None = None
    last_crc_map: Dict[str, Dict[str, int | None]] | None = None
    seq = 0

    try:
        while True:
            raw = socket.recv()
            data = msgpack.unpackb(raw, raw=False)
            frames = data.get("frames") or []
            timestamp = data.get("timestamp")

            camera_set = _collect_camera_ids(frames)
            crc_map = _collect_crc32(frames)
            crc_changed = _diff_crc32(crc_map, last_crc_map)

            timestamp_changed = last_timestamp is None or timestamp != last_timestamp
            delta_ts = None if last_timestamp is None else timestamp - last_timestamp
            camera_set_changed = last_camera_set is None or camera_set != last_camera_set

            payload: Dict[str, Any] = {
                "seq": seq,
                "timestamp": timestamp,
                "delta_ts": delta_ts,
                "timestamp_changed": timestamp_changed,
                "camera_set": camera_set,
                "camera_views": _collect_camera_views(frames),
                "camera_set_changed": camera_set_changed,
                "crc32": crc_map,
                "crc32_changed": crc_changed,
            }
            if args.show_raw_timestamp:
                payload["frame_timestamps"] = _collect_raw_timestamps(frames)

            print(json.dumps(payload, ensure_ascii=True))

            last_timestamp = timestamp
            last_camera_set = camera_set
            last_crc_map = crc_map
            seq += 1

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        socket.close()
        context.term()


if __name__ == "__main__":
    main()
