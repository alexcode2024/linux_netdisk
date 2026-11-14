from __future__ import annotations

import asyncio
import math
import mimetypes
import os
import secrets
import shutil
import time
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DbSession

from .cloud import BaiduPanAdapter, CloudAdapterError, get_cloud_adapter
from .config import Settings, load_settings
from .database import AuditLog, BackupJob, BackupJobItem, BaiduOAuthState, Session, SessionLocal, User, get_db, init_db, utcnow
from .paths import get_root, safe_name, safe_path, validate_relative_path
from .schemas import (
    BackupRequest,
    FileEntry,
    FileListResponse,
    JobItemResponse,
    JobResponse,
    LoginRequest,
    MkdirRequest,
    MoveRequest,
    PathRequest,
    RemoteDirectoryRequest,
    RenameRequest,
)
from .security import create_session, get_current_user, is_login_limited, register_login_failure, verify_password


app = FastAPI(title="Ubuntu NAS 文件管理", version="1.0.0", docs_url="/api/docs", redoc_url=None)


@app.on_event("startup")
def startup() -> None:
    settings = load_settings()
    app.state.settings = settings
    init_db()


def settings_for(request: Request) -> Settings:
    return request.app.state.settings


def current_user(request: Request, db: DbSession = Depends(get_db)) -> User:
    return get_current_user(request, db, settings_for(request))


def audit(db: DbSession, user: User, action: str, root_id: str | None, path: str | None, result: str) -> None:
    db.add(AuditLog(user_id=user.id, action=action, root_id=root_id, relative_path=path, result=result))


def job_response(job: BackupJob, include_items: bool = False) -> JobResponse:
    return JobResponse(
        id=job.id,
        status=job.status,
        cloud_provider=job.cloud_provider,
        source_name=job.source_name,
        remote_directory_id=job.remote_directory_id,
        remote_directory_path=job.remote_directory_path,
        conflict_policy=job.conflict_policy,
        total_files=job.total_files,
        completed_files=job.completed_files,
        failed_files=job.failed_files,
        total_bytes=job.total_bytes,
        uploaded_bytes=job.uploaded_bytes,
        current_file=job.current_file,
        speed_bytes_per_second=job.speed_bytes_per_second,
        error_summary=job.error_summary,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        items=[
            JobItemResponse(
                id=item.id,
                source_relative_path=item.source_relative_path,
                source_type=item.source_type,
                file_size=item.file_size,
                uploaded_bytes=item.uploaded_bytes,
                status=item.status,
                error_message=item.error_message,
                retry_count=item.retry_count,
            )
            for item in job.items
        ]
        if include_items
        else None,
    )


def expanded_files(settings: Settings, item_root_id: str, relative_path: str, source_type: str) -> list[tuple[str, str, str, int]]:
    root = get_root(settings, item_root_id)
    normalized = validate_relative_path(relative_path, allow_empty=False)
    source = safe_path(root, normalized)
    if source.is_file():
        return [(item_root_id, normalized, source.name, source.stat().st_size)]
    if not source.is_dir():
        raise HTTPException(status_code=400, detail="仅支持文件或文件夹")
    files: list[tuple[str, str, int]] = []
    for current_root, directories, filenames in os.walk(source, topdown=True, followlinks=False):
        directories[:] = sorted(directory for directory in directories if not (Path(current_root) / directory).is_symlink())
        for filename in sorted(filenames):
            current = Path(current_root) / filename
            if current.is_symlink():
                continue
            relative = current.relative_to(root.container_path.resolve()).as_posix()
            remote_relative = current.relative_to(source.parent if source_type == "directory" else source).as_posix()
            if source_type == "directory":
                remote_relative = (Path(source.name) / current.relative_to(source)).as_posix()
            files.append((item_root_id, relative, remote_relative, current.stat().st_size))
    return files


@app.get("/health/live", include_in_schema=False)
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
def health_ready(request: Request, db: DbSession = Depends(get_db)) -> dict[str, str]:
    settings = settings_for(request)
    for root in settings.storage.allowed_roots:
        if not root.container_path.is_dir():
            raise HTTPException(status_code=503, detail=f"授权目录不可用: {root.id}")
    db.execute(select(func.count(User.id))).scalar_one()
    return {"status": "ready"}


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest, request: Request, db: DbSession = Depends(get_db)) -> dict[str, str]:
    key = f"{request.client.host if request.client else 'unknown'}:{payload.username.lower()}"
    settings = settings_for(request)
    if is_login_limited(key, settings):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    user = db.scalar(select(User).where(User.username == payload.username, User.is_active.is_(True)))
    if user is None or not verify_password(payload.password, user.password_hash):
        register_login_failure(key)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user.last_login_at = utcnow()
    session_id = create_session(db, user, settings.security.session_ttl_hours)
    response = {"username": user.username}
    request.state.session_id = session_id
    from fastapi.responses import JSONResponse

    json_response = JSONResponse(response)
    json_response.set_cookie(
        "nas_session",
        session_id,
        max_age=settings.security.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return json_response


@app.post("/api/v1/auth/logout")
def logout(request: Request, db: DbSession = Depends(get_db)) -> dict[str, str]:
    session_id = request.cookies.get("nas_session")
    if session_id:
        db.execute(delete(Session).where(Session.id == session_id))
        db.commit()
    from fastapi.responses import JSONResponse

    response = JSONResponse({"status": "ok"})
    response.delete_cookie("nas_session", path="/")
    return response


@app.get("/api/v1/auth/me")
def me(user: User = Depends(current_user)) -> dict[str, str]:
    return {"username": user.username}


@app.get("/api/v1/roots")
def roots(request: Request, user: User = Depends(current_user)) -> list[dict[str, object]]:
    return [
        {"id": root.id, "name": root.name, "read_only": root.read_only, "allow_upload": root.allow_upload}
        for root in settings_for(request).storage.allowed_roots
    ]


@app.get("/api/v1/files", response_model=FileListResponse)
def list_files(
    request: Request,
    root: str = Query(..., min_length=1),
    path: str = Query(default=""),
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=50, ge=1, le=200),
    sort: str = Query(default="name", pattern=r"^(name|size|modified)$"),
    order: str = Query(default="asc", pattern=r"^(asc|desc)$"),
    user: User = Depends(current_user),
) -> FileListResponse:
    settings = settings_for(request)
    root_config = get_root(settings, root)
    normalized = validate_relative_path(path)
    directory = safe_path(root_config, normalized)
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail="目标路径不是目录")
    if len(q) > 200 or any(ord(char) < 32 for char in q):
        raise HTTPException(status_code=400, detail="搜索词无效")
    entries: list[FileEntry] = []
    for entry in directory.iterdir():
        if entry.is_symlink():
            entry_type = "symlink"
            size = None
        elif entry.is_dir():
            entry_type = "directory"
            size = None
        elif entry.is_file():
            entry_type = "file"
            size = entry.stat().st_size
        else:
            entry_type = "other"
            size = None
        if q.casefold() not in entry.name.casefold():
            continue
        relative = (Path(normalized) / entry.name).as_posix() if normalized else entry.name
        modified = entry.stat(follow_symlinks=False).st_mtime
        from datetime import datetime, timezone

        entries.append(
            FileEntry(
                name=entry.name,
                relative_path=relative,
                type=entry_type,
                size=size,
                modified_at=datetime.fromtimestamp(modified, tz=timezone.utc),
                extension=entry.suffix.lower().lstrip(".") or None,
            )
        )
    reverse = order == "desc"
    if sort == "size":
        entries.sort(key=lambda entry: entry.size if entry.size is not None else -1, reverse=reverse)
    elif sort == "modified":
        entries.sort(key=lambda entry: entry.modified_at, reverse=reverse)
    else:
        entries.sort(key=lambda entry: entry.name.casefold(), reverse=reverse)
    entries.sort(key=lambda entry: entry.type != "directory")
    total = len(entries)
    start = (page - 1) * page_size
    return FileListResponse(root_id=root, path=normalized, items=entries[start : start + page_size], page=page, page_size=page_size, total=total, sort=sort, order=order)


@app.get("/api/v1/files/info")
def file_info(request: Request, root: str, path: str = "", user: User = Depends(current_user)) -> dict[str, object]:
    root_config = get_root(settings_for(request), root)
    target = safe_path(root_config, path)
    stat_result = target.stat(follow_symlinks=False)
    return {"root_id": root, "relative_path": validate_relative_path(path), "name": target.name, "type": "directory" if target.is_dir() else "file", "size": stat_result.st_size if target.is_file() else None, "modified_at": stat_result.st_mtime}


@app.get("/api/v1/files/download")
def download_file(request: Request, root: str, path: str, user: User = Depends(current_user)) -> FileResponse:
    root_config = get_root(settings_for(request), root)
    target = safe_path(root_config, path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="只能下载文件")
    return FileResponse(target, filename=target.name, media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream")


@app.get("/api/v1/files/preview")
def preview_file(request: Request, root: str, path: str, user: User = Depends(current_user)) -> FileResponse:
    root_config = get_root(settings_for(request), root)
    target = safe_path(root_config, path)
    if not target.is_file() or target.stat().st_size > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="仅支持预览 10MB 以内的文件")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    safe_image_types = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}
    safe_text_types = {"text/plain", "text/csv", "application/json"}
    if media_type not in safe_image_types | safe_text_types | {"application/pdf"}:
        raise HTTPException(status_code=415, detail="该文件类型不支持预览")
    response_type = "text/plain; charset=utf-8" if media_type in safe_text_types else media_type
    return FileResponse(target, media_type=response_type, headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})


@app.post("/api/v1/files/mkdir")
def mkdir(payload: MkdirRequest, request: Request, db: DbSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, str]:
    root = get_root(settings_for(request), payload.root_id)
    if root.read_only:
        raise HTTPException(status_code=403, detail="授权目录只读")
    parent = safe_path(root, payload.relative_path)
    if not parent.is_dir():
        raise HTTPException(status_code=400, detail="父路径不是目录")
    name = safe_name(payload.name)
    destination = safe_path(root, f"{validate_relative_path(payload.relative_path)}/{name}".strip("/"), must_exist=False)
    if destination.exists():
        raise HTTPException(status_code=409, detail="目标已存在")
    destination.mkdir()
    audit(db, user, "mkdir", root.id, f"{payload.relative_path}/{name}".strip("/"), "success")
    db.commit()
    return {"path": f"{payload.relative_path}/{name}".strip("/")}


@app.post("/api/v1/files/rename")
def rename(payload: RenameRequest, request: Request, db: DbSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, str]:
    root = get_root(settings_for(request), payload.root_id)
    if root.read_only:
        raise HTTPException(status_code=403, detail="授权目录只读")
    source = safe_path(root, payload.relative_path)
    name = safe_name(payload.new_name)
    destination_path = (Path(validate_relative_path(payload.relative_path)).parent / name).as_posix()
    destination = safe_path(root, destination_path, must_exist=False)
    if destination.exists() and not payload.overwrite:
        raise HTTPException(status_code=409, detail="目标已存在，请确认覆盖")
    if destination.exists() and destination.is_dir():
        raise HTTPException(status_code=409, detail="不能覆盖目录")
    source.rename(destination)
    audit(db, user, "rename", root.id, payload.relative_path, "success")
    db.commit()
    return {"path": destination_path}


@app.post("/api/v1/files/move")
def move(payload: MoveRequest, request: Request, db: DbSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, str]:
    root = get_root(settings_for(request), payload.root_id)
    if root.read_only:
        raise HTTPException(status_code=403, detail="授权目录只读")
    source = safe_path(root, payload.relative_path)
    destination_parent = safe_path(root, payload.destination_path)
    if not destination_parent.is_dir():
        raise HTTPException(status_code=400, detail="目标路径不是目录")
    destination_path = (Path(validate_relative_path(payload.destination_path)) / source.name).as_posix()
    destination = safe_path(root, destination_path, must_exist=False)
    if destination.exists() and not payload.overwrite:
        raise HTTPException(status_code=409, detail="目标已存在，请确认覆盖")
    if destination.exists():
        raise HTTPException(status_code=409, detail="默认不允许覆盖目标")
    if source.is_dir() and destination_parent.resolve().is_relative_to(source.resolve()):
        raise HTTPException(status_code=400, detail="不能将目录移动到自身内部")
    source.rename(destination)
    audit(db, user, "move", root.id, payload.relative_path, "success")
    db.commit()
    return {"path": destination_path}


@app.delete("/api/v1/files")
def delete_file(request: Request, root: str, path: str, db: DbSession = Depends(get_db), user: User = Depends(current_user)) -> None:
    settings = settings_for(request)
    root_config = get_root(settings, root)
    if not settings.security.allow_delete or not root_config.allow_delete:
        audit(db, user, "delete", root, path, "denied")
        db.commit()
        raise HTTPException(status_code=403, detail="删除功能默认关闭")
    raise HTTPException(status_code=501, detail="删除功能尚未启用")


@app.post("/api/v1/files/upload")
async def upload_file(request: Request, root: str, path: str = "", file: UploadFile = File(...), db: DbSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, str]:
    root_config = get_root(settings_for(request), root)
    if root_config.read_only or not root_config.allow_upload:
        raise HTTPException(status_code=403, detail="该目录不允许上传")
    parent = safe_path(root_config, path)
    if not parent.is_dir():
        raise HTTPException(status_code=400, detail="上传目标不是目录")
    filename = safe_name(file.filename or "")
    destination = safe_path(root_config, f"{validate_relative_path(path)}/{filename}".strip("/"), must_exist=False)
    if destination.exists():
        raise HTTPException(status_code=409, detail="目标已存在，不允许覆盖")
    temporary = destination.with_name(f".{destination.name}.upload-{secrets.token_hex(12)}")
    try:
        with temporary.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                output.write(chunk)
        os.replace(temporary, destination)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="上传文件失败") from exc
    finally:
        await file.close()
    audit(db, user, "upload", root, f"{path}/{filename}".strip("/"), "success")
    db.commit()
    return {"path": f"{path}/{filename}".strip("/")}


@app.get("/api/v1/cloud/quark/status")
def quark_status(request: Request, user: User = Depends(current_user)) -> dict[str, object]:
    try:
        settings = settings_for(request)
        status_result = get_cloud_adapter("quark", settings.quark, settings.baidu).check_auth()
        return {"authenticated": status_result.authenticated, "message": status_result.message}
    except CloudAdapterError as exc:
        return {"authenticated": False, "message": str(exc)}


@app.get("/api/v1/cloud/quark/directories")
def quark_directories(request: Request, fid: str = "0", path: str = "/", user: User = Depends(current_user)) -> list[dict[str, str]]:
    try:
        settings = settings_for(request)
        entries = get_cloud_adapter("quark", settings.quark, settings.baidu).list_directory(fid, path)
        return [{"id": entry.id, "name": entry.name, "path": entry.path, "type": entry.type} for entry in entries]
    except CloudAdapterError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/cloud/quark/directories")
def quark_mkdir(payload: RemoteDirectoryRequest, request: Request, user: User = Depends(current_user)) -> dict[str, str]:
    try:
        settings = settings_for(request)
        entry = get_cloud_adapter("quark", settings.quark, settings.baidu).create_directory(payload.parent_id, payload.parent_path, payload.name)
        return {"id": entry.id, "name": entry.name, "path": entry.path}
    except CloudAdapterError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/cloud/baidu/status")
def baidu_status(request: Request, user: User = Depends(current_user)) -> dict[str, object]:
    settings = settings_for(request)
    status_result = BaiduPanAdapter(settings.baidu).check_auth()
    try:
        root_path = BaiduPanAdapter(settings.baidu).app_root()
    except CloudAdapterError:
        root_path = None
    return {"authenticated": status_result.authenticated, "message": status_result.message, "root_path": root_path}


@app.get("/api/v1/cloud/baidu/authorize")
def baidu_authorize(request: Request, db: DbSession = Depends(get_db), user: User = Depends(current_user)) -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    db.query(BaiduOAuthState).filter(BaiduOAuthState.created_at < utcnow() - timedelta(minutes=10)).delete()
    db.add(BaiduOAuthState(state=state, user_id=user.id))
    db.commit()
    try:
        settings = settings_for(request)
        url = BaiduPanAdapter(settings.baidu).authorize_url(state)
    except CloudAdapterError as exc:
        db.query(BaiduOAuthState).filter(BaiduOAuthState.state == state).delete()
        db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RedirectResponse(url, status_code=302)


@app.get("/api/v1/cloud/baidu/oauth/callback")
def baidu_callback(request: Request, state: str = "", code: str = "", error: str = "", db: DbSession = Depends(get_db)) -> RedirectResponse:
    flow = db.get(BaiduOAuthState, state) if state else None
    if flow is None or flow.created_at < utcnow() - timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="百度网盘授权状态无效或已过期")
    db.delete(flow)
    db.commit()
    if error or not code:
        return RedirectResponse("/?baidu_auth=failed", status_code=303)
    try:
        settings = settings_for(request)
        BaiduPanAdapter(settings.baidu).exchange_code(code)
    except CloudAdapterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return RedirectResponse("/?baidu_auth=success", status_code=303)


@app.get("/api/v1/cloud/baidu/directories")
def baidu_directories(request: Request, fid: str = "/", path: str = "/", user: User = Depends(current_user)) -> list[dict[str, str]]:
    try:
        settings = settings_for(request)
        entries = get_cloud_adapter("baidu", settings.quark, settings.baidu).list_directory(fid, path)
        return [{"id": entry.id, "name": entry.name, "path": entry.path, "type": entry.type} for entry in entries]
    except CloudAdapterError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/cloud/baidu/directories")
def baidu_mkdir(payload: RemoteDirectoryRequest, request: Request, user: User = Depends(current_user)) -> dict[str, str]:
    try:
        settings = settings_for(request)
        entry = get_cloud_adapter("baidu", settings.quark, settings.baidu).create_directory(payload.parent_id, payload.parent_path, payload.name)
        return {"id": entry.id, "name": entry.name, "path": entry.path}
    except CloudAdapterError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/backups", response_model=JobResponse)
def create_backup(payload: BackupRequest, request: Request, db: DbSession = Depends(get_db), user: User = Depends(current_user)) -> JobResponse:
    settings = settings_for(request)
    try:
        auth = get_cloud_adapter(payload.cloud_provider, settings.quark, settings.baidu).check_auth()
        if not auth.authenticated:
            raise HTTPException(status_code=503, detail=auth.message)
    except CloudAdapterError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    files: list[tuple[str, str, str, int]] = []
    source_names: list[str] = []
    seen: set[tuple[str, str]] = set()
    for requested in payload.items:
        root = get_root(settings, requested.root_id)
        normalized = validate_relative_path(requested.relative_path, allow_empty=False)
        source = safe_path(root, normalized)
        actual_type = "directory" if source.is_dir() else "file" if source.is_file() else "other"
        if actual_type != requested.type:
            raise HTTPException(status_code=400, detail=f"文件类型已变化: {normalized}")
        source_names.append(source.name)
        for expanded in expanded_files(settings, requested.root_id, normalized, requested.type):
            if (expanded[0], expanded[1]) not in seen:
                files.append(expanded)
                seen.add((expanded[0], expanded[1]))
    if not files:
        raise HTTPException(status_code=400, detail="所选目录不包含可上传文件")
    source_name = source_names[0] if len(source_names) == 1 else f"{source_names[0]} 等 {len(source_names)} 项"
    job = BackupJob(id=str(uuid.uuid4()), status="pending", cloud_provider=payload.cloud_provider, source_name=source_name, remote_directory_id=payload.remote_directory_id, remote_directory_path=payload.remote_directory_path, conflict_policy=payload.conflict_policy, total_files=len(files), total_bytes=sum(size for _, _, _, size in files))
    job.items = [BackupJobItem(root_id=root_id, source_relative_path=path, remote_relative_path=remote_path, source_type="file", file_size=size) for root_id, path, remote_path, size in files]
    db.add(job)
    audit(db, user, "backup_create", None, None, "success")
    db.commit()
    db.refresh(job)
    return job_response(job, include_items=True)


@app.get("/api/v1/backups", response_model=list[JobResponse])
def list_backups(user: User = Depends(current_user), db: DbSession = Depends(get_db), limit: int = Query(default=50, ge=1, le=200)) -> list[JobResponse]:
    jobs = db.scalars(select(BackupJob).order_by(BackupJob.created_at.desc()).limit(limit)).all()
    return [job_response(job) for job in jobs]


@app.get("/api/v1/backups/{job_id}", response_model=JobResponse)
def get_backup(job_id: str, user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> JobResponse:
    job = db.get(BackupJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job_response(job, include_items=True)


@app.post("/api/v1/backups/{job_id}/retry", response_model=JobResponse)
def retry_backup(job_id: str, user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> JobResponse:
    job = db.get(BackupJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status not in {"failed", "completed"}:
        raise HTTPException(status_code=409, detail="当前任务不可重试")
    failed = [item for item in job.items if item.status == "failed"]
    if not failed:
        failed = [item for item in job.items if item.status != "completed"]
    if not failed:
        raise HTTPException(status_code=409, detail="没有失败项目")
    for item in failed:
        item.status = "pending"
        item.error_message = None
        item.uploaded_bytes = 0
        item.retry_count += 1
    job.failed_files = 0
    job.status = "pending"
    job.error_summary = None
    job.finished_at = None
    db.commit()
    return job_response(job, include_items=True)


@app.post("/api/v1/backups/{job_id}/cancel", response_model=JobResponse)
def cancel_backup(job_id: str, user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> JobResponse:
    job = db.get(BackupJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status not in {"pending", "preparing", "running", "retrying"}:
        raise HTTPException(status_code=409, detail="当前任务不可取消")
    job.status = "cancelled"
    job.finished_at = utcnow()
    db.commit()
    return job_response(job, include_items=True)


@app.delete("/api/v1/backups/{job_id}")
def delete_backup(job_id: str, user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> dict[str, bool]:
    job = db.get(BackupJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status != "failed":
        raise HTTPException(status_code=409, detail="仅允许删除失败任务")
    audit(db, user, "backup_delete", None, job.source_name, "success")
    db.delete(job)
    db.commit()
    return {"deleted": True}


@app.get("/api/v1/backups/{job_id}/events")
async def backup_events(job_id: str, request: Request, user: User = Depends(current_user)) -> StreamingResponse:
    async def event_stream():
        while True:
            if await request.is_disconnected():
                return
            with SessionLocal() as db:
                job = db.get(BackupJob, job_id)
                if job is None:
                    yield 'event: error\ndata: {"message":"任务不存在"}\n\n'
                    return
                payload = job_response(job, include_items=False).model_dump_json()
                yield f"event: progress\ndata: {payload}\n\n"
                if job.status in {"completed", "failed", "cancelled"}:
                    return
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


static_dir = Path(__file__).resolve().parents[2] / "static"
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
