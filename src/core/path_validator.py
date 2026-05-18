# src/core/path_validator.py
"""路径验证和清洗工具

防止路径遍历攻击和命令注入风险。
"""

import re
from pathlib import Path


class PathValidationError(ValueError):
    """路径验证失败异常"""

    pass


def validate_safe_path(
    path: str | Path,
    allowed_base: str | Path | None = None,
    allow_relative: bool = False,
) -> Path:
    """验证并清洗路径

    Args:
        path: 待验证的路径
        allowed_base: 允许的基础目录（如果指定，路径必须在此目录下）
        allow_relative: 是否允许相对路径

    Returns:
        规范化后的 Path 对象

    Raises:
        PathValidationError: 路径验证失败
    """
    if not path:
        raise PathValidationError("路径不能为空")

    path_obj = Path(path)

    # 检查是否为绝对路径
    if not allow_relative and not path_obj.is_absolute():
        raise PathValidationError(f"路径必须是绝对路径: {path}")

    # 规范化路径（解析 .. 和 .）
    try:
        resolved = path_obj.resolve()
    except (OSError, RuntimeError) as e:
        raise PathValidationError(f"路径解析失败: {path}, {e}")

    # 检查路径遍历攻击
    if allowed_base:
        base = Path(allowed_base).resolve()
        try:
            resolved.relative_to(base)
        except ValueError:
            raise PathValidationError(f"路径 {resolved} 不在允许的目录 {base} 下")

    # 检查危险字符（命令注入相关）
    path_str = str(resolved)
    dangerous_patterns = [
        r"[`$]",  # Shell 变量和命令替换
        r"[;&|]",  # 命令分隔符
        r"[<>]",  # 重定向
        r"\n|\r",  # 换行符
        r"\x00",  # 空字节
    ]
    for pattern in dangerous_patterns:
        if re.search(pattern, path_str):
            raise PathValidationError(f"路径包含危险字符: {path}")

    return resolved


def sanitize_filename(filename: str) -> str:
    """清洗文件名，移除危险字符

    Args:
        filename: 原始文件名

    Returns:
        清洗后的文件名
    """
    if not filename:
        raise PathValidationError("文件名不能为空")

    # 移除路径分隔符
    filename = filename.replace("/", "_").replace("\\", "_")

    # 移除危险字符
    filename = re.sub(r"[`$;&|<>\n\r\x00]", "", filename)

    # 移除前导点（防止隐藏文件）
    filename = filename.lstrip(".")

    if not filename:
        raise PathValidationError("清洗后文件名为空")

    return filename
