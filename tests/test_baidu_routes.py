from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlencode, urlsplit

from backend.app.cloud import CloudAdapterError, RemoteEntry
from backend.app.config import BaiduConfig, QuarkConfig
from backend.app.main import app, current_user, get_db


class BaiduRouteTests(unittest.IsolatedAsyncioTestCase):
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

    async def request(self, query: str = "") -> tuple[int, object]:
        target = urlsplit(f"/api/v1/cloud/baidu/directories{query}")
        messages = []

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            messages.append(message)

        await app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": target.path, "raw_path": target.path.encode(),
            "query_string": target.query.encode(), "root_path": "", "headers": [],
            "client": ("127.0.0.1", 12345), "server": ("test", 80),
        }, receive, send)
        response = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return messages[0]["status"], json.loads(response)

    async def test_browse_returns_files_folders_and_optional_metadata(self) -> None:
        adapter = Mock()
        adapter.list_directory.return_value = [
            RemoteEntry("123", "资料", "/apps/NAS文件管理/资料", "directory"),
            RemoteEntry("456", "notes.txt", "/apps/NAS文件管理/notes.txt", "file", 2048, 1700000000000),
        ]
        with patch("backend.app.main.get_cloud_adapter", return_value=adapter) as factory:
            status, response = await self.request()
        self.assertEqual(status, 200)
        self.assertEqual(factory.call_args.args[0], "baidu")
        adapter.list_directory.assert_called_once_with("/", "/")
        self.assertEqual([entry["type"] for entry in response], ["directory", "file"])
        self.assertIsNone(response[0]["size"])
        self.assertEqual(response[1]["size"], 2048)
        self.assertEqual(response[1]["modified_at"], 1700000000000)

    async def test_browse_preserves_nested_unicode_path(self) -> None:
        adapter = Mock()
        adapter.list_directory.return_value = []
        path = "/apps/NAS文件管理/照片与资料 #1"
        with patch("backend.app.main.get_cloud_adapter", return_value=adapter):
            status, response = await self.request("?" + urlencode({"fid": "123", "path": path}))
        self.assertEqual((status, response), (200, []))
        adapter.list_directory.assert_called_once_with("123", path)

    async def test_browse_reports_cloud_error(self) -> None:
        adapter = Mock()
        adapter.list_directory.side_effect = CloudAdapterError("百度网盘授权已失效，请重新授权")
        with patch("backend.app.main.get_cloud_adapter", return_value=adapter):
            status, response = await self.request()
        self.assertEqual(status, 503)
        self.assertEqual(response, {"detail": "百度网盘授权已失效，请重新授权"})

    async def test_browse_requires_login(self) -> None:
        del app.dependency_overrides[current_user]
        with patch("backend.app.main.get_cloud_adapter") as factory:
            status, response = await self.request()
        self.assertEqual(status, 401)
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
