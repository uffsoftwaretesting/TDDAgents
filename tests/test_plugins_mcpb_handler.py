"""The MCPB handler (`app/utils/plugins/mcpb_handler.py`): load, cache, user config, download."""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import stat
import zipfile
from collections.abc import Callable
from typing import Any

import pytest

from app.utils.dxt import zip as dxt_zip
from app.utils.plugins import mcpb_handler as mh
from app.utils.secure_storage.plain_text_storage import plain_text_storage
from app.utils.settings.settings import SettingsLocations

PLUGIN_ID = "weather@market"


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def locations(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    return SettingsLocations(project_root=project, home=tmp_path / "home", managed_dir=tmp_path / "managed")


@pytest.fixture
def plugin_dir(tmp_path):
    path = tmp_path / "plugin"
    path.mkdir()
    return path


def _user_settings(locations):
    path = locations.user_config_dir / "settings.json"
    return json.loads(path.read_text()) if path.exists() else None


def manifest(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "manifest_version": "0.3",
        "name": "weather",
        "version": "1.0.0",
        "description": "Weather tools",
        "author": {"name": "Ada"},
        "server": {
            "type": "binary",
            "entry_point": "bin/server",
            "mcp_config": {"command": "${__dirname}/bin/server", "args": ["--key=${user_config.key}"]},
        },
    }
    base.update(overrides)
    return base


def bundle(m: dict[str, Any], extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(m))
        exe = zipfile.ZipInfo("bin/server")
        exe.create_system = 3
        exe.external_attr = 0o100755 << 16
        archive.writestr(exe, b"\x7fELF binary")
        archive.writestr("bin/", b"")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


NO_CONFIG = manifest(server={
    "type": "node", "entry_point": "index.js", "mcp_config": {"command": "node", "args": ["${__dirname}/index.js"]},
})
WITH_CONFIG = manifest(user_config={
    "key": {"type": "string", "title": "API key", "description": "d", "required": True, "sensitive": True},
    "units": {"type": "string", "title": "Units", "description": "d"},
})


def test_is_mcpb_source() -> None:
    assert mh.is_mcpb_source("./s.mcpb") and mh.is_mcpb_source("https://x/s.dxt")
    assert not mh.is_mcpb_source("./s.zip")


def test_load_extracts_and_generates_the_config(plugin_dir, locations) -> None:
    data = bundle(NO_CONFIG, {"README.md": "﻿hello".encode()})
    (plugin_dir / "s.mcpb").write_bytes(data)
    progress: list[str] = []
    result = run(mh.load_mcpb_file("./s.mcpb", str(plugin_dir), PLUGIN_ID, locations, progress.append))
    assert isinstance(result, mh.McpbLoadResult)
    expected_dir = os.path.join(str(plugin_dir), ".mcpb-cache", result.content_hash)
    assert result.extracted_path == expected_dir and len(result.content_hash) == 16
    assert result.mcp_config == {"command": "node", "args": [f"{expected_dir}/index.js"]}
    server = os.path.join(expected_dir, "bin", "server")
    assert stat.S_IMODE(os.stat(server).st_mode) == 0o755
    assert open(os.path.join(expected_dir, "README.md"), encoding="utf-8").read() == "hello"
    assert not os.path.isfile(os.path.join(expected_dir, "bin"))
    assert progress[0] == "Loading ./s.mcpb..." and progress[-1] == "Generating MCP server configuration..."
    metadata_files = [f for f in os.listdir(plugin_dir / ".mcpb-cache") if f.endswith(".metadata.json")]
    assert len(metadata_files) == 1


def test_a_cached_bundle_is_reused_until_the_file_changes(plugin_dir, locations, monkeypatch) -> None:
    path = plugin_dir / "s.mcpb"
    path.write_bytes(bundle(NO_CONFIG))
    first = run(mh.load_mcpb_file("./s.mcpb", str(plugin_dir), PLUGIN_ID, locations))
    assert not run(mh.check_mcpb_changed("./s.mcpb", str(plugin_dir)))

    calls: list[bytes] = []
    real_unzip = dxt_zip.unzip_file

    async def counting_unzip(data: bytes) -> dict[str, bytes]:
        calls.append(data)
        return await real_unzip(data)

    monkeypatch.setattr(mh, "unzip_file", counting_unzip)
    again = run(mh.load_mcpb_file("./s.mcpb", str(plugin_dir), PLUGIN_ID, locations))
    assert again == first and calls == []

    future = os.stat(path).st_mtime + 60
    os.utime(path, (future, future))
    assert run(mh.check_mcpb_changed("./s.mcpb", str(plugin_dir)))
    run(mh.load_mcpb_file("./s.mcpb", str(plugin_dir), PLUGIN_ID, locations))
    assert len(calls) == 1


def test_check_mcpb_changed_without_record_or_extraction(plugin_dir, locations) -> None:
    assert run(mh.check_mcpb_changed("./s.mcpb", str(plugin_dir)))
    (plugin_dir / "s.mcpb").write_bytes(bundle(NO_CONFIG))
    result = run(mh.load_mcpb_file("./s.mcpb", str(plugin_dir), PLUGIN_ID, locations))
    shutil.rmtree(result.extracted_path)
    assert run(mh.check_mcpb_changed("./s.mcpb", str(plugin_dir)))
    os.remove(plugin_dir / "s.mcpb")
    assert run(mh.check_mcpb_changed("./s.mcpb", str(plugin_dir)))


def test_needs_config_then_provided_values_are_saved_split(plugin_dir, locations) -> None:
    (plugin_dir / "s.mcpb").write_bytes(bundle(WITH_CONFIG))
    first = run(mh.load_mcpb_file("./s.mcpb", str(plugin_dir), PLUGIN_ID, locations))
    assert isinstance(first, mh.McpbNeedsConfigResult)
    assert first.status == "needs-config" and first.existing_config == {}
    assert first.validation_errors == ["API key is required but not provided"]
    assert set(first.config_schema) == {"key", "units"}

    provided = {"key": "s3cret", "units": "metric"}
    loaded = run(mh.load_mcpb_file("./s.mcpb", str(plugin_dir), PLUGIN_ID, locations, None, provided))
    assert isinstance(loaded, mh.McpbLoadResult)
    assert loaded.mcp_config["args"] == ["--key=s3cret"]
    stored = plain_text_storage.read() or {}
    assert stored["pluginSecrets"][f"{PLUGIN_ID}/weather"] == {"key": "s3cret"}
    assert _user_settings(locations)["pluginConfigs"][PLUGIN_ID]["mcpServers"]["weather"] == {"units": "metric"}
    assert mh.load_mcp_server_user_config(PLUGIN_ID, "weather", locations) == provided

    # Cached path: saved values satisfy it, and a forced dialog asks again with them.
    cached = run(mh.load_mcpb_file("./s.mcpb", str(plugin_dir), PLUGIN_ID, locations))
    assert isinstance(cached, mh.McpbLoadResult) and cached.mcp_config["args"] == ["--key=s3cret"]
    forced = run(mh.load_mcpb_file("./s.mcpb", str(plugin_dir), PLUGIN_ID, locations, force_config_dialog=True))
    assert isinstance(forced, mh.McpbNeedsConfigResult)
    assert forced.existing_config == provided and forced.validation_errors == []


def test_save_scrubs_keys_that_moved_between_stores(locations) -> None:
    schema_sensitive = {"tok": {"type": "string", "title": "T", "description": "d", "sensitive": True}}
    schema_plain = {"tok": {"type": "string", "title": "T", "description": "d"}}
    mh.save_mcp_server_user_config(PLUGIN_ID, "srv", {"tok": "plain"}, schema_plain, locations)
    assert _user_settings(locations)["pluginConfigs"][PLUGIN_ID]["mcpServers"]["srv"] == {"tok": "plain"}
    mh.save_mcp_server_user_config(PLUGIN_ID, "srv", {"tok": 7}, schema_sensitive, locations)
    assert (plain_text_storage.read() or {})["pluginSecrets"][f"{PLUGIN_ID}/srv"] == {"tok": "7"}
    assert "tok" not in _user_settings(locations)["pluginConfigs"][PLUGIN_ID]["mcpServers"]["srv"]
    mh.save_mcp_server_user_config(PLUGIN_ID, "srv", {"tok": "back"}, schema_plain, locations)
    assert (plain_text_storage.read() or {})["pluginSecrets"][f"{PLUGIN_ID}/srv"] == {}
    assert mh.load_mcp_server_user_config(PLUGIN_ID, "srv", locations) == {"tok": "back"}


def test_load_user_config_is_none_without_entries(locations) -> None:
    assert mh.load_mcp_server_user_config(PLUGIN_ID, "nothing", locations) is None


def test_save_failure_is_wrapped(locations, monkeypatch) -> None:
    class Failing:
        def read(self) -> None:
            return None

        def update(self, _data: Any) -> Any:
            return type("R", (), {"success": False, "warning": None})()

    monkeypatch.setattr(mh, "get_secure_storage", lambda: Failing())
    schema = {"tok": {"type": "string", "title": "T", "description": "d", "sensitive": True}}
    with pytest.raises(RuntimeError, match=(
        f"^Failed to save user configuration for {PLUGIN_ID}/srv: "
        f"Failed to save sensitive config to secure storage for {PLUGIN_ID}/srv$"
    )):
        mh.save_mcp_server_user_config(PLUGIN_ID, "srv", {"tok": "x"}, schema, locations)
    assert _user_settings(locations) is None


def test_errors_for_missing_file_manifest_and_bad_manifest(plugin_dir, locations) -> None:
    with pytest.raises(RuntimeError, match="^MCPB file not found: "):
        run(mh.load_mcpb_file("./none.mcpb", str(plugin_dir), PLUGIN_ID, locations))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("other.txt", "x")
    (plugin_dir / "empty.mcpb").write_bytes(buffer.getvalue())
    with pytest.raises(RuntimeError, match="^No manifest.json found in MCPB file$"):
        run(mh.load_mcpb_file("./empty.mcpb", str(plugin_dir), PLUGIN_ID, locations))
    (plugin_dir / "bad.mcpb").write_bytes(bundle(manifest(version=1)))
    with pytest.raises(ValueError, match="^Invalid manifest: version: Expected string, received number$"):
        run(mh.load_mcpb_file("./bad.mcpb", str(plugin_dir), PLUGIN_ID, locations))


def test_url_sources_download_once(plugin_dir, locations, monkeypatch) -> None:
    data = bundle(NO_CONFIG)
    fetched: list[str] = []

    async def fake_get(url: str, timeout_ms: int, on_bytes: Callable[[int, int | None], None]) -> bytes:
        fetched.append(url)
        assert timeout_ms == 120000
        on_bytes(len(data) // 2, len(data))
        on_bytes(len(data), len(data))
        return data

    monkeypatch.setattr(mh, "http_get_bytes", fake_get)
    progress: list[str] = []
    url = "https://example.com/weather.mcpb"
    result = run(mh.load_mcpb_file(url, str(plugin_dir), PLUGIN_ID, locations, progress.append))
    assert isinstance(result, mh.McpbLoadResult)
    assert progress[:4] == [f"Downloading {url}...", "Downloading... 50%", "Downloading... 100%", "Download complete"]
    saved = [f for f in os.listdir(plugin_dir / ".mcpb-cache") if f.endswith(".mcpb")]
    assert len(saved) == 1 and (plugin_dir / ".mcpb-cache" / saved[0]).read_bytes() == data
    run(mh.load_mcpb_file(url, str(plugin_dir), PLUGIN_ID, locations))
    assert fetched == [url]


def test_download_failure_is_wrapped(plugin_dir, locations, monkeypatch) -> None:
    async def failing(url: str, timeout_ms: int, on_bytes: Callable[[int, int | None], None]) -> bytes:
        raise OSError("Request failed with status code 404")

    monkeypatch.setattr(mh, "http_get_bytes", failing)
    with pytest.raises(RuntimeError, match=(
        "^Failed to download MCPB file from https://x/y.mcpb: Request failed with status code 404$"
    )):
        run(mh.load_mcpb_file("https://x/y.mcpb", str(plugin_dir), PLUGIN_ID, locations))


def test_a_corrupt_metadata_record_is_reextracted(plugin_dir, locations) -> None:
    (plugin_dir / "s.mcpb").write_bytes(bundle(NO_CONFIG))
    run(mh.load_mcpb_file("./s.mcpb", str(plugin_dir), PLUGIN_ID, locations))
    cache = plugin_dir / ".mcpb-cache"
    record = next(p for p in cache.iterdir() if p.name.endswith(".metadata.json"))
    record.write_text("{not json")
    assert run(mh.check_mcpb_changed("./s.mcpb", str(plugin_dir)))
    record.write_text("{}")
    assert run(mh.check_mcpb_changed("./s.mcpb", str(plugin_dir)))
