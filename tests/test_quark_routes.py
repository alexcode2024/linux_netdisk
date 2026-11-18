from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app.cloud import CloudAdapterError, RemoteEntry, ShareResult
from backend.app.config import BaiduConfig, QuarkConfig
from backend.app.main import app, current_user, get_db


class QuarkRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.previous_settings = getattr(app.state, "settings", None)
        self.previous_overrides = app.dependency_overrides.copy()
        app.state.settings = SimpleNamespace(quark=QuarkConfig(), baidu=BaiduConfig())
        app.dependency_overrides[get_db] = lambda: None
        app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=1)

    def tearDown(self) -> None:
        app.state.settings = self.previous_settings
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.previous_overrides)

    async def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        messages = []
        body = json.dumps(payload).encode() if payload is not None else b""

        async def receive() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict) -> None:
            messages.append(message)

        await app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": b"", "root_path": "", "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345), "server": ("test", 80),
        }, receive, send)
        response = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return messages[0]["status"], json.loads(response)

    async def test_share_returns_official_link_and_passcode(self) -> None:
        with patch("backend.app.main.QuarkOfficialAdapter") as adapter:
            adapter.return_value.share.return_value = ShareResult("https://pan.quark.cn/s/example", "aB12")
            status, response = await self.request("POST", "/api/v1/cloud/quark/shares", {"file_id": "folder-123"})
        self.assertEqual(status, 200)
        self.assertEqual(response, {"share_url": "https://pan.quark.cn/s/example", "passcode": "aB12"})
        adapter.return_value.share.assert_called_once_with("folder-123", 2, 1)

    async def test_share_rejects_invalid_payload_without_calling_cli(self) -> None:
        with patch("backend.app.main.QuarkOfficialAdapter") as adapter:
            status, response = await self.request("POST", "/api/v1/cloud/quark/shares", {"file_id": "0"})
        self.assertEqual(status, 422)
        adapter.assert_not_called()

    async def test_share_accepts_official_composite_fid(self) -> None:
        file_id = "~1abcdefghijklmnopqrstuv|0123456789_ABC-def"
        with patch("backend.app.main.QuarkOfficialAdapter") as adapter:
            adapter.return_value.share.return_value = ShareResult("https://pan.quark.cn/s/example", "aB12")
            status, response = await self.request("POST", "/api/v1/cloud/quark/shares", {"file_id": file_id})
        self.assertEqual(status, 200)
        adapter.return_value.share.assert_called_once_with(file_id, 2, 1)

    async def test_share_service_error_reaches_frontend(self) -> None:
        with patch("backend.app.main.QuarkOfficialAdapter") as adapter:
            adapter.return_value.share.side_effect = CloudAdapterError("官方分享服务暂时不可用")
            status, response = await self.request("POST", "/api/v1/cloud/quark/shares", {"file_id": "folder-123"})
        self.assertEqual(status, 503)
        self.assertEqual(response, {"detail": "官方分享服务暂时不可用"})

    async def test_browse_returns_files_folders_and_optional_metadata(self) -> None:
        adapter = Mock()
        adapter.list_directory.return_value = [RemoteEntry("folder-123", "Folder", "/Folder", "directory"),
                                               RemoteEntry("file-123", "notes.txt", "/notes.txt", "file", 12, 1700000000000)]
        with patch("backend.app.main.get_cloud_adapter", return_value=adapter):
            status, response = await self.request("GET", "/api/v1/cloud/quark/directories")
        self.assertEqual(status, 200)
        self.assertEqual([entry["type"] for entry in response], ["directory", "file"])
        self.assertIsNone(response[0]["size"])
        self.assertEqual(response[1]["modified_at"], 1700000000000)

    async def test_browse_and_share_require_login(self) -> None:
        del app.dependency_overrides[current_user]
        with patch("backend.app.main.QuarkOfficialAdapter") as adapter, patch("backend.app.main.get_cloud_adapter") as factory:
            for method, path, payload in [
                ("GET", "/api/v1/cloud/quark/directories", None),
                ("POST", "/api/v1/cloud/quark/shares", {"file_id": "folder-123"}),
            ]:
                status, response = await self.request(method, path, payload)
                self.assertEqual(status, 401)
        adapter.assert_not_called()
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
