from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import database_url


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    user: Mapped[User] = relationship()


class BaiduOAuthState(Base):
    __tablename__ = "baidu_oauth_states"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BackupJob(Base):
    __tablename__ = "backup_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    cloud_provider: Mapped[str] = mapped_column(String(20), default="quark")
    source_name: Mapped[str] = mapped_column(String(1000), default="")
    remote_directory_id: Mapped[str] = mapped_column(String(300))
    remote_directory_path: Mapped[str] = mapped_column(String(2000))
    conflict_policy: Mapped[str] = mapped_column(String(20), default="skip")
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    completed_files: Mapped[int] = mapped_column(Integer, default=0)
    failed_files: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_bytes: Mapped[int] = mapped_column(Integer, default=0)
    current_file: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    speed_bytes_per_second: Mapped[float] = mapped_column(Float, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    items: Mapped[list["BackupJobItem"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class BackupJobItem(Base):
    __tablename__ = "backup_job_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("backup_jobs.id", ondelete="CASCADE"), index=True)
    root_id: Mapped[str] = mapped_column(String(64))
    source_relative_path: Mapped[str] = mapped_column(String(2000))
    remote_relative_path: Mapped[str] = mapped_column(String(2000), default="")
    source_type: Mapped[str] = mapped_column(String(20))
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    remote_file_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    job: Mapped[BackupJob] = relationship(back_populates="items")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    root_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relative_path: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    result: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def _sqlite_pragmas(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


engine = create_engine(
    database_url(),
    connect_args={"check_same_thread": False} if database_url().startswith("sqlite") else {},
    pool_pre_ping=True,
)
if database_url().startswith("sqlite"):
    event.listen(engine, "connect", _sqlite_pragmas)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(*, recover_interrupted: bool = False) -> None:
    if database_url().startswith("sqlite"):
        db_path = database_url().removeprefix("sqlite:///")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        job_columns = {column["name"] for column in inspect(connection).get_columns("backup_jobs")}
        if "cloud_provider" not in job_columns:
            connection.execute(text("ALTER TABLE backup_jobs ADD COLUMN cloud_provider VARCHAR(20) NOT NULL DEFAULT 'quark'"))
        if "source_name" not in job_columns:
            connection.execute(text("ALTER TABLE backup_jobs ADD COLUMN source_name VARCHAR(1000) NOT NULL DEFAULT ''"))
        item_columns = {column["name"] for column in inspect(connection).get_columns("backup_job_items")}
        if "remote_relative_path" not in item_columns:
            connection.execute(text("ALTER TABLE backup_job_items ADD COLUMN remote_relative_path VARCHAR(2000) NOT NULL DEFAULT ''"))
        connection.execute(text("""
            UPDATE backup_jobs
            SET source_name = (
                SELECT CASE
                    WHEN instr(remote_relative_path, '/') > 0
                    THEN substr(remote_relative_path, 1, instr(remote_relative_path, '/') - 1)
                    ELSE remote_relative_path
                END
                FROM backup_job_items
                WHERE backup_job_items.job_id = backup_jobs.id
                ORDER BY backup_job_items.id
                LIMIT 1
            )
            WHERE source_name = ''
              AND EXISTS (SELECT 1 FROM backup_job_items WHERE backup_job_items.job_id = backup_jobs.id)
        """))
        if recover_interrupted:
            connection.execute(text("UPDATE backup_jobs SET status='pending', error_summary='服务重启后等待恢复' WHERE status IN ('preparing','running','retrying')"))


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
