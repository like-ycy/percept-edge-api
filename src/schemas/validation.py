# src/schemas/validation.py
"""数据完整性校验相关 Schema"""

from pydantic import BaseModel

from src.schemas.status import ValidationStatus


ValidationStatusEnum = ValidationStatus


class FileValidationError(BaseModel):
    """单个文件校验错误"""

    file_name: str
    error_type: str  # "missing" | "extra" | "frame_drop_exceeded" | "ffprobe_error"
    expected: int | str | None = None
    actual: int | str | None = None
    message: str


class ValidationResult(BaseModel):
    """校验结果"""

    status: ValidationStatusEnum
    directory: str
    expected_steps: int
    expected_files: list[str]
    found_files: list[str]
    missing_files: list[str]
    extra_files: list[str]
    errors: list[FileValidationError]
    summary: str
