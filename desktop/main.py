"""Desktop 统一入口。

机型通过 PERCEPT_ROBOT / APP_ROBOT 环境变量或 --robot 参数指定；
环境通过 PERCEPT_ENV / APP_ENV 或 --env 指定。
"""

from __future__ import annotations

import argparse
import os
import sys

from desktop.app import run
from desktop.profiles import load_profile


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Percept Edge Runtime Console")
    parser.add_argument(
        "--robot",
        default=None,
        help="机型名称（如 robot-cr4c / robot-w1）；缺省读取 PERCEPT_ROBOT / APP_ROBOT",
    )
    parser.add_argument(
        "--env",
        default=None,
        help="运行环境（test/prod）；缺省读取 PERCEPT_ENV / APP_ENV，否则 test",
    )
    parser.add_argument(
        "--mode",
        default=None,
        choices=("bilateral", "vr"),
        help="启动方式：bilateral=同构臂，vr=VR；缺省读取 PERCEPT_LAUNCH_MODE 或机型默认值",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    env = args.env or os.getenv("PERCEPT_ENV") or os.getenv("APP_ENV") or "test"
    mode = args.mode or os.getenv("PERCEPT_LAUNCH_MODE")
    if mode is not None:
        profile = load_profile(args.robot)
        if mode not in profile.launch_modes:
            raise SystemExit(
                f"不支持的启动模式: {mode}，{profile.robot_name} 仅支持 "
                f"{', '.join(profile.launch_modes)}"
            )
    return run(profile_name=args.robot, environment=env, launch_mode=mode)


if __name__ == "__main__":
    raise SystemExit(main())
