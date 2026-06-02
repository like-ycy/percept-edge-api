#!/usr/bin/env python3
"""持续监控 robot_os command monitor 链路。"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_default_command_endpoint() -> str:
    sys.path.append(str(_repo_root()))
    from src.config import get_settings

    return get_settings().zeromq.command_endpoint


def _read_positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default

    value = float(raw)
    if value <= 0:
        msg = f"{name} 必须大于 0"
        raise ValueError(msg)
    return value


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default

    value = int(raw)
    if value <= 0:
        msg = f"{name} 必须大于 0"
        raise ValueError(msg)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="持续监控 robot_os command monitor 链路")
    parser.add_argument(
        "--command-endpoint",
        default=None,
        help="ZeroMQ command REQ/REP 端点",
    )
    parser.add_argument(
        "--monitor-timeout",
        type=float,
        default=None,
        help="单次 monitor 超时（秒）",
    )
    parser.add_argument("--watch-interval", type=float, default=None, help="监控间隔（秒）")
    parser.add_argument(
        "--failure-threshold",
        type=int,
        default=None,
        help="连续失败达到该次数后标记链路异常",
    )
    parser.add_argument("--quiet", action="store_true", help="静默模式，仅返回退出码")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)

    args.command_endpoint = (
        args.command_endpoint
        or os.getenv("ROBOT_OS_COMMAND_ENDPOINT")
        or _load_default_command_endpoint()
    )
    args.monitor_timeout = args.monitor_timeout or _read_positive_float_env(
        "ROBOT_OS_MONITOR_TIMEOUT", 2.0
    )
    args.watch_interval = args.watch_interval or _read_positive_float_env(
        "ROBOT_OS_WATCH_INTERVAL", 2.0
    )
    args.failure_threshold = args.failure_threshold or _read_positive_int_env(
        "ROBOT_OS_WATCH_FAILURE_THRESHOLD", 3
    )
    return args


class StopSignal:
    def __init__(self) -> None:
        self.received = False

    def mark_received(self, _signum: int, _frame: object) -> None:
        self.received = True


def watch_link(
    *,
    command_endpoint: str,
    monitor_timeout: float,
    watch_interval: float,
    failure_threshold: int,
    quiet: bool,
    stop_signal: StopSignal,
) -> int:
    root_str = str(_repo_root())
    if root_str not in sys.path:
        sys.path.append(root_str)
    from scripts.debug.wait_robot_os_ready import probe_monitor

    consecutive_failures = 0
    link_down = False
    if not quiet:
        print(f"[watch_robot_os_link] started: {command_endpoint}", flush=True)

    while not stop_signal.received:
        ready = probe_monitor(command_endpoint, monitor_timeout)
        if ready:
            if link_down and not quiet:
                print(f"[watch_robot_os_link] recovered: {command_endpoint}", flush=True)
            consecutive_failures = 0
            link_down = False
        else:
            consecutive_failures += 1
            if consecutive_failures >= failure_threshold and not link_down:
                link_down = True
                if not quiet:
                    print(
                        (
                            "[watch_robot_os_link] link down: "
                            f"endpoint={command_endpoint} failures={consecutive_failures}"
                        ),
                        file=sys.stderr,
                        flush=True,
                    )

        deadline = time.monotonic() + watch_interval
        while not stop_signal.received and time.monotonic() < deadline:
            time.sleep(min(0.2, deadline - time.monotonic()))

    if not quiet:
        print("[watch_robot_os_link] stopped", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except ValueError as exc:
        print(f"[watch_robot_os_link] 参数错误: {exc}", file=sys.stderr)
        return 2

    stop_signal = StopSignal()
    signal.signal(signal.SIGINT, stop_signal.mark_received)
    signal.signal(signal.SIGTERM, stop_signal.mark_received)

    return watch_link(
        command_endpoint=args.command_endpoint,
        monitor_timeout=args.monitor_timeout,
        watch_interval=args.watch_interval,
        failure_threshold=args.failure_threshold,
        quiet=args.quiet,
        stop_signal=stop_signal,
    )


if __name__ == "__main__":
    raise SystemExit(main())
