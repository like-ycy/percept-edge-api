"""上传完成后的云端通知"""

from datetime import datetime
from pathlib import Path

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
        *,
        token: str | None = None,
    ) -> int | None:
        """通知云端上传完成。

        成功时返回 cloud_id，失败时抛出异常供上层捕获并写入数据库。
        """
        if record.task_id is None:
            raise ValueError(f"采集记录 {record.id} 缺少 task_id")
        if not record.output_dir:
            raise ValueError(f"采集记录 {record.id} 缺少 output_dir")

        output_path = Path(record.output_dir).resolve()
        try:
            relative_path = str(output_path.relative_to(self._storage_base))
        except ValueError as exc:
            raise ValueError(
                f"采集记录 {record.id} 输出目录不在存储根目录内: {output_path}"
            ) from exc
        remote_filepath = f"{self._remote_path}/{relative_path}"

        request_data = CloudDataCreateRequest(
            task_id=record.task_id,
            filepath=remote_filepath,
            collector=record.user_id,
            file_size=record.file_size or 0,
            file_time=record.duration or 0,
            upload_time=int(upload_start_time.timestamp()),
            end_time=int(upload_end_time.timestamp()),
        )

        cloud_id = await self._cloud_client.create_data(request_data, token=token)
        if cloud_id is None:
            raise RuntimeError(f"云端返回空 cloud_id: record_id={record.id}")

        return cloud_id
