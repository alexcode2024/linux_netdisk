import json
from pathlib import Path

from backend.app.cloud import CloudAdapterError, QuarkOfficialAdapter
from backend.app.config import QuarkConfig


def make_adapter(tmp_path: Path) -> QuarkOfficialAdapter:
    home = tmp_path / "home"
    home.mkdir()
    script = tmp_path / "quark-drive.cjs"
    script.write_text(
        """
const args = process.argv.slice(2);
const command = args[0];
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
  output({type: 'progress', data: {current: 3, total: 5}});
  output({code: 0, type: 'result', data: {successCount: 1, failedCount: 0, fids: ['uploaded-id']}});
}
""",
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
