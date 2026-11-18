from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from backend.app.cloud import CloudAdapterError, QuarkOfficialAdapter
from backend.app.config import QuarkConfig
from backend.app.schemas import QuarkShareRequest


class QuarkShareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = QuarkOfficialAdapter(QuarkConfig())

    def result(self, data: dict, code: int = 0, message: str = "成功") -> str:
        return json.dumps({"code": code, "type": "result", "action": "share", "data": data, "msg": message})

    def test_private_folder_share_uses_official_command_and_server_passcode(self) -> None:
        with patch.object(self.adapter, "_run", return_value=self.result({
            "share_url": "https://pan.quark.cn/s/example", "passcode": "aB12",
        })) as command:
            result = self.adapter.share("folder-123")
        command.assert_called_once_with("share", "folder-123", "--url-type", "2", "--expired-type", "1", timeout=120)
        self.assertEqual(result.share_url, "https://pan.quark.cn/s/example")
        self.assertEqual(result.passcode, "aB12")

    def test_public_file_share_and_permanent_expiration(self) -> None:
        with patch.object(self.adapter, "_run", return_value=self.result({"share_url": "https://pan.quark.cn/s/example"})) as command:
            result = self.adapter.share("file-123", 1, 1)
        self.assertIsNone(result.passcode)
        self.assertEqual(command.call_args.args[-3:], ("1", "--expired-type", "1"))

    def test_official_composite_fid_is_passed_unchanged(self) -> None:
        file_id = "~1abcdefghijklmnopqrstuv|0123456789_ABC-def"
        payload = QuarkShareRequest(file_id=file_id)
        with patch.object(self.adapter, "_run", return_value=self.result({
            "share_url": "https://pan.quark.cn/s/example", "passcode": "aB12",
        })) as command:
            self.adapter.share(payload.file_id)
        self.assertEqual(command.call_args.args[1], file_id)

    def test_private_share_requires_passcode(self) -> None:
        for passcode in [None, "", " ", 1234]:
            with self.subTest(passcode=passcode), patch.object(self.adapter, "_run", return_value=self.result({
                "share_url": "https://pan.quark.cn/s/example", "passcode": passcode,
            })):
                with self.assertRaisesRegex(CloudAdapterError, "未返回提取码"):
                    self.adapter.share("folder-123")

    def test_rejects_missing_or_unsafe_share_urls(self) -> None:
        for url in [None, "javascript:alert(1)", "http://pan.quark.cn/s/example", "https://pan.quark.cn.evil.test/s/example",
                    "https://pan.quark.cn@evil.test/s/example", "https://pan.quark.cn/s/", "https://pan.quark.cn/", "https://["]:
            with self.subTest(url=url), patch.object(self.adapter, "_run", return_value=self.result({"share_url": url})):
                with self.assertRaises(CloudAdapterError):
                    self.adapter.share("folder-123", 1)

    def test_share_failure_is_reported_without_retry(self) -> None:
        with patch.object(self.adapter, "_run", return_value=self.result({}, -403, "分享操作失败")) as command:
            with self.assertRaisesRegex(CloudAdapterError, "分享操作失败"):
                self.adapter.share("folder-123")
        self.assertEqual(command.call_count, 1)

    def test_rejects_root_and_option_injection_before_running_cli(self) -> None:
        for file_id in ["0", "--url-type", "", "../test", "folder id", "file\n123", "a" * 301]:
            with self.subTest(file_id=file_id), patch.object(self.adapter, "_run") as command:
                with self.assertRaises(CloudAdapterError):
                    self.adapter.share(file_id)
                command.assert_not_called()
                with self.assertRaises(ValidationError):
                    QuarkShareRequest(file_id=file_id)

    def test_rejects_invalid_share_options(self) -> None:
        for url_type, expired_type in [(0, 3), (3, 3), (2, 0), (2, 8)]:
            with self.subTest(url_type=url_type, expired_type=expired_type), patch.object(self.adapter, "_run") as command:
                with self.assertRaises(CloudAdapterError):
                    self.adapter.share("folder-123", url_type, expired_type)
                command.assert_not_called()
                with self.assertRaises(ValidationError):
                    QuarkShareRequest(file_id="folder-123", url_type=url_type, expired_type=expired_type)

    def test_browse_preserves_optional_metadata_and_does_not_invent_folder_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_path = Path(directory)
            artifact = task_path / "files.jsonl"
            artifact.write_text("\n".join(json.dumps(entry) for entry in [
                {"fid": "folder-123", "filename": "Folder", "file_type": "0", "size": 4096, "updated_at": 1700000000000},
                {"fid": "file-123", "filename": "notes.txt", "file_type": "1", "size": 12, "updated_at": 1700000000000},
                {"fid": "file-456", "filename": "empty.txt", "file_type": "1", "size": 0},
                {"fid": "file-789", "filename": "unknown.txt", "file_type": "1"},
            ]), encoding="utf-8")
            with patch("backend.app.cloud.tempfile.TemporaryDirectory") as temporary, patch.object(
                self.adapter, "_run", return_value=self.result({"file_path": str(artifact)}),
            ):
                temporary.return_value.__enter__.return_value = directory
                entries = self.adapter.list_directory("0")
        self.assertIsNone(entries[0].size)
        self.assertEqual(entries[1].size, 12)
        self.assertEqual(entries[1].modified_at, 1700000000000)
        self.assertEqual(entries[2].size, 0)
        self.assertIsNone(entries[3].size)
        self.assertIsNone(entries[3].modified_at)


if __name__ == "__main__":
    unittest.main()
