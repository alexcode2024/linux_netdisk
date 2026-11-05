from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import HTTPException

from .config import AllowedRoot, Settings


CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def get_root(settings: Settings, root_id: str) -> AllowedRoot:
    for root in settings.storage.allowed_roots:
        if root.id == root_id:
            return root
    raise HTTPException(status_code=404, detail="授权目录不存在")


def validate_relative_path(relative_path: str, allow_empty: bool = True) -> str:
    if not isinstance(relative_path, str) or CONTROL_CHARACTERS.search(relative_path):
        raise HTTPException(status_code=400, detail="路径包含非法字符")
    normalized = relative_path.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        raise HTTPException(status_code=400, detail="不允许绝对路径")
    parts = [part for part in normalized.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise HTTPException(status_code=400, detail="不允许路径穿越")
    result = "/".join(parts)
    if not allow_empty and not result:
        raise HTTPException(status_code=400, detail="路径不能为空")
    return result


def safe_path(root: AllowedRoot, relative_path: str = "", must_exist: bool = True) -> Path:
    relative = validate_relative_path(relative_path)
    root_path = root.container_path
    try:
        root_real = root_path.resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"授权目录不可用: {root.id}") from exc
    if not root_real.is_dir():
        raise HTTPException(status_code=503, detail=f"授权目录不是文件夹: {root.id}")
    current = root_real
    for component in relative.split("/") if relative else []:
        current = current / component
        try:
            stat_result = current.lstat()
        except FileNotFoundError:
            if must_exist:
                raise HTTPException(status_code=404, detail="文件不存在")
            continue
        except OSError as exc:
            raise HTTPException(status_code=400, detail="无法访问路径") from exc
        if os.path.islink(current):
            raise HTTPException(status_code=403, detail="不允许访问符号链接")
        if stat_result.st_mode & 0o170000 == 0o120000:
            raise HTTPException(status_code=403, detail="不允许访问符号链接")
    candidate = root_real / relative if relative else root_real
    try:
        resolved = candidate.resolve(strict=must_exist)
    except FileNotFoundError:
        if must_exist:
            raise HTTPException(status_code=404, detail="文件不存在")
        resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root_real)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="路径超出授权目录") from exc
    return candidate


def safe_name(name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\\" in name or CONTROL_CHARACTERS.search(name):
        raise HTTPException(status_code=400, detail="文件名无效")
    return name
