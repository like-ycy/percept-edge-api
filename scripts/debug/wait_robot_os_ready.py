#!/usr/bin/env python3
"""等待 robot_os monitor 接口 ready。"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import msgpack
import zmq


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_default_rep_endpoint() -> str:
    sys.path.append(str(_repo_root()))
    from src.config import get_settings

    return get_settings().zeromq.rep_endpoint


def _read_positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default

    value = float(raw)
    if value <= 0:
        msg = f"{name} 必须大于 0"
        raise ValueError(msg)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="等待 robot_os monitor 接口 ready")
    parser.add_argument("--rep-endpoint", default=None, help="ZeroMQ monitor REP 端点")
    parser.add_argument("--ready-timeout", type=float, default=None, help="总等待超时（秒）")
    parser.add_argument(
        "--monitor-timeout", type=float, default=None, help="单次 monitor 超时（秒）"
    )
    parser.add_argument("--probe-interval", type=float, default=None, help="探测间隔（秒）")
    parser.add_argument("--quiet", action="store_true", help="静默模式，仅返回退出码")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)

    args.rep_endpoint = (
        args.rep_endpoint or os.getenv("ROBOT_OS_REP_ENDPOINT") or _load_default_rep_endpoint()
    )
    args.ready_timeout = args.ready_timeout or _read_positive_float_env(
        "ROBOT_OS_READY_TIMEOUT", 30.0
    )
    args.monitor_timeout = args.monitor_timeout or _read_positive_float_env(
        "ROBOT_OS_MONITOR_TIMEOUT", 2.0
    )
    args.probe_interval = args.probe_interval or _read_positive_float_env(
        "ROBOT_OS_PROBE_INTERVAL", 1.0
    )
    return args


def probe_monitor(rep_endpoint: str, timeout: float) -> bool:
    context = zmq.Context()
    socket = context.socket(zmq.REQ)
    socket.setsockopt(zmq.LINGER, 0)
    poller = zmq.Poller()
    poller.register(socket, zmq.POLLIN)

    try:
        socket.connect(rep_endpoint)
        socket.send(msgpack.packb({"cmd": "monitor"}))
        events = dict(poller.poll(int(timeout * 1000)))
        if socket not in events:
            return False

        payload = msgpack.unpackb(socket.recv(), raw=False)
        if not payload.get("success"):
            return False

        data = payload.get("data")
        return isinstance(data, dict) and bool(data) and "system" in data and "robot" in data
    except Exception:
        return False
    finally:
        socket.close()
        context.term()


def wait_until_ready(
    rep_endpoint: str,
    ready_timeout: float,
    monitor_timeout: float,
    probe_interval: float,
) -> bool:
    deadline = time.monotonic() + ready_timeout
    while time.monotonic() < deadline:
        if probe_monitor(rep_endpoint, monitor_timeout):
            return True

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(probe_interval, remaining))
    return False


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except ValueError as exc:
        print(f"[wait_robot_os_ready] 参数错误: {exc}", file=sys.stderr)
        return 2

    ready = wait_until_ready(
        rep_endpoint=args.rep_endpoint,
        ready_timeout=args.ready_timeout,
        monitor_timeout=args.monitor_timeout,
        probe_interval=args.probe_interval,
    )

    if ready:
        if not args.quiet:
            print(f"[wait_robot_os_ready] ready: {args.rep_endpoint}")
        return 0

    if not args.quiet:
        print(
            (
                "[wait_robot_os_ready] timeout: "
                f"endpoint={args.rep_endpoint} ready_timeout={args.ready_timeout}s"
            ),
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
