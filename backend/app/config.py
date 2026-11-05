from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class LoginRateLimit(BaseModel):
    attempts: int = Field(default=5, ge=1, le=100)
    window_seconds: int = Field(default=300, ge=10, le=86400)


class SecurityConfig(BaseModel):
    session_ttl_hours: int = Field(default=12, ge=1, le=720)
    login_rate_limit: LoginRateLimit = Field(default_factory=LoginRateLimit)
    allow_delete: bool = False
    allow_symlinks: bool = False
    cors_origins: list[str] = Field(default_factory=list)


class AllowedRoot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    container_path: Path
    read_only: bool = False
    allow_upload: bool = True
    allow_delete: bool = False


class StorageConfig(BaseModel):
    allowed_roots: list[AllowedRoot] = Field(min_length=1)

    @field_validator("allowed_roots")
    @classmethod
    def unique_root_ids(cls, roots: list[AllowedRoot]) -> list[AllowedRoot]:
        ids = [root.id for root in roots]
        if len(ids) != len(set(ids)):
            raise ValueError("storage.allowed_roots ids must be unique")
        return roots


class RetryConfig(BaseModel):
    max_attempts: int = Field(default=3, ge=0, le=10)
    initial_delay_seconds: int = Field(default=5, ge=1, le=3600)
    max_delay_seconds: int = Field(default=120, ge=1, le=86400)


class QuarkConfig(BaseModel):
    enabled: bool = False
    adapter: str = "quarkclouddrive_offical"
    script_path: Path | None = None
    home: Path | None = None
    max_concurrent_uploads: int = Field(default=1, ge=1, le=1)
    conflict_policy: str = Field(default="skip", pattern=r"^(skip|fail)$")
    retry: RetryConfig = Field(default_factory=RetryConfig)


class BaiduConfig(BaseModel):
    enabled: bool = False
    app_name: str | None = None
    credentials_file: Path | None = None
    token_file: Path | None = None
    redirect_uri: str | None = None
    chunk_size_bytes: int = Field(default=4194304, ge=4194304, le=16777216)


class TasksConfig(BaseModel):
    retention_days: int = Field(default=90, ge=1, le=3650)
    progress_update_interval_seconds: float = Field(default=1.0, ge=0.1, le=60)


class AppSection(BaseModel):
    name: str = "Ubuntu NAS"
    timezone: str = "Asia/Shanghai"
    listen_host: str = "0.0.0.0"
    listen_port: int = Field(default=8080, ge=1, le=65535)
    log_level: str = "INFO"


class Settings(BaseModel):
    app: AppSection = Field(default_factory=AppSection)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    storage: StorageConfig
    quark: QuarkConfig = Field(default_factory=QuarkConfig)
    baidu: BaiduConfig = Field(default_factory=BaiduConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)

    @classmethod
    def from_file(cls, path: str | Path) -> "Settings":
        config_path = Path(path)
        if not config_path.is_file():
            raise RuntimeError(f"配置文件不存在: {config_path}")
        try:
            raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            settings = cls.model_validate(raw)
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            raise RuntimeError(f"配置校验失败: {exc}") from exc
        for root in settings.storage.allowed_roots:
            if not root.container_path.is_absolute():
                raise RuntimeError(f"授权目录必须是绝对路径: {root.id}")
        return settings


def config_path() -> Path:
    return Path(os.environ.get("APP_CONFIG", "config/app.yaml"))


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite:///./data/nas-web.sqlite3")


def load_settings() -> Settings:
    return Settings.from_file(config_path())
