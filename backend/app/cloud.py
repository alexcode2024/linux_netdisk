from __future__ import annotations

import json
import os
import re
import selectors
import subprocess
import tempfile
import threading
import time
import hashlib
import mimetypes
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .config import BaiduConfig, QuarkConfig


_NETWORK_RETRY_DELAYS = (60,) * 10
_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


class CloudAdapterError(RuntimeError):
    pass


@dataclass
class AuthStatus:
    authenticated: bool
    message: str


@dataclass
class RemoteEntry:
    id: str
    name: str
    path: str
    type: str


@dataclass
class UploadResult:
    success: bool
    remote_id: str | None = None
    message: str | None = None


class CloudDriveAdapter(Protocol):
    def check_auth(self) -> AuthStatus: ...
    def list_directory(self, remote_id: str, remote_path: str = "/") -> list[RemoteEntry]: ...
    def create_directory(self, parent_id: str, parent_path: str, name: str) -> RemoteEntry: ...
    def upload_file(self, local_path: Path, remote_id: str, progress_callback: Callable[[int], None]) -> UploadResult: ...


class QuarkOfficialAdapter:
    def __init__(self, config: QuarkConfig):
        self.config = config

    def _ensure_configured(self) -> None:
        if not self.config.enabled:
            raise CloudAdapterError("夸克网盘未启用")
        if not self.config.script_path or not self.config.script_path.is_file():
            raise CloudAdapterError("未找到官方 quarkclouddrive CLI 脚本")
        if not self.config.home or not self.config.home.is_dir():
            raise CloudAdapterError("官方 CLI 用户目录不存在，请先在服务器侧完成授权")

    @staticmethod
    def _redact(value: str) -> str:
        return re.sub(
            r"(?i)(cookie|token|authorization|password|secret)(\s*[:=]\s*)[^\s,;]+",
            r"\1\2[REDACTED]",
            value,
        )

    @staticmethod
    def _result(stdout: str, action: str) -> dict:
        result = None
        for line in stdout.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("type") == "result":
                result = item
        if not isinstance(result, dict):
            raise CloudAdapterError(f"官方夸克 CLI {action} 未返回最终结果")
        if result.get("code") != 0:
            message = str(result.get("msg") or f"官方夸克 CLI {action} 失败")
            raise CloudAdapterError(QuarkOfficialAdapter._redact(message))
        return result

    def _run(self, *args: str, timeout: int = 3600, progress_callback: Callable[[int], None] | None = None) -> str:
        self._ensure_configured()
        try:
            process = subprocess.Popen(
                ["node", self.config.script_path.as_posix(), *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env={
                    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                    "HOME": self.config.home.as_posix(),
                    "CLIENT_INFO_PRODUCT_NAME": os.environ.get("CLIENT_INFO_PRODUCT_NAME", ""),
                },
            )
            stderr_parts: list[bytes] = []
            stderr_thread = threading.Thread(target=lambda: stderr_parts.append(process.stderr.read()), daemon=True)
            stderr_thread.start()
            stdout_parts: list[str] = []
            pending = b""
            deadline = time.monotonic() + timeout
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise CloudAdapterError("官方夸克 CLI 执行超时")
                        if not selector.select(min(remaining, 1)):
                            if process.poll() is not None:
                                break
                            continue
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            selector.unregister(process.stdout)
                            continue
                        pending += chunk
                        lines = pending.split(b"\n")
                        pending = lines.pop()
                        for raw_line in lines:
                            line = raw_line.decode("utf-8", errors="replace") + "\n"
                            stdout_parts.append(line)
                            if not progress_callback:
                                continue
                            try:
                                event = json.loads(line)
                                if event.get("type") == "progress":
                                    progress_callback(int((event.get("data") or {}).get("current", 0)))
                            except (ValueError, TypeError):
                                pass
                    if pending:
                        stdout_parts.append(pending.decode("utf-8", errors="replace"))
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except (subprocess.TimeoutExpired, CloudAdapterError):
                process.kill()
                process.wait()
                raise CloudAdapterError("官方夸克 CLI 执行超时")
            finally:
                stderr_thread.join(timeout=2)
            stdout = "".join(stdout_parts)
            stderr = b"".join(stderr_parts).decode("utf-8", errors="replace")
        except OSError as exc:
            raise CloudAdapterError(f"无法启动官方夸克 CLI: {type(exc).__name__}") from exc
        if process.returncode != 0:
            raise CloudAdapterError(self._redact((stderr or stdout or "官方夸克 CLI 执行失败")[-2000:]))
        return stdout

    def check_auth(self) -> AuthStatus:
        try:
            self._result(self._run("get-user-info", timeout=60), "授权检查")
            return AuthStatus(True, "夸克授权有效")
        except CloudAdapterError as exc:
            return AuthStatus(False, str(exc))

    def list_directory(self, remote_id: str, remote_path: str = "/") -> list[RemoteEntry]:
        with tempfile.TemporaryDirectory(prefix="quark-browse-") as task_dir:
            stdout = self._run("browse", "--parent-fid", remote_id, "--all", "--task-dir", task_dir, timeout=300)
            result = self._result(stdout, "目录浏览")
            artifact = result.get("data", {}).get("file_path")
            if not artifact:
                raise CloudAdapterError("官方夸克 CLI 未返回目录清单路径")
            artifact_path = Path(artifact)
            if not artifact_path.is_file() or not artifact_path.resolve().is_relative_to(Path(task_dir).resolve()):
                raise CloudAdapterError("官方夸克 CLI 返回了无效目录清单")
            entries: list[RemoteEntry] = []
            try:
                for line in artifact_path.read_text(encoding="utf-8").splitlines():
                    item = json.loads(line)
                    fid = item.get("fid")
                    name = item.get("filename")
                    if not fid or not name:
                        continue
                    is_directory = str(item.get("file_type")) == "0"
                    entries.append(RemoteEntry(str(fid), str(name), f"{remote_path.rstrip('/')}/{name}" or "/", "directory" if is_directory else "file"))
            except (OSError, ValueError, TypeError) as exc:
                raise CloudAdapterError("无法解析官方夸克 CLI 目录清单") from exc
            return entries

    def create_directory(self, parent_id: str, parent_path: str, name: str) -> RemoteEntry:
        result = self._result(self._run("create-folder", "--dir-path", name, "--parent-fid", parent_id), "创建目录")
        data = result.get("data") or {}
        fid = data.get("fid")
        if not fid:
            raise CloudAdapterError("官方夸克 CLI 创建目录结果缺少 FID")
        return RemoteEntry(str(fid), name, str(data.get("full_path") or f"{parent_path.rstrip('/')}/{name}"), "directory")

    def upload_file(self, local_path: Path, remote_id: str, progress_callback: Callable[[int], None]) -> UploadResult:
        output = self._run("upload", local_path.as_posix(), "--parent-fid", remote_id, progress_callback=progress_callback)
        result = self._result(output, "文件上传")
        data = result.get("data") or {}
        if int(data.get("successCount", 0)) < 1 or int(data.get("failedCount", 0)):
            raise CloudAdapterError(str(result.get("msg") or "官方夸克 CLI 未确认文件上传成功"))
        fids = data.get("fids") or []
        return UploadResult(True, str(fids[0]) if fids else None, str(result.get("msg") or "上传成功"))


class BaiduPanAdapter:
    oauth_authorize_url = "https://openapi.baidu.com/oauth/2.0/authorize"
    oauth_token_url = "https://openapi.baidu.com/oauth/2.0/token"
    api_url = "https://pan.baidu.com/rest/2.0/xpan/file"
    user_info_url = "https://pan.baidu.com/rest/2.0/xpan/nas"
    locate_upload_url = "https://d.pcs.baidu.com/rest/2.0/pcs/file"

    def __init__(self, config: BaiduConfig):
        self.config = config
        self._token_lock = threading.Lock()

    def _credentials(self) -> dict[str, str]:
        if not self.config.enabled or not self.config.credentials_file or not self.config.credentials_file.is_file():
            raise CloudAdapterError("百度网盘应用参数未配置")
        credentials: dict[str, str] = {}
        try:
            for line in self.config.credentials_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                separator = "=" if "=" in line else ":" if ":" in line else None
                if separator is None:
                    continue
                key, value = line.split(separator, 1)
                normalized = re.sub(r"[^a-z]", "", key.lower())
                credentials[normalized] = value.strip().strip("\"'")
        except OSError as exc:
            raise CloudAdapterError("无法读取百度网盘应用参数") from exc
        required = ("appid", "appkey", "secretkey")
        if any(not credentials.get(key) for key in required):
            raise CloudAdapterError("百度网盘应用参数缺少 AppID、AppKey 或 SecretKey")
        return credentials

    def app_root(self) -> str:
        name = (self.config.app_name or "").strip()
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise CloudAdapterError("请在百度网盘配置中填写开放平台显示的应用名称")
        return f"/apps/{name}"

    def authorize_url(self, state: str) -> str:
        if not self.config.redirect_uri:
            raise CloudAdapterError("百度网盘 OAuth 回调地址未配置")
        credentials = self._credentials()
        query = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": credentials["appkey"],
            "redirect_uri": self.config.redirect_uri,
            "scope": "basic,netdisk",
            "state": state,
        })
        return f"{self.oauth_authorize_url}?{query}"

    @staticmethod
    def _json_request(url: str, data: bytes | None = None, timeout: int = 30, retry_safe: bool = False) -> dict:
        request = urllib.request.Request(url, data=data, headers={"User-Agent": "NAS-Backup/1.0"})
        for attempt in range(len(_NETWORK_RETRY_DELAYS) + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    response_body = response.read()
                break
            except urllib.error.HTTPError as exc:
                if not retry_safe or exc.code not in _RETRYABLE_HTTP_STATUS_CODES or attempt == len(_NETWORK_RETRY_DELAYS):
                    raise CloudAdapterError("百度网盘服务暂时不可用") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if not retry_safe or attempt == len(_NETWORK_RETRY_DELAYS):
                    raise CloudAdapterError("百度网盘服务暂时不可用") from exc
            if attempt < len(_NETWORK_RETRY_DELAYS):
                time.sleep(_NETWORK_RETRY_DELAYS[attempt])
        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CloudAdapterError("百度网盘服务暂时不可用") from exc
        if not isinstance(payload, dict):
            raise CloudAdapterError("百度网盘返回了无效响应")
        error = payload.get("errno", payload.get("error"))
        if error not in (None, 0, "0"):
            raise CloudAdapterError(f"百度网盘 API 错误 ({error})")
        return payload

    def exchange_code(self, code: str) -> None:
        if not self.config.redirect_uri or not self.config.token_file:
            raise CloudAdapterError("百度网盘 OAuth 配置不完整")
        credentials = self._credentials()
        query = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "client_id": credentials["appkey"],
            "client_secret": credentials["secretkey"],
            "redirect_uri": self.config.redirect_uri,
        })
        token = self._json_request(f"{self.oauth_token_url}?{query}")
        if not token.get("access_token") or not token.get("refresh_token"):
            raise CloudAdapterError("百度网盘未返回有效授权凭据")
        token["expires_at"] = int(time.time()) + int(token.get("expires_in", 0))
        self._save_token(token)

    def _save_token(self, token: dict) -> None:
        if not self.config.token_file:
            raise CloudAdapterError("百度网盘授权存储路径未配置")
        path = self.config.token_file
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".baidu-token-", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(token, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _access_token(self) -> str:
        if not self.config.token_file or not self.config.token_file.is_file():
            raise CloudAdapterError("百度网盘尚未授权")
        with self._token_lock:
            try:
                token = json.loads(self.config.token_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CloudAdapterError("百度网盘授权文件无法读取") from exc
            if int(token.get("expires_at", 0)) > int(time.time()) + 60:
                return str(token["access_token"])
            credentials = self._credentials()
            query = urllib.parse.urlencode({
                "grant_type": "refresh_token",
                "refresh_token": token.get("refresh_token", ""),
                "client_id": credentials["appkey"],
                "client_secret": credentials["secretkey"],
            })
            refreshed = self._json_request(f"{self.oauth_token_url}?{query}")
            if not refreshed.get("access_token"):
                raise CloudAdapterError("百度网盘授权已失效，请重新授权")
            refreshed.setdefault("refresh_token", token.get("refresh_token"))
            refreshed["expires_at"] = int(time.time()) + int(refreshed.get("expires_in", 0))
            self._save_token(refreshed)
            return str(refreshed["access_token"])

    def _api(self, params: dict[str, str], post: dict[str, str] | None = None, *, retry_safe: bool = False) -> dict:
        token = self._access_token()
        query = urllib.parse.urlencode({**params, "access_token": token})
        body = urllib.parse.urlencode(post).encode() if post is not None else None
        return self._json_request(f"{self.api_url}?{query}", body, retry_safe=retry_safe or post is None)

    def check_auth(self) -> AuthStatus:
        try:
            token = self._access_token()
            query = urllib.parse.urlencode({"method": "uinfo", "access_token": token})
            result = self._json_request(f"{self.user_info_url}?{query}", retry_safe=True)
            return AuthStatus(bool(result.get("uk") or result.get("baidu_name")), "百度网盘授权有效")
        except CloudAdapterError as exc:
            return AuthStatus(False, str(exc))

    def list_directory(self, remote_id: str, remote_path: str = "/") -> list[RemoteEntry]:
        app_root = self.app_root()
        if remote_path == "/":
            remote_path = app_root
        if remote_path != app_root and not remote_path.startswith(f"{app_root}/"):
            raise CloudAdapterError("百度网盘只能访问当前应用目录")
        result = self._api({"method": "list", "dir": remote_path, "order": "name", "desc": "0", "start": "0", "limit": "1000", "web": "web", "folder": "0", "root": "0"})
        entries = []
        for item in result.get("list", []):
            path = str(item.get("path", ""))
            entries.append(RemoteEntry(str(item.get("fs_id", path)), str(item.get("server_filename", Path(path).name)), path, "directory" if int(item.get("isdir", 0)) else "file"))
        return entries

    def create_directory(self, parent_id: str, parent_path: str, name: str) -> RemoteEntry:
        app_root = self.app_root()
        if parent_path != app_root and not parent_path.startswith(f"{app_root}/"):
            raise CloudAdapterError("百度网盘只能在当前应用目录内创建文件夹")
        for entry in self.list_directory(parent_id, parent_path):
            if entry.name == name and entry.type == "directory":
                return entry
        path = f"{parent_path.rstrip('/')}/{name}" or f"/{name}"
        result = self._api({"method": "create"}, {"path": path, "isdir": "1", "rtype": "0"})
        return RemoteEntry(str(result.get("fs_id", path)), name, path, "directory")

    def upload_file(self, local_path: Path, remote_id: str, progress_callback: Callable[[int], None]) -> UploadResult:
        size = local_path.stat().st_size
        chunk_size = self.config.chunk_size_bytes
        digests: list[str] = []
        with local_path.open("rb") as source:
            while chunk := source.read(chunk_size):
                digests.append(hashlib.md5(chunk).hexdigest())
        if not digests:
            digests.append(hashlib.md5(b"").hexdigest())
        app_root = self.app_root()
        if remote_id != app_root and not remote_id.startswith(f"{app_root}/"):
            raise CloudAdapterError("百度网盘只能向当前应用目录上传文件")
        for entry in self.list_directory(remote_id, remote_id):
            if entry.name == local_path.name:
                return UploadResult(True, entry.id, "目标已存在，按跳过策略处理")
        remote_path = f"{remote_id.rstrip('/')}/{local_path.name}"
        precreate = self._api({"method": "precreate"}, {"path": remote_path, "size": str(size), "isdir": "0", "autoinit": "1", "block_list": json.dumps(digests), "rtype": "0"}, retry_safe=True)
        upload_id = str(precreate.get("uploadid", ""))
        if not upload_id:
            raise CloudAdapterError("百度网盘未返回分片上传标识")
        needed_parts = {int(index) for index in precreate.get("block_list", range(len(digests)))}
        upload_query = urllib.parse.urlencode({
            "method": "locateupload",
            "appid": "250528",
            "access_token": self._access_token(),
            "path": remote_path,
            "uploadid": upload_id,
        })
        upload_location = self._json_request(f"{self.locate_upload_url}?{upload_query}", retry_safe=True)
        upload_host = str(upload_location.get("host") or "")
        if not upload_host:
            servers = upload_location.get("servers") or []
            upload_host = str(servers[0].get("server", "")) if servers else ""
        if not upload_host:
            raise CloudAdapterError("百度网盘未返回可用上传域名")
        if not upload_host.startswith(("https://", "http://")):
            upload_host = f"https://{upload_host}"
        parsed_host = urllib.parse.urlparse(upload_host)
        if parsed_host.scheme != "https" or not parsed_host.hostname or not (parsed_host.hostname == "baidu.com" or parsed_host.hostname.endswith(".baidu.com")):
            raise CloudAdapterError("百度网盘返回了无效上传域名")
        uploaded = 0
        with local_path.open("rb") as source:
            for part_index, digest in enumerate(digests):
                chunk = source.read(chunk_size)
                if part_index not in needed_parts:
                    uploaded += len(chunk)
                    progress_callback(uploaded)
                    continue
                boundary = "----nasbackup" + secrets.token_hex(12)
                prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"blob\"\r\nContent-Type: {mimetypes.guess_type(local_path.name)[0] or 'application/octet-stream'}\r\n\r\n").encode()
                body = prefix + chunk + f"\r\n--{boundary}--\r\n".encode()
                query = urllib.parse.urlencode({"method": "upload", "type": "tmpfile", "path": remote_path, "uploadid": upload_id, "partseq": str(part_index), "access_token": self._access_token()})
                request = urllib.request.Request(f"{upload_host.rstrip('/')}/rest/2.0/pcs/superfile2?{query}", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "NAS-Backup/1.0"})
                for attempt in range(len(_NETWORK_RETRY_DELAYS) + 1):
                    try:
                        with urllib.request.urlopen(request, timeout=300) as response:
                            response_body = response.read()
                    except urllib.error.HTTPError as exc:
                        if exc.code not in _RETRYABLE_HTTP_STATUS_CODES or attempt == len(_NETWORK_RETRY_DELAYS):
                            raise CloudAdapterError("百度网盘分片上传失败") from exc
                    except (urllib.error.URLError, TimeoutError) as exc:
                        if attempt == len(_NETWORK_RETRY_DELAYS):
                            raise CloudAdapterError("百度网盘分片上传失败") from exc
                    else:
                        break
                    if attempt < len(_NETWORK_RETRY_DELAYS):
                        time.sleep(_NETWORK_RETRY_DELAYS[attempt])
                try:
                    part_result = json.loads(response_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CloudAdapterError("百度网盘分片上传失败") from exc
                if part_result.get("errno", 0) != 0:
                    raise CloudAdapterError(f"百度网盘分片上传错误 ({part_result.get('errno')})")
                uploaded += len(chunk)
                progress_callback(uploaded)
        created = self._api({"method": "create"}, {"path": remote_path, "size": str(size), "isdir": "0", "uploadid": upload_id, "block_list": json.dumps(digests), "rtype": "0"})
        return UploadResult(True, str(created.get("fs_id", "")) or None, "上传成功")


def get_cloud_adapter(provider: str, quark: QuarkConfig, baidu: BaiduConfig) -> CloudDriveAdapter:
    if provider == "baidu":
        return BaiduPanAdapter(baidu)
    if provider == "quark":
        return QuarkOfficialAdapter(quark)
    raise CloudAdapterError("不支持的网盘类型")
