from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
