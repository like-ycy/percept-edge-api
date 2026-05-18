"""上传完成后的云端通知"""

from datetime import datetime
from pathlib import Path

from loguru import logger

from src.models.database import CollectionRecord
from src.schemas.upload import CloudDataCreateRequest
from src.services.cloud_client import CloudClient


class UploadNotifier:
    """上传通知器"""

    def __init__(self, storage_base: Path, remote_path: str, cloud_client: CloudClient):
        self._storage_base = storage_base
        self._remote_path = remote_path
        self._cloud_client = cloud_client

    async def notify(
        self,
        record: CollectionRecord,
        upload_start_time: datetime,
        upload_end_time: datetime,
    ) -> int | None:
        """通知云端上传完成"""
        if record.task_id is None:
            logger.error(f"采集记录 {record.id} 缺少 task_id，跳过云端通知")
            return None

        relative_path = ""
        if record.output_dir:
            output_path = Path(record.output_dir)
            try:
                relative_path = str(output_path.relative_to(self._storage_base))
            except ValueError:
                relative_path = output_path.name
        remote_filepath = f"{self._remote_path}/{relative_path}" if relative_path else ""

        request_data = CloudDataCreateRequest(
            task_id=record.task_id,
            filepath=remote_filepath,
            collector=record.user_id,
            file_size=record.file_size or 0,
            file_time=record.duration or 0,
            upload_time=int(upload_start_time.timestamp()),
            end_time=int(upload_end_time.timestamp()),
        )

        return await self._cloud_client.create_data(request_data)
