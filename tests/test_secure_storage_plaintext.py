"""The plaintext secure storage (`app/utils/secure_storage/`), upstream's `plainTextStorage.ts`."""

from __future__ import annotations

import asyncio
import json
import os
import stat

from app.utils.secure_storage.index import get_secure_storage
from app.utils.secure_storage.plain_text_storage import (
    PLAINTEXT_WARNING,
    get_storage_path,
    plain_text_storage,
)


def test_storage_path_is_the_credentials_file_under_the_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "home"))
    storage_dir, storage_path = get_storage_path()
    assert storage_dir == tmp_path / "home"
    assert storage_path == tmp_path / "home" / ".credentials.json"


def test_read_is_none_without_a_file():
    assert plain_text_storage.read() is None
    assert asyncio.run(plain_text_storage.read_async()) is None


def test_update_writes_the_document_mode_0600_and_warns(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "fresh"))
    document = {"pluginSecrets": {"p@m": {"token": "s3cret"}}}
    result = plain_text_storage.update(document)
    assert result.success is True
    assert result.warning == PLAINTEXT_WARNING
    _, path = get_storage_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text(encoding="utf-8")) == document
    assert plain_text_storage.read() == document
    assert asyncio.run(plain_text_storage.read_async()) == document


def test_malformed_or_non_object_file_reads_as_none():
    storage_dir, path = get_storage_path()
    storage_dir.mkdir(parents=True, exist_ok=True)
    path.write_text("{nope", encoding="utf-8")
    assert plain_text_storage.read() is None
    path.write_text("[1, 2]", encoding="utf-8")
    assert plain_text_storage.read() is None


def test_update_fails_when_the_directory_cannot_be_created(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(blocker / "sub"))
    result = plain_text_storage.update({"a": 1})
    assert result.success is False
    assert result.warning is None


def test_delete_removes_the_file_and_is_true_when_absent():
    plain_text_storage.update({"a": 1})
    _, path = get_storage_path()
    assert path.exists()
    assert plain_text_storage.delete() is True
    assert not path.exists()
    assert plain_text_storage.delete() is True


def test_delete_reports_false_when_the_path_cannot_be_unlinked():
    _, path = get_storage_path()
    path.mkdir(parents=True)  # a directory where the file should be
    assert plain_text_storage.delete() is False


def test_get_secure_storage_is_the_plaintext_storage():
    storage = get_secure_storage()
    assert storage is plain_text_storage
    assert storage.name == "plaintext"
