"""Shared naming rules for materialized collection outputs."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

_FILENAME_PREFIX_RE = re.compile(r"^file_\d{14}_[0-9a-f]{6}$")
_CAMERA_PREFIX = "camera_"
_VIDEO_SUFFIXES = {"rgb", "depth"}


def build_filename_prefix(start_time: datetime, uuid_hex: str | None = None) -> str:
    """Build file_{YYYYMMDDHHMMSS}_{uuid6}."""
    timestamp = start_time.strftime("%Y%m%d%H%M%S")
    suffix = (uuid_hex or uuid.uuid4().hex).replace("-", "")[:6].lower()
    if len(suffix) != 6 or not re.fullmatch(r"[0-9a-f]{6}", suffix):
        raise ValueError(f"uuid suffix must contain at least 6 hex chars: {uuid_hex}")
    return f"file_{timestamp}_{suffix}"


def is_valid_filename_prefix(value: str) -> bool:
    return bool(_FILENAME_PREFIX_RE.fullmatch(value.strip()))


def require_valid_filename_prefix(value: str) -> str:
    prefix = value.strip()
    if not is_valid_filename_prefix(prefix):
        raise ValueError(f"filename_prefix must match file_YYYYMMDDHHMMSS_uuid6: {value}")
    return prefix


def camera_component_to_view_base(component_id: str) -> str:
    if not component_id.startswith(_CAMERA_PREFIX):
        raise ValueError(f"camera component_id must start with camera_: {component_id}")
    view_base = component_id[len(_CAMERA_PREFIX) :].strip("_")
    if not view_base:
        raise ValueError(f"camera component_id missing view name: {component_id}")
    return view_base


def build_video_filename(filename_prefix: str, component_id: str, stream_type: str) -> str:
    if stream_type not in _VIDEO_SUFFIXES:
        raise ValueError(f"stream_type must be rgb or depth: {stream_type}")
    prefix = require_valid_filename_prefix(filename_prefix)
    view_base = camera_component_to_view_base(component_id)
    return f"{prefix}_{view_base}_{stream_type}.mp4"


def extract_materialized_video_view(json_stem: str, file_name: str) -> str | None:
    prefix = f"{json_stem}_"
    suffix = ".mp4"
    if not file_name.startswith(prefix) or not file_name.endswith(suffix):
        return None

    view_name = file_name[len(prefix) : -len(suffix)]
    if not (view_name.endswith("_rgb") or view_name.endswith("_depth")):
        return None
    return view_name or None
