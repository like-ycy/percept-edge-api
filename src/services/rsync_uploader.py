"""rsync 上传执行器"""

import asyncio
import re
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.path_validator import PathValidationError, validate_safe_path
from src.services.upload_progress_store import UploadProgressStore
from src.services.upload_record_store import UploadRecordStore


class RsyncUploader:
    """rsync 执行器"""

    def __init__(
        self,
        *,
        remote_user: str,
        remote_host: str,
        remote_port: int,
        remote_path: str,
        ssh_key: str,
        storage_base: Path,
        progress_store: UploadProgressStore,
        record_store: UploadRecordStore,
    ) -> None:
        self._remote_user = remote_user
        self._remote_host = remote_host
        self._remote_port = remote_port
        self._remote_path = remote_path
        self._ssh_key = ssh_key
        self._storage_base = storage_base
        self._progress_store = progress_store
        self._record_store = record_store

    async def upload(self, local_path: str, record_id: int, db: AsyncSession) -> None:
        """执行 rsync 上传并更新进度"""
        if not self._remote_host or not self._remote_path:
            raise RuntimeError("未配置远程主机或路径")

        try:
            validated_path = validate_safe_path(
                local_path,
                allowed_base=self._storage_base,
                allow_relative=False,
            )
        except PathValidationError as exc:
            raise RuntimeError(f"路径验证失败: {exc}") from exc

        if not validated_path.exists():
            raise RuntimeError(f"本地路径不存在: {validated_path}")

        ssh_key_path = self._resolve_ssh_key_path()
        remote_full_path = self._build_remote_path(validated_path)
        cmd = self._build_rsync_command(validated_path, remote_full_path, ssh_key_path)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            last_db_progress = 0
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(256)
                if not chunk:
                    break

                matches = re.findall(r"(\d+)%", chunk.decode(errors="ignore"))
                if not matches:
                    continue

                progress_pct = int(matches[-1])
                self._progress_store.update_progress(record_id, progress_pct)
                if progress_pct >= last_db_progress + 20 or progress_pct == 100:
                    await self._record_store.update_upload_progress(record_id, progress_pct, db)
                    last_db_progress = (progress_pct // 20) * 20

            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"rsync 失败: {stderr.decode()}")
        except asyncio.CancelledError:
            self._progress_store.mark_interrupted(record_id)
            await self._record_store.update_upload_status(record_id, "interrupted", db)
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            raise

    def _resolve_ssh_key_path(self) -> Path | None:
        """解析 SSH 密钥路径"""
        if not self._ssh_key:
            return None
        try:
            ssh_key_path = validate_safe_path(self._ssh_key, allow_relative=False)
        except PathValidationError as exc:
            raise RuntimeError(f"SSH 密钥路径验证失败: {exc}") from exc

        if not ssh_key_path.exists():
            raise RuntimeError(f"SSH 密钥文件不存在: {ssh_key_path}")
        return ssh_key_path

    def _build_remote_path(self, validated_path: Path) -> str:
        """构建远程完整路径"""
        try:
            relative_path = validated_path.relative_to(self._storage_base)
        except ValueError:
            relative_path = validated_path.name
        return f"{self._remote_path}/{relative_path}"

    def _build_rsync_command(
        self, validated_path: Path, remote_full_path: str, ssh_key_path: Path | None
    ) -> list[str]:
        """构建 rsync 命令"""
        cmd = [
            "/usr/local/bin/rsync",
            "-av",
            "--info=progress2",
            "--mkpath",
            "--no-compress",
            "--whole-file",
            "--inplace",
            "--block-size=131072",
        ]

        if ssh_key_path:
            cmd.extend(
                [
                    "-e",
                    (f"ssh -i {ssh_key_path} -p {self._remote_port} -o StrictHostKeyChecking=no"),
                ]
            )

        cmd.extend(
            [
                f"{validated_path}/",
                f"{self._remote_user}@{self._remote_host}:{remote_full_path}",
            ]
        )
        return cmd
