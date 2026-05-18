"""上传停止保护测试"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.task_manager import BackgroundTaskManager
from src.schemas.upload import ActiveUploadResponse, UploadStatus
from src.services.upload_progress_store import UploadProgressStore
from src.services.upload_service import UploadService
from src.services.rsync_uploader import RsyncUploader


def test_upload_status_has_interrupted():
    assert hasattr(UploadStatus, "INTERRUPTED")
    assert UploadStatus.INTERRUPTED.value == "interrupted"


def test_get_tasks_by_prefix():
    async def _run():
        manager = BackgroundTaskManager()
        created_tasks = []

        async def dummy():
            await asyncio.sleep(10)

        t1 = manager.create_task("upload_123_1234567890", dummy(), critical=True)
        t2 = manager.create_task("cloud_sync_456_1234567890", dummy(), critical=True)
        t3 = manager.create_task("other_task", dummy(), critical=False)
        created_tasks.extend([t1, t2, t3])

        upload_tasks = manager.get_tasks_by_prefix("upload_")
        assert len(upload_tasks) == 1
        assert "upload_123_1234567890" in upload_tasks

        for task in created_tasks:
            if task:
                task.cancel()
        await asyncio.gather(*[t for t in created_tasks if t], return_exceptions=True)

    asyncio.run(_run())


def test_progress_store_mark_interrupted():
    store = UploadProgressStore()
    store.start(123)
    progress = store.mark_interrupted(123)
    assert progress is not None
    assert progress.status == UploadStatus.INTERRUPTED
    assert progress.error_message == "应用停止，上传中断"


def test_rsync_uploader_cancelled_error_propagates():
    store = UploadProgressStore()
    record_store = MagicMock()
    record_store.update_upload_status = AsyncMock()
    storage_base = MagicMock()
    storage_base.resolve.return_value = storage_base

    uploader = RsyncUploader(
        remote_user="test",
        remote_host="test.com",
        remote_port=22,
        remote_path="/data",
        ssh_key="",
        storage_base=storage_base,
        progress_store=store,
        record_store=record_store,
    )

    mock_proc = AsyncMock()
    mock_proc.stdout.read = AsyncMock(side_effect=asyncio.CancelledError)
    mock_proc.terminate = MagicMock()
    mock_proc.wait = AsyncMock()
    mock_proc.kill = MagicMock()
    mock_proc.returncode = None
    db = MagicMock()

    async def _run():
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("src.services.rsync_uploader.validate_safe_path") as mock_validate:
                mock_path = MagicMock()
                mock_path.exists.return_value = True
                mock_validate.return_value = mock_path

                try:
                    await uploader.upload("/test/path", 1, db)
                except asyncio.CancelledError:
                    return True
        return False

    result = asyncio.run(_run())
    assert result is True
    mock_proc.terminate.assert_called_once()
    record_store.update_upload_status.assert_awaited_once_with(1, "interrupted", db)


def test_upload_service_has_get_active_uploads():
    assert hasattr(UploadService, "get_active_uploads")


def test_get_active_uploads_includes_scheduled_batch_records():
    service = object.__new__(UploadService)
    service._progress_store = UploadProgressStore()
    service._task_manager = None
    service._scheduled_batch_uploads = {"upload_batch_123": (101, 102)}

    active = asyncio.run(service.get_active_uploads())

    assert {item["record_id"] for item in active} == {101, 102}
    assert all(item["status"] == "scheduled" for item in active)


def test_active_upload_response_schema():
    response = ActiveUploadResponse(
        has_active_upload=True,
        records=[{"record_id": 123, "status": "uploading", "progress": 50, "source": "memory"}],
    )
    assert response.has_active_upload is True
    assert len(response.records) == 1
    assert response.records[0]["record_id"] == 123
