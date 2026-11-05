from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.paths import safe_path, validate_relative_path
from backend.app.config import AllowedRoot


def test_validate_relative_path_rejects_escape():
    with pytest.raises(HTTPException):
        validate_relative_path('../outside')
    with pytest.raises(HTTPException):
        validate_relative_path('/etc/passwd')


def test_safe_path_rejects_symlink(tmp_path: Path):
    root = tmp_path / 'root'
    outside = tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (outside / 'secret.txt').write_text('secret')
    (root / 'link').symlink_to(outside, target_is_directory=True)
    config = AllowedRoot(id='test', name='Test', container_path=root)
    with pytest.raises(HTTPException):
        safe_path(config, 'link/secret.txt')


def test_safe_path_allows_regular_file(tmp_path: Path):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'hello.txt').write_text('hello')
    config = AllowedRoot(id='test', name='Test', container_path=root)
    assert safe_path(config, 'hello.txt').read_text() == 'hello'
