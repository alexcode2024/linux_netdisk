from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class RootResponse(BaseModel):
    id: str
    name: str
    read_only: bool
    allow_upload: bool


class FileEntry(BaseModel):
    name: str
    relative_path: str
    type: Literal["file", "directory", "symlink", "other"]
    size: int | None
    modified_at: datetime
    extension: str | None = None


class FileListResponse(BaseModel):
    root_id: str
    path: str
    items: list[FileEntry]
    page: int
    page_size: int
    total: int
    sort: str
    order: str


class PathRequest(BaseModel):
    root_id: str
    relative_path: str = ""


class MkdirRequest(PathRequest):
    name: str


class RenameRequest(PathRequest):
    new_name: str
    overwrite: bool = False


class MoveRequest(PathRequest):
    destination_path: str
    overwrite: bool = False


class BackupItemRequest(BaseModel):
    root_id: str
    relative_path: str
    type: Literal["file", "directory"]


class BackupRequest(BaseModel):
    items: list[BackupItemRequest] = Field(min_length=1, max_length=1000)
    cloud_provider: Literal["quark", "baidu"] = "quark"
    remote_directory_id: str = Field(min_length=1, max_length=300)
    remote_directory_path: str = Field(default="/", min_length=1, max_length=2000)
    conflict_policy: Literal["skip", "fail"] = "skip"


class RemoteDirectoryRequest(BaseModel):
    parent_id: str = Field(default="0", min_length=1, max_length=300)
    parent_path: str = Field(default="/", min_length=1, max_length=2000)
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value) or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("远端目录名称无效")
        return value


class QuarkShareRequest(BaseModel):
    file_id: str = Field(min_length=1, max_length=300, pattern=r"^[A-Za-z0-9_~|][A-Za-z0-9_~|\-]*$")
    url_type: Literal[1, 2] = 2
    expired_type: Literal[1, 2, 3, 4, 5, 6, 7] = 1

    @field_validator("file_id")
    @classmethod
    def validate_file_id(cls, value: str) -> str:
        if value == "0":
            raise ValueError("请选择要分享的文件或文件夹")
        return value


class JobItemResponse(BaseModel):
    id: int
    source_relative_path: str
    source_type: str
    file_size: int
    uploaded_bytes: int
    status: str
    error_message: str | None
    retry_count: int


class JobResponse(BaseModel):
    id: str
    status: str
    cloud_provider: Literal["quark", "baidu"] = "quark"
    source_name: str = ""
    remote_directory_id: str
    remote_directory_path: str
    conflict_policy: str
    total_files: int
    completed_files: int
    failed_files: int
    total_bytes: int
    uploaded_bytes: int
    current_file: str | None
    speed_bytes_per_second: float
    error_summary: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    items: list[JobItemResponse] | None = None
