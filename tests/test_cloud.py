import json
from pathlib import Path
from unittest.mock import patch

from backend.app.cloud import CloudAdapterError, QuarkOfficialAdapter
from backend.app.config import QuarkConfig


def make_adapter(tmp_path: Path, upload_mode: str = "success") -> QuarkOfficialAdapter:
    home = tmp_path / "home"
    home.mkdir()
    script = tmp_path / "quark-drive.cjs"
    script.write_text(
        """
const args = process.argv.slice(2);
const command = args[0];
const uploadMode = UPLOAD_MODE;
const output = (value) => console.log(JSON.stringify(value));
if (command === 'get-user-info') output({code: 0, type: 'result', data: {}});
if (command === 'browse') {
  const dir = args[args.indexOf('--task-dir') + 1] + '/browse';
  require('fs').mkdirSync(dir, {recursive: true});
  const file = dir + '/entries.jsonl';
  require('fs').writeFileSync(file, [
    JSON.stringify({fid: 'folder-id', filename: 'Folder', file_type: '0'}),
    JSON.stringify({fid: 'file-id', filename: 'notes.txt', file_type: '1'})
  ].join('\\n'));
  output({code: 0, type: 'result', data: {file_path: file}});
}
if (command === 'create-folder') output({code: 0, type: 'result', data: {fid: 'created-id', full_path: '夸克网盘/Folder'}});
if (command === 'upload') {
  if (args[1] === 'resume') {
    if (uploadMode === 'retry-fails') {
      console.error('[ERROR][UploadManager] Task 123e4567-e89b-12d3-a456-426614174000 upload failed: [11005] SocketTimeoutException: Read timed out');
      process.exitCode = 1;
    } else if (uploadMode === 'retry-json-fails' && !require('fs').existsSync(process.env.HOME + '/resumed-once')) {
      require('fs').writeFileSync(process.env.HOME + '/resumed-once', '1');
      output({code: 11005, msg: 'SocketTimeoutException: Read timed out', data: {recordId: '123e4567-e89b-12d3-a456-426614174000'}, type: 'list'});
      output({code: -204, msg: '上传操作失败', data: {}, type: 'result'});
    } else {
      output({type: 'progress', data: {current: 5, total: 5}});
      output({code: 0, type: 'list', data: {recordId: '123e4567-e89b-12d3-a456-426614174000', fileId: 'uploaded-id'}});
      output({code: 0, type: 'result', data: {recordId: '123e4567-e89b-12d3-a456-426614174000', fileName: 'source.txt'}});
    }
  } else if (uploadMode !== 'success') {
    output({type: 'progress', data: {current: 2, total: 5}});
    if (uploadMode === 'server-json-exit') {
      output({code: 53000, msg: '服务器内部异常{0}', data: {recordId: '123e4567-e89b-12d3-a456-426614174000'}, type: 'list'});
      output({code: -204, msg: '上传失败', data: {}, type: 'result'});
      console.error('Upload failed');
      process.exitCode = 1;
      return;
    }
    const failure = uploadMode === 'business-failure' ? 'permission denied' :
      uploadMode === 'server-error' ? '[53000] 服务器内部异常{0}' : '[11005] java.net.SocketTimeoutException: Read timed out';
    console.error(`[ERROR][UploadManager] Task 123e4567-e89b-12d3-a456-426614174000 upload failed: ${failure}`);
    process.exitCode = 1;
  } else {
  output({type: 'progress', data: {current: 3, total: 5}});
  output({code: 0, type: 'result', data: {successCount: 1, failedCount: 0, fids: ['uploaded-id']}});
  }
}
""".replace("UPLOAD_MODE", json.dumps(upload_mode)),
        encoding="utf-8",
    )
    return QuarkOfficialAdapter(QuarkConfig(enabled=True, script_path=script, home=home))


def test_official_browse_reads_artifact_and_fid_fields(tmp_path: Path):
    entries = make_adapter(tmp_path).list_directory("0", "/")
    assert [(entry.id, entry.name, entry.type, entry.path) for entry in entries] == [
        ("folder-id", "Folder", "directory", "/Folder"),
        ("file-id", "notes.txt", "file", "/notes.txt"),
    ]


def test_official_upload_reports_progress_and_success(tmp_path: Path):
    adapter = make_adapter(tmp_path)
    progress: list[int] = []
    result = adapter.upload_file(tmp_path / "source.txt", "target-id", progress.append)
    assert result.success
    assert result.remote_id == "uploaded-id"
    assert progress == [3]


def test_quark_upload_resumes_saved_task_after_socket_timeout(tmp_path: Path):
    adapter = make_adapter(tmp_path, "retry-once")
    progress: list[int] = []

    with patch("backend.app.cloud.time.sleep") as sleep:
        result = adapter.upload_file(tmp_path / "source.txt", "target-id", progress.append)

    assert result.success
    assert result.remote_id == "uploaded-id"
    assert progress == [2, 5]
    sleep.assert_called_once_with(5)


def test_quark_upload_resumes_after_server_error(tmp_path: Path):
    adapter = make_adapter(tmp_path, "server-error")
    with patch("backend.app.cloud.time.sleep") as sleep:
        result = adapter.upload_file(tmp_path / "source.txt", "target-id", lambda uploaded: None)
    assert result.remote_id == "uploaded-id"
    sleep.assert_called_once_with(5)


def test_quark_upload_preserves_json_error_when_cli_exits_with_failure(tmp_path: Path):
    adapter = make_adapter(tmp_path, "server-json-exit")
    with patch("backend.app.cloud.time.sleep") as sleep:
        result = adapter.upload_file(tmp_path / "source.txt", "target-id", lambda uploaded: None)
    assert result.remote_id == "uploaded-id"
    sleep.assert_called_once_with(5)


def test_quark_upload_retries_transient_resumes_up_to_configured_limit(tmp_path: Path):
    adapter = make_adapter(tmp_path, "retry-fails")
    adapter.config.retry.max_attempts = 2
    progress: list[int] = []

    with patch("backend.app.cloud.time.sleep") as sleep:
        try:
            adapter.upload_file(tmp_path / "source.txt", "target-id", progress.append)
        except CloudAdapterError as exc:
            assert "SocketTimeoutException" in str(exc)
        else:
            raise AssertionError("expected upload timeout after exhausting resume attempts")

    assert progress == [2]
    assert [call.args[0] for call in sleep.call_args_list] == [5, 10]


def test_quark_upload_retries_timeout_reported_in_resume_json(tmp_path: Path):
    adapter = make_adapter(tmp_path, "retry-json-fails")
    progress: list[int] = []

    with patch("backend.app.cloud.time.sleep") as sleep:
        result = adapter.upload_file(tmp_path / "source.txt", "target-id", progress.append)

    assert result.success
    assert result.remote_id == "uploaded-id"
    assert progress == [2, 5]
    assert [call.args[0] for call in sleep.call_args_list] == [5, 10]


def test_quark_upload_does_not_resume_non_network_failures(tmp_path: Path):
    adapter = make_adapter(tmp_path, "business-failure")
    progress: list[int] = []

    with patch("backend.app.cloud.time.sleep") as sleep:
        try:
            adapter.upload_file(tmp_path / "source.txt", "target-id", progress.append)
        except CloudAdapterError as exc:
            assert "permission denied" in str(exc)
        else:
            raise AssertionError("expected non-network upload error")

    assert progress == [2]
    sleep.assert_not_called()


def test_official_result_rejects_failure_and_missing_result():
    for output in (
        json.dumps({"type": "result", "code": -603, "msg": "parent folder not found"}),
        "not-json",
    ):
        try:
            QuarkOfficialAdapter._result(output, "test")
        except CloudAdapterError:
            continue
        raise AssertionError("expected CloudAdapterError")
