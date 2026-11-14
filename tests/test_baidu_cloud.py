from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from backend.app.cloud import BaiduPanAdapter, CloudAdapterError
from backend.app.config import BaiduConfig


class BaiduPanAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.credentials = self.root / "baidu_param"
        self.credentials.write_text("AppID=123\nAppKey=test-app-key\nSecretKey=test-secret\nSignKey=test-sign\n", encoding="utf-8")
        self.config = BaiduConfig(
            enabled=True,
            credentials_file=self.credentials,
            token_file=self.root / "token.json",
            redirect_uri="http://192.168.1.230:8088/api/v1/cloud/baidu/oauth/callback",
        )
        self.adapter = BaiduPanAdapter(self.config)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_authorize_url_uses_registered_redirect_and_state(self) -> None:
        parsed = urlparse(self.adapter.authorize_url("state-value"))
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "openapi.baidu.com")
        self.assertEqual(query["client_id"], ["test-app-key"])
        self.assertEqual(query["redirect_uri"], [self.config.redirect_uri])
        self.assertEqual(query["state"], ["state-value"])

    def test_credentials_are_required(self) -> None:
        self.credentials.write_text("AppID=123\nAppKey=test-app-key\n", encoding="utf-8")
        with self.assertRaises(CloudAdapterError):
            self.adapter.authorize_url("state-value")

    def test_app_root_is_scoped_to_the_registered_app_name(self) -> None:
        self.config.app_name = "NAS文件管理"
        self.assertEqual(self.adapter.app_root(), "/apps/NAS文件管理")
        self.config.app_name = "../other-app"
        with self.assertRaises(CloudAdapterError):
            self.adapter.app_root()

    def test_token_file_is_saved_with_private_permissions(self) -> None:
        self.adapter._save_token({"access_token": "sample-token"})
        self.assertEqual(json.loads(self.config.token_file.read_text())["access_token"], "sample-token")
        self.assertEqual(self.config.token_file.stat().st_mode & 0o777, 0o600)

    def test_json_get_retries_transient_service_timeouts(self) -> None:
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"errno":0}'
        with patch("backend.app.cloud.urllib.request.urlopen", side_effect=[
            urllib.error.URLError("timed out"),
            response,
        ]) as urlopen, patch("backend.app.cloud.time.sleep") as sleep:
            payload = self.adapter._json_request("https://example.invalid/api", retry_safe=True)

        self.assertEqual(payload, {"errno": 0})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_json_post_does_not_retry_ambiguous_timeouts(self) -> None:
        with patch("backend.app.cloud.urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")) as urlopen, \
             patch("backend.app.cloud.time.sleep") as sleep:
            with self.assertRaisesRegex(CloudAdapterError, "服务暂时不可用"):
                self.adapter._json_request("https://example.invalid/api", data=b"request")

        urlopen.assert_called_once()
        sleep.assert_not_called()

    def test_upload_retries_transient_part_timeouts(self) -> None:
        source = self.root / "upload.txt"
        source.write_bytes(b"upload contents")
        progress: list[int] = []
        self.adapter._api = Mock(side_effect=[
            {"uploadid": "upload-id", "block_list": [0]},
            {"fs_id": "uploaded-id"},
        ])
        self.adapter._json_request = Mock(return_value={"host": "https://d.pcs.baidu.com"})
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"errno":0}'
        with patch("backend.app.cloud.BaiduPanAdapter.app_root", return_value="/apps/NAS文件管理"), \
             patch("backend.app.cloud.BaiduPanAdapter.list_directory", return_value=[]), \
             patch.object(self.adapter, "_access_token", return_value="test-token"), \
             patch("backend.app.cloud.urllib.request.urlopen", side_effect=[
                 urllib.error.URLError("timed out"),
                 urllib.error.URLError("timed out"),
                 response,
             ]) as urlopen, \
             patch("backend.app.cloud.time.sleep") as sleep:
            result = self.adapter.upload_file(source, "/apps/NAS文件管理", progress.append)

        self.assertTrue(result.success)
        self.assertEqual(result.remote_id, "uploaded-id")
        self.assertEqual(progress, [len(b"upload contents")])
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_upload_fails_after_transient_part_timeouts_are_exhausted(self) -> None:
        source = self.root / "upload.txt"
        source.write_bytes(b"upload contents")
        self.adapter._api = Mock(return_value={"uploadid": "upload-id", "block_list": [0]})
        self.adapter._json_request = Mock(return_value={"host": "https://d.pcs.baidu.com"})
        with patch("backend.app.cloud.BaiduPanAdapter.app_root", return_value="/apps/NAS文件管理"), \
             patch("backend.app.cloud.BaiduPanAdapter.list_directory", return_value=[]), \
             patch.object(self.adapter, "_access_token", return_value="test-token"), \
             patch("backend.app.cloud.urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")) as urlopen, \
             patch("backend.app.cloud.time.sleep") as sleep:
            with self.assertRaisesRegex(CloudAdapterError, "分片上传失败"):
                self.adapter.upload_file(source, "/apps/NAS文件管理", lambda _: None)

        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])


if __name__ == "__main__":
    unittest.main()
