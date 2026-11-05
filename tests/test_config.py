from pathlib import Path

import pytest

from backend.app.config import Settings


def test_config_requires_absolute_allowed_root(tmp_path: Path):
    path = tmp_path / 'app.yaml'
    path.write_text('storage:\n  allowed_roots:\n    - id: downloads\n      name: Downloads\n      container_path: relative\n')
    with pytest.raises(RuntimeError):
        Settings.from_file(path)
