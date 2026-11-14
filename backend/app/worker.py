from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from sqlalchemy import select

from .cloud import CloudAdapterError, get_cloud_adapter
from .config import load_settings
from .database import BackupJob, BackupJobItem, SessionLocal, init_db, utcnow
from .paths import get_root, safe_path

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("nas-worker")


def process_job(job_id: str) -> None:
    settings = load_settings()
    db = SessionLocal()
    try:
        job = db.get(BackupJob, job_id)
        if job is None or job.status == "cancelled":
            return
        job.status = "preparing"
        job.current_file = None
        job.speed_bytes_per_second = 0
        job.started_at = job.started_at or utcnow()
        db.commit()
        adapter = get_cloud_adapter(job.cloud_provider, settings.quark, settings.baidu)
        auth_status = adapter.check_auth()
        if not auth_status.authenticated:
            raise CloudAdapterError(auth_status.message)
        db.refresh(job)
        if job.status == "cancelled":
            return
        job.status = "running"
        db.commit()
        transfer_started_at = time.monotonic()
        initial_uploaded_bytes = job.uploaded_bytes
        remote_directories: dict[str, tuple[str, str]] = {"": (job.remote_directory_id, job.remote_directory_path)}
        for item in sorted(job.items, key=lambda current: current.id):
            db.refresh(job)
            if job.status == "cancelled":
                return
            if item.status == "completed":
                continue
            root = get_root(settings, item.root_id)
            source = safe_path(root, item.source_relative_path)
            if not source.is_file():
                raise RuntimeError(f"源文件不存在或不是普通文件: {item.source_relative_path}")
            stat_before = source.stat()
            job.current_file = item.source_relative_path
            item.status = "running"
            item.uploaded_bytes = 0
            db.commit()

            remote_parent = Path(item.remote_relative_path).parent.as_posix()
            if remote_parent == ".":
                remote_parent = ""
            current_remote_id, current_remote_path = job.remote_directory_id, job.remote_directory_path
            accumulated = ""
            for component in remote_parent.split("/") if remote_parent else []:
                accumulated = f"{accumulated}/{component}".strip("/")
                if accumulated not in remote_directories:
                    parent = remote_directories.get(Path(accumulated).parent.as_posix(), (job.remote_directory_id, job.remote_directory_path))
                    created = adapter.create_directory(parent[0], parent[1], component)
                    remote_directories[accumulated] = (created.id, created.path)
                current_remote_id, current_remote_path = remote_directories[accumulated]

            def progress(uploaded: int) -> None:
                item.uploaded_bytes = min(uploaded, item.file_size)
                job.uploaded_bytes = sum(current.uploaded_bytes for current in job.items)
                elapsed = max(time.monotonic() - transfer_started_at, 0.001)
                job.speed_bytes_per_second = max(0, (job.uploaded_bytes - initial_uploaded_bytes) / elapsed)
                db.commit()

            upload_target = current_remote_path if job.cloud_provider == "baidu" else current_remote_id
            result = adapter.upload_file(source, upload_target, progress)
            stat_after = source.stat()
            if stat_before.st_size != stat_after.st_size or stat_before.st_mtime_ns != stat_after.st_mtime_ns:
                raise RuntimeError(f"上传期间源文件发生变化: {item.source_relative_path}")
            if not result.success:
                raise RuntimeError(result.message or "适配器未确认上传成功")
            item.status = "completed"
            item.uploaded_bytes = item.file_size
            item.remote_file_id = result.remote_id
            job.completed_files = sum(current.status == "completed" for current in job.items)
            job.uploaded_bytes = sum(current.uploaded_bytes for current in job.items)
            elapsed = max(time.monotonic() - transfer_started_at, 0.001)
            job.speed_bytes_per_second = max(0, (job.uploaded_bytes - initial_uploaded_bytes) / elapsed)
            db.commit()
        db.refresh(job)
        if job.status == "cancelled":
            return
        job.current_file = None
        job.status = "completed" if job.failed_files == 0 else "failed"
        job.finished_at = utcnow()
        db.commit()
    except Exception as exc:
        logger.exception("backup job failed: %s", job_id)
        job = db.get(BackupJob, job_id)
        if job is not None:
            db.refresh(job)
            if job.status == "cancelled":
                return
            for item in job.items:
                if item.status in {"pending", "running"}:
                    item.status = "failed"
                    item.error_message = str(exc)[:2000]
                    item.retry_count += 1
            job.failed_files = sum(item.status == "failed" for item in job.items)
            job.error_summary = str(exc)[:2000]
            job.status = "failed"
            job.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


def run() -> None:
    init_db(recover_interrupted=True)
    logger.info("nas-worker started")
    while True:
        db = SessionLocal()
        try:
            job = db.scalar(select(BackupJob).where(BackupJob.status == "pending").order_by(BackupJob.created_at).limit(1))
            if job:
                job_id = job.id
                db.close()
                process_job(job_id)
                continue
        finally:
            db.close()
        time.sleep(2)


if __name__ == "__main__":
    run()
