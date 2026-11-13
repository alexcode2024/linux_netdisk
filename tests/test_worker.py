from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import worker
from backend.app.cloud import AuthStatus, RemoteEntry, UploadResult
from backend.app.config import AllowedRoot, Settings, StorageConfig
from backend.app.database import BackupJob, BackupJobItem, Base


class BackupWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.settings = Settings(storage=StorageConfig(allowed_roots=[
            AllowedRoot(id="downloads", name="Downloads", container_path=self.root),
        ]))
        self.adapter = Mock()
        self.adapter.check_auth.return_value = AuthStatus(True, "授权有效")
        self.adapter.upload_file.return_value = UploadResult(True, "uploaded-file-id")

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def create_job(self, provider: str, paths: list[str], remote_id: str | None = None) -> str:
        remote_path = "/apps/NAS文件管理" if provider == "baidu" else "/备份"
        job = BackupJob(
            id="test-job",
            status="pending",
            cloud_provider=provider,
            remote_directory_id=remote_id if remote_id is not None else remote_path,
            remote_directory_path=remote_path,
            total_files=len(paths),
            total_bytes=len(paths) * 4,
        )
        for relative_path in paths:
            source = self.root / relative_path
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"test")
            job.items.append(BackupJobItem(
                root_id="downloads",
                source_relative_path=relative_path,
                remote_relative_path=relative_path,
                source_type="file",
                file_size=4,
            ))
        with self.sessions() as session:
            session.add(job)
            session.commit()
        return job.id

    def run_job(self, job_id: str) -> None:
        with patch.object(worker, "load_settings", return_value=self.settings), \
             patch.object(worker, "SessionLocal", self.sessions), \
             patch.object(worker, "get_cloud_adapter", return_value=self.adapter):
            worker.process_job(job_id)
        with self.sessions() as session:
            job = session.get(BackupJob, job_id)
            self.assertEqual(job.status, "completed", job.error_summary)
            self.assertEqual(job.completed_files, job.total_files)
            self.assertEqual(job.uploaded_bytes, job.total_bytes)
            self.assertTrue(all(item.status == "completed" for item in job.items))

    def test_baidu_folder_upload_uses_path_not_numeric_directory_id(self) -> None:
        job_id = self.create_job("baidu", ["照片/相片.jpg"])
        self.adapter.create_directory.return_value = RemoteEntry(
            "123456789", "照片", "/apps/NAS文件管理/照片", "directory",
        )
        self.run_job(job_id)
        self.assertEqual(self.adapter.upload_file.call_args.args[1], "/apps/NAS文件管理/照片")

    def test_baidu_nested_folders_reuse_cached_directories(self) -> None:
        job_id = self.create_job("baidu", ["照片/旅行/第一张.jpg", "照片/旅行/第二张.jpg"])
        self.adapter.create_directory.side_effect = [
            RemoteEntry("123456789", "照片", "/apps/NAS文件管理/照片", "directory"),
            RemoteEntry("987654321", "旅行", "/apps/NAS文件管理/照片/旅行", "directory"),
        ]
        self.run_job(job_id)
        self.assertEqual(self.adapter.create_directory.call_count, 2)
        self.assertEqual(self.adapter.create_directory.call_args.args, (
            "123456789", "/apps/NAS文件管理/照片", "旅行",
        ))
        self.assertEqual([call.args[1] for call in self.adapter.upload_file.call_args_list], [
            "/apps/NAS文件管理/照片/旅行", "/apps/NAS文件管理/照片/旅行",
        ])

    def test_baidu_single_file_uses_selected_directory_path(self) -> None:
        job_id = self.create_job("baidu", ["文件.txt"], remote_id="123456789")
        self.run_job(job_id)
        self.adapter.create_directory.assert_not_called()
        self.assertEqual(self.adapter.upload_file.call_args.args[1], "/apps/NAS文件管理")

    def test_quark_folder_upload_keeps_directory_fid(self) -> None:
        job_id = self.create_job("quark", ["照片/相片.jpg"], remote_id="quark-root-fid")
        self.adapter.create_directory.return_value = RemoteEntry(
            "quark-child-fid", "照片", "/备份/照片", "directory",
        )
        self.run_job(job_id)
        self.assertEqual(self.adapter.upload_file.call_args.args[1], "quark-child-fid")

    def test_quark_single_file_keeps_selected_directory_fid(self) -> None:
        job_id = self.create_job("quark", ["文件.txt"], remote_id="quark-root-fid")
        self.run_job(job_id)
        self.adapter.create_directory.assert_not_called()
        self.assertEqual(self.adapter.upload_file.call_args.args[1], "quark-root-fid")


if __name__ == "__main__":
    unittest.main()
