# -*- coding: utf-8 -*-
# main.py
"""应用启动脚本"""

import argparse
import os
from typing import Optional

import uvicorn

from src.config import get_settings


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 Percept Edge API")
    parser.add_argument("--robot", help="指定机器人标识")
    parser.add_argument("--env", choices=["test", "prod"], help="指定运行环境")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None):
    """启动应用服务器"""
    args = parse_args(argv)
    if args.robot:
        os.environ["PERCEPT_ROBOT"] = args.robot
    if args.env:
        os.environ["PERCEPT_ENV"] = args.env

    settings = get_settings(args.env, args.robot)

    uvicorn.run(
        "src.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.server.debug,
    )


if __name__ == "__main__":
    main()
