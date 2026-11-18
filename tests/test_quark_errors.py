from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.cloud import CloudAdapterError, QuarkCLIError, QuarkOfficialAdapter
from backend.app.config import QuarkConfig, RetryConfig


class QuarkErrorHandlingTests(unittest.TestCase):
    record_id = "659c7c1e-4b09-4cd9-9d4b-47a23a9809ce"

    def setUp(self) -> None:
        self.adapter = QuarkOfficialAdapter(QuarkConfig(
            retry=RetryConfig(max_attempts=3, initial_delay_seconds=5, max_delay_seconds=12),
        ))
        self.progress = Mock()

    def failure(self, code: int, message: str, record_id: str | None = None) -> str:
        return "\n".join(json.dumps(event) for event in [
            {"type": "list", "code": code, "msg": message, "data": {"recordId": record_id or self.record_id}},
            {"type": "result", "code": -204, "msg": "上传失败", "data": {}},
        ])

    def success(self, record_id: str | None = None) -> str:
        return "\n".join(json.dumps(event) for event in [
            {"type": "list", "code": 0, "data": {"recordId": record_id or self.record_id, "fileId": "uploaded-fid"}},
            {"type": "result", "code": 0, "data": {"recordId": record_id or self.record_id}},
        ])

    def test_server_error_in_stderr_resumes_original_record(self) -> None:
        error = CloudAdapterError(
            f"[ERROR][UploadManager] Task {self.record_id} upload failed: [53000] 服务器内部异常{{0}}",
        )
        with patch.object(self.adapter, "_run", side_effect=[error, self.success()]) as run, \
             patch("backend.app.cloud.time.sleep") as sleep:
            result = self.adapter.upload_file(Path("source.mp4"), "target-fid", self.progress)
        self.assertEqual(result.remote_id, "uploaded-fid")
        self.assertEqual(run.call_args.args, ("upload", "resume", "--record-id", self.record_id))
        sleep.assert_called_once_with(5)

    def test_server_error_in_json_resumes_and_retries_same_record(self) -> None:
        with patch.object(self.adapter, "_run", side_effect=[
            self.failure(53000, "服务器内部异常{0}"),
            self.failure(53000, "服务器内部异常{0}"), self.success(),
        ]) as run, patch("backend.app.cloud.time.sleep") as sleep:
            result = self.adapter.upload_file(Path("source.mp4"), "target-fid", self.progress)
        self.assertTrue(result.success)
        self.assertEqual([call.args for call in run.call_args_list[1:]], [
            ("upload", "resume", "--record-id", self.record_id),
        ] * 2)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 10])

    def test_server_error_retries_are_bounded_and_preserve_code(self) -> None:
        with patch.object(self.adapter, "_run", return_value=self.failure(53000, "服务器内部异常{0}")) as run, \
             patch("backend.app.cloud.time.sleep") as sleep:
            with self.assertRaises(QuarkCLIError) as caught:
                self.adapter.upload_file(Path("source.mp4"), "target-fid", self.progress)
        self.assertEqual(caught.exception.code, "53000")
        self.assertEqual(caught.exception.record_id, self.record_id)
        self.assertEqual(run.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 10, 12])

    def test_server_error_without_record_does_not_start_new_upload(self) -> None:
        output = json.dumps({"type": "result", "code": 53000, "msg": "服务器内部异常{0}"})
        with patch.object(self.adapter, "_run", return_value=output) as run, \
             patch("backend.app.cloud.time.sleep") as sleep:
            with self.assertRaises(QuarkCLIError):
                self.adapter.upload_file(Path("source.mp4"), "target-fid", self.progress)
        run.assert_called_once()
        sleep.assert_not_called()

    def test_capacity_and_auth_errors_do_not_retry(self) -> None:
        for code in (32003, 32004, 31003, -401):
            with self.subTest(code=code), \
                 patch.object(self.adapter, "_run", return_value=self.failure(code, "业务失败")) as run, \
                 patch("backend.app.cloud.time.sleep") as sleep:
                with self.assertRaises(QuarkCLIError) as caught:
                    self.adapter.upload_file(Path("source.mp4"), "target-fid", self.progress)
                self.assertEqual(caught.exception.code, str(code))
                run.assert_called_once()
                sleep.assert_not_called()

    def test_resume_stops_when_error_changes_to_capacity_error(self) -> None:
        with patch.object(self.adapter, "_run", side_effect=[
            self.failure(53000, "服务器内部异常{0}"), self.failure(32003, "空间不足"),
        ]) as run, patch("backend.app.cloud.time.sleep") as sleep:
            with self.assertRaises(QuarkCLIError) as caught:
                self.adapter.upload_file(Path("source.mp4"), "target-fid", self.progress)
        self.assertEqual(caught.exception.code, "32003")
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(5)

    def test_disabled_retries_preserve_initial_error(self) -> None:
        self.adapter.config.retry.max_attempts = 0
        with patch.object(self.adapter, "_run", return_value=self.failure(53000, "服务器内部异常{0}")) as run, \
             patch("backend.app.cloud.time.sleep") as sleep:
            with self.assertRaises(QuarkCLIError) as caught:
                self.adapter.upload_file(Path("source.mp4"), "target-fid", self.progress)
        self.assertEqual(caught.exception.code, "53000")
        run.assert_called_once()
        sleep.assert_not_called()

    def test_resume_requires_matching_success_record_and_file_id(self) -> None:
        for output in (
            self.success("another-record"),
            json.dumps({"type": "result", "code": 0, "data": {"recordId": self.record_id}}),
        ):
            with self.subTest(output=output), self.assertRaises(CloudAdapterError):
                self.adapter._resume_result(output, self.record_id)


if __name__ == "__main__":
    unittest.main()
