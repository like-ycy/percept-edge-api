"""数据库状态值枚举。"""

from enum import Enum


class TaskStatus(str, Enum):
    """任务主状态。"""

    PENDING = "pending"
    RUNNING = "run"
    COMPLETED = "completed"
    STOPPED = "stop"
    PAUSED = "paused"
    UNRELEASED = "unrelease"


class TaskLocalStatus(str, Enum):
    """任务本地状态。"""

    IDLE = "idle"


class CollectionRecordStatus(str, Enum):
    """采集记录持久化状态。"""

    COLLECTING = "collecting"
    FINALIZING = "finalizing"
    VALIDATING = "validating"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FINALIZE_FAILED = "finalize_failed"
    VALIDATION_FAILED = "validation_failed"


class ValidationStatus(str, Enum):
    """采集记录完整性校验状态。"""

    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class UploadStatus(str, Enum):
    """采集记录上传状态。"""

    PENDING = "pending"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class CloudNotifyStatus(str, Enum):
    """上传完成后的云端通知状态。"""

    PENDING = "pending"
    NOTIFYING = "notifying"
    COMPLETED = "completed"
    FAILED = "failed"
