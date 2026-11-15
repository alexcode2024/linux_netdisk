from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.cloud import BaiduAPIError, BaiduPanAdapter
from backend.app.config import BaiduConfig


class BaiduErrorHandlingTests(unittest.TestCase):
    @staticmethod
    def response(payload: object, headers: dict | None = None) -> Mock:
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(payload).encode()
        response.getcode.return_value = 200
        response.headers = headers or {}
        return response

    def test_business_rate_limit_retries_with_request_id(self) -> None:
        with patch("backend.app.cloud.urllib.request.urlopen", side_effect=[
            self.response({"errno": "31034", "errmsg": "too frequent", "request_id": 12345}),
            self.response({"errno": "0", "uploadid": "new-id", "request_id": "success-id"}),
        ]) as request, patch("backend.app.cloud.time.sleep") as sleep, \
             self.assertLogs("backend.app.cloud", level="INFO") as logs:
            payload = BaiduPanAdapter._json_request(
                "https://example.invalid/?method=precreate", data=b"body", retry_safe=True,
            )
        self.assertEqual(payload["uploadid"], "new-id")
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(60)
        self.assertIn("request_id=12345", " ".join(logs.output))
        self.assertIn("request_id=success-id", " ".join(logs.output))

    def test_permanent_business_errors_are_classified_without_retries(self) -> None:
        cases = [(20016, "授权异常"), (20017, "授权异常"), (-6, "授权异常"),
                 (20012, "调用额度限制"), (20013, "权限或路径异常"),
                 (-10, "容量不足"), (31355, "参数异常"),
                 (31363, "分片不完整"), (31190, "分片不完整"),
                 (31365, "上传大小限制"), (-8, "目标已存在")]
        for code, category in cases:
            with self.subTest(code=code), \
                 patch("backend.app.cloud.urllib.request.urlopen", return_value=self.response({
                     "errno": code, "errmsg": "official error", "request_id": "failure-id",
                 })) as request, patch("backend.app.cloud.time.sleep") as sleep:
                with self.assertRaises(BaiduAPIError) as caught:
                    BaiduPanAdapter._json_request("https://example.invalid/?method=create", retry_safe=True)
                error = caught.exception
                self.assertEqual(error.category, category)
                self.assertEqual(error.code, str(code))
                self.assertEqual(error.request_id, "failure-id")
                self.assertIn("official error", str(error))
                request.assert_called_once()
                sleep.assert_not_called()

    def test_rate_limit_exhaustion_preserves_last_request_id(self) -> None:
        with patch("backend.app.cloud.urllib.request.urlopen", return_value=self.response({
            "errno": 31034, "request_id": "last-id",
        })) as request, patch("backend.app.cloud.time.sleep") as sleep:
            with self.assertRaises(BaiduAPIError) as caught:
                BaiduPanAdapter._json_request("https://example.invalid/?method=list", retry_safe=True)
        self.assertEqual(request.call_count, 11)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [60] * 10)
        self.assertEqual(caught.exception.request_id, "last-id")

    def test_error_codes_are_scoped_to_operation(self) -> None:
        for operation, code, retryable in [("uinfo", 42905, True), ("create", 42905, False),
                                           ("filemanager", 111, True), ("create", 111, False)]:
            with self.subTest(operation=operation, code=code), \
                 patch("backend.app.cloud.urllib.request.urlopen", return_value=self.response({"errno": code})):
                with self.assertRaises(BaiduAPIError) as caught:
                    BaiduPanAdapter._json_request(f"https://example.invalid/?method={operation}")
                self.assertEqual(caught.exception.retryable, retryable)

    def test_http_error_body_uses_common_parser(self) -> None:
        error = urllib.error.HTTPError("https://example.invalid/", 403, "Forbidden", {},
                                      io.BytesIO(json.dumps({"error_code": 31024, "error_msg": "no permission", "request_id": 999}).encode()))
        with patch("backend.app.cloud.urllib.request.urlopen", side_effect=error), \
             patch("backend.app.cloud.time.sleep") as sleep:
            with self.assertRaises(BaiduAPIError) as caught:
                BaiduPanAdapter._json_request("https://example.invalid/?method=upload", retry_safe=True)
        self.assertEqual(caught.exception.code, "31024")
        self.assertEqual(caught.exception.http_status, 403)
        self.assertEqual(caught.exception.request_id, "999")
        self.assertIn("no permission", str(caught.exception))
        sleep.assert_not_called()

    def test_non_json_http_error_retains_header_id_and_retries(self) -> None:
        error = urllib.error.HTTPError("https://example.invalid/", 503, "Unavailable",
                                      {"X-Request-Id": "header-id"}, io.BytesIO(b"<html>error</html>"))
        with patch("backend.app.cloud.urllib.request.urlopen", side_effect=[error, self.response({"errno": 0})]), \
             patch("backend.app.cloud.time.sleep") as sleep, \
             self.assertLogs("backend.app.cloud", level="WARNING") as logs:
            BaiduPanAdapter._json_request("https://example.invalid/", retry_safe=True)
        sleep.assert_called_once_with(60)
        self.assertIn("request_id=header-id", " ".join(logs.output))

    def test_error_message_and_logs_redact_credentials(self) -> None:
        secret = "sample-private-token"
        url = f"https://example.invalid/?method=list&access_token={secret}"
        response = self.response({"errno": -6, "errmsg": f"invalid {secret} url={url}", "request_id": "safe-id"})
        with patch("backend.app.cloud.urllib.request.urlopen", return_value=response), \
             self.assertLogs("backend.app.cloud", level="WARNING") as logs:
            with self.assertRaises(BaiduAPIError) as caught:
                BaiduPanAdapter._json_request(url)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, " ".join(logs.output))
        self.assertNotIn("https://", str(caught.exception))

    def test_oauth_error_is_parsed_and_secret_is_redacted(self) -> None:
        with patch("backend.app.cloud.urllib.request.urlopen", return_value=self.response({
            "error": "invalid_grant", "error_description": "invalid secret-value", "request_id": "oauth-id",
        })):
            with self.assertRaises(BaiduAPIError) as caught:
                BaiduPanAdapter._json_request("https://example.invalid/oauth/token?client_secret=secret-value")
        self.assertEqual(caught.exception.category, "授权异常")
        self.assertEqual(caught.exception.request_id, "oauth-id")
        self.assertNotIn("secret-value", str(caught.exception))

    def test_md5_mismatch_and_missing_md5_retry_same_part(self) -> None:
        digest = hashlib.md5(b"contents").hexdigest()
        with patch("backend.app.cloud.urllib.request.urlopen", side_effect=[
            self.response({"md5": "0" * 32, "request_id": "bad-digest"}),
            self.response({"errno": 0}), self.response({"md5": digest.upper()}),
        ]) as request, patch("backend.app.cloud.time.sleep") as sleep:
            result = BaiduPanAdapter._json_request("https://example.invalid/?method=upload", b"contents",
                                                   retry_safe=True, expected_md5=digest)
        self.assertEqual(result["md5"], digest.upper())
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [60, 60])
        self.assertEqual(request.call_count, 3)
        self.assertTrue(all(call.args[0] is request.call_args.args[0] for call in request.call_args_list))

    def test_invalid_success_response_cannot_confirm_file(self) -> None:
        with patch("backend.app.cloud.urllib.request.urlopen", return_value=self.response({"errno": 0, "request_id": "invalid-id"})):
            with self.assertRaisesRegex(BaiduAPIError, "缺少 fs_id.*request_id=invalid-id"):
                BaiduPanAdapter._json_request("https://example.invalid/?method=create")

    def test_existing_file_business_error_is_reconciled(self) -> None:
        adapter = BaiduPanAdapter(BaiduConfig())
        existing = {"path": "/apps/test/file.txt", "size": 4, "isdir": 0, "fs_id": "existing-id"}
        with patch.object(adapter, "_api", side_effect=[
            BaiduAPIError("create", "目标已存在", "conflict", code="-8", request_id="conflict-id"),
            {"list": [existing]},
        ]) as request, patch("backend.app.cloud.time.sleep") as sleep:
            result = adapter._complete_upload({"path": existing["path"], "size": "4", "rtype": "0"})
        self.assertEqual(result, existing)
        self.assertEqual([call.args[0]["method"] for call in request.call_args_list], ["create", "list"])
        sleep.assert_not_called()

    def test_bad_md5_never_advances_progress_or_merges_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "upload.txt"
            source.write_bytes(b"contents")
            adapter = BaiduPanAdapter(BaiduConfig(app_name="NAS文件管理"))
            adapter._api = Mock(return_value={"uploadid": "upload-id", "block_list": [0]})
            progress = Mock()
            original = adapter._json_request
            def request(url: str, *args, **kwargs) -> dict:
                if "method=locateupload" in url:
                    return {"host": "https://d.pcs.baidu.com"}
                return original(url, *args, **kwargs)
            with patch.object(adapter, "list_directory", return_value=[]), \
                 patch.object(adapter, "_access_token", return_value="test-token"), \
                 patch.object(adapter, "_json_request", side_effect=request), \
                 patch("backend.app.cloud.urllib.request.urlopen", return_value=self.response({"md5": "bad", "request_id": "md5-id"})), \
                 patch("backend.app.cloud.time.sleep") as sleep:
                with self.assertRaisesRegex(BaiduAPIError, "分片校验失败.*request_id=md5-id"):
                    adapter.upload_file(source, "/apps/NAS文件管理", progress)
            progress.assert_not_called()
            self.assertEqual(adapter._api.call_count, 1)
            self.assertEqual(sleep.call_count, 10)


if __name__ == "__main__":
    unittest.main()
