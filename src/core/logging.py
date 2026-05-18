# src/core/logging.py
"""日志配置模块"""

from pathlib import Path

from loguru import logger

# 确保日志目录存在
log_dir = Path(__file__).parent.parent.parent / "logs"
log_dir.mkdir(parents=True, exist_ok=True)

logger.add(
    log_dir / "app.log",
    rotation="10 MB",
    retention="7 days",
    level="WARNING",
    encoding="utf-8",
)
