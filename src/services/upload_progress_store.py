"""上传进度存储"""

from src.schemas.upload import UploadProgress, UploadStatus


class UploadProgressStore:
    """上传进度与取消状态存储"""

    def __init__(self) -> None:
        self._progress: dict[int, UploadProgress] = {}
        self._cancelled: set[int] = set()

    def start(self, record_id: int) -> UploadProgress:
        """初始化上传进度"""
        progress = UploadProgress(record_id=record_id, status=UploadStatus.UPLOADING)
        self._progress[record_id] = progress
        return progress

    def mark_retry(self, progress: UploadProgress, attempt_number: int) -> None:
        """标记重试次数"""
        progress.retry_count = attempt_number - 1
        if progress.retry_count > 0:
            progress.progress = 0

    def update_progress(self, record_id: int, progress_pct: int) -> None:
        """更新内存进度"""
        progress = self._progress.get(record_id)
        if progress:
            progress.progress = progress_pct

    def mark_completed(self, record_id: int) -> UploadProgress | None:
        """标记上传完成"""
        progress = self._progress.get(record_id)
        if progress:
            progress.status = UploadStatus.COMPLETED
            progress.progress = 100
        return progress

    def mark_failed(self, record_id: int) -> UploadProgress | None:
        """标记上传失败"""
        progress = self._progress.get(record_id)
        if progress:
            progress.status = UploadStatus.FAILED
        return progress

    def mark_interrupted(self, record_id: int) -> UploadProgress | None:
        """标记上传被中断（应用停止时）"""
        progress = self._progress.get(record_id)
        if progress:
            progress.status = UploadStatus.INTERRUPTED
            progress.error_message = "应用停止，上传中断"
        return progress

    def set_error_message(self, record_id: int, message: str) -> None:
        """设置错误信息"""
        progress = self._progress.get(record_id)
        if progress:
            progress.error_message = message

    def get(self, record_id: int) -> UploadProgress | None:
        """获取上传进度"""
        return self._progress.get(record_id)

    def cancel(self, record_id: int) -> None:
        """取消上传"""
        self._cancelled.add(record_id)

    def is_cancelled(self, record_id: int) -> bool:
        """检查是否已取消"""
        return record_id in self._cancelled

    def consume_cancelled(self, record_id: int) -> bool:
        """消费取消标记"""
        if record_id in self._cancelled:
            self._cancelled.discard(record_id)
            return True
        return False
