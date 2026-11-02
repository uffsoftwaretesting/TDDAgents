"""The MCPB/DXT helpers: zip limits and modes (`app/utils/dxt/zip.py`), the manifest schema and
config generation (`app/utils/dxt/mcpb.py`), manifest parsing (`app/utils/dxt/helpers.py`) and
the system directories (`app/utils/system_directories.py`)."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from typing import Any

import pytest

from app.utils import system_directories as sysdirs
from app.utils.dxt import helpers, mcpb
from app.utils.dxt import zip as dxt_zip


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def make_zip(entries: dict[str, tuple[bytes, int | None]]) -> bytes:
    """A zip whose entries carry Unix modes (`create_system = 3`) when a mode is given."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, (data, mode) in entries.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if mode is not None:
                info.create_system = 3
                info.external_attr = (mode & 0xFFFF) << 16
            else:
                info.create_system = 0
            archive.writestr(info, data)
    return buffer.getvalue()


def manifest(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "manifest_version": "0.3",
        "name": "weather",
        "version": "1.0.0",
        "description": "Weather tools",
        "author": {"name": "Ada Lovelace"},
        "server": {
            "type": "node",
            "entry_point": "server/index.js",
            "mcp_config": {"command": "node", "args": ["${__dirname}/server/index.js"]},
        },
    }
    base.update(overrides)
    return base


# ── zip.py ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("path", "safe"), [
    ("a/b.txt", True), ("./a", True), ("../x", False), ("a/../../x", False), ("/etc/passwd", False),
    ("a\\..\\x", False), ("a..b", True),
])
def test_is_path_safe(path: str, safe: bool) -> None:
    assert dxt_zip.is_path_safe(path) is safe


def test_unzip_returns_files_and_directory_entries() -> None:
    data = make_zip({"bin/": (b"", 0o40755), "bin/run": (b"#!/bin/sh\n", 0o100755), "a.txt": (b"hi", None)})
    files = run(dxt_zip.unzip_file(data))
    assert files == {"bin/": b"", "bin/run": b"#!/bin/sh\n", "a.txt": b"hi"}


def test_unzip_rejects_an_unsafe_path() -> None:
    data = make_zip({"../evil": (b"x", None)})
    with pytest.raises(ValueError, match='Unsafe file path detected: "../evil". Path traversal'):
        run(dxt_zip.unzip_file(data))


def test_unzip_rejects_a_zip_bomb_ratio() -> None:
    data = make_zip({"zeros": (b"\0" * 200_000, None)})
    with pytest.raises(ValueError, match=r"Suspicious compression ratio detected: \d+\.\d:1 \(max: 50:1\)"):
        run(dxt_zip.unzip_file(data))


def test_validate_zip_file_reports_the_last_failing_check() -> None:
    state = dxt_zip.ZipValidationState(compressed_size=10, file_count=dxt_zip.MAX_FILE_COUNT)
    result = dxt_zip.validate_zip_file("/abs", 0, state)
    assert result == dxt_zip.FileValidationResult(
        False, 'Unsafe file path detected: "/abs". Path traversal or absolute paths are not allowed.'
    )
    roomy = dxt_zip.ZipValidationState(compressed_size=1 << 40)
    big = dxt_zip.validate_zip_file("big", dxt_zip.MAX_FILE_SIZE + 1, roomy)
    assert big.error == 'File "big" is too large: 512MB (max: 512MB)'
    ok = dxt_zip.validate_zip_file("ok", 5, dxt_zip.ZipValidationState(compressed_size=1))
    assert ok == dxt_zip.FileValidationResult(True)


def test_validate_zip_file_count_limit() -> None:
    state = dxt_zip.ZipValidationState(compressed_size=1, file_count=dxt_zip.MAX_FILE_COUNT)
    assert dxt_zip.validate_zip_file("a", 0, state).error == (
        f"Archive contains too many files: {dxt_zip.MAX_FILE_COUNT + 1} (max: {dxt_zip.MAX_FILE_COUNT})"
    )


def test_parse_zip_modes_reads_unix_modes_only() -> None:
    data = make_zip({"run": (b"x", 0o100755), "plain": (b"y", None), "doc": (b"z", 0o100644)})
    assert dxt_zip.parse_zip_modes(data) == {"run": 0o100755, "doc": 0o100644}


def test_parse_zip_modes_on_garbage_is_empty() -> None:
    assert dxt_zip.parse_zip_modes(b"not a zip at all, definitely not" * 3) == {}


def test_read_and_unzip_file_wraps_errors(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        run(dxt_zip.read_and_unzip_file(str(tmp_path / "missing.zip")))
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"nope")
    with pytest.raises(RuntimeError, match="^Failed to read or unzip file: "):
        run(dxt_zip.read_and_unzip_file(str(bad)))
    good = tmp_path / "good.zip"
    good.write_bytes(make_zip({"f": (b"1", None)}))
    assert run(dxt_zip.read_and_unzip_file(str(good))) == {"f": b"1"}


# ── mcpb.py: the schema ───────────────────────────────────────────────────────

def test_a_valid_manifest_parses() -> None:
    result = mcpb.safe_parse_manifest(manifest(user_config={
        "key": {"type": "string", "title": "Key", "description": "API key", "sensitive": True, "required": True},
    }))
    assert result.success and result.data is not None and result.data["name"] == "weather"


def test_the_manifest_reports_zod3_issues_in_order() -> None:
    bad = manifest(version=3, author={"name": "A", "email": "not-an-email"}, homepage="nope", extra=1)
    result = mcpb.safe_parse_manifest(bad)
    assert not result.success
    assert [(i.path, i.message) for i in result.issues] == [
        (("version",), "Expected string, received number"),
        (("author", "email"), "Invalid email"),
        (("homepage",), "Invalid url"),
        ((), "Unrecognized key(s) in object: 'extra'"),
    ]


def test_missing_fields_read_required_and_literal_enum_messages() -> None:
    bad = manifest(manifest_version="0.2")
    del bad["description"]
    bad["server"] = {**bad["server"], "type": "ruby"}
    messages = [(i.path, i.message) for i in mcpb.safe_parse_manifest(bad).issues]
    assert messages == [
        (("manifest_version",), 'Invalid literal value, expected "0.3"'),
        (("description",), "Required"),
        (("server", "type"), "Invalid enum value. Expected 'python' | 'node' | 'binary', received 'ruby'"),
    ]


def test_the_version_refine_runs_only_when_nothing_aborted() -> None:
    no_version = manifest()
    del no_version["manifest_version"]
    issues = mcpb.safe_parse_manifest(no_version).issues
    assert [(i.path, i.message) for i in issues] == [
        ((), "Either 'dxt_version' (deprecated) or 'manifest_version' must be provided"),
    ]
    # A dirty-only issue (a string check) still lets the refine run; a type failure does not.
    dirty = {**no_version, "homepage": "nope"}
    assert [i.message for i in mcpb.safe_parse_manifest(dirty).issues] == [
        "Invalid url", "Either 'dxt_version' (deprecated) or 'manifest_version' must be provided",
    ]
    aborted = {**no_version, "name": 1}
    assert [i.message for i in mcpb.safe_parse_manifest(aborted).issues] == ["Expected string, received number"]


def test_union_and_regex_messages() -> None:
    bad = manifest(
        user_config={"k": {"type": "string", "title": "K", "description": "d", "default": {"x": 1}}},
        icons=[{"src": "a.png", "size": "big", "theme": ""}],
        localization={"resources": "x", "default_locale": "!"},
    )
    assert [(i.path, i.message) for i in mcpb.safe_parse_manifest(bad).issues] == [
        (("icons", 0, "size"), 'size must be in the format "WIDTHxHEIGHT" (e.g., "16x16")'),
        (("icons", 0, "theme"), "theme cannot be empty when provided"),
        (("localization", "resources"), 'resources must include a "${locale}" placeholder'),
        (("localization", "default_locale"), "default_locale must be a valid BCP 47 locale identifier"),
        (("user_config", "k", "default"), "Invalid input"),
    ]


def test_platform_override_entries_are_partial_and_strict() -> None:
    ok = manifest()
    ok["server"]["mcp_config"]["platform_overrides"] = {"win32": {"command": "node.exe"}}
    assert mcpb.safe_parse_manifest(ok).success
    bad = manifest()
    bad["server"]["mcp_config"]["platform_overrides"] = {"win32": {"cmd": "x"}}
    assert [(i.path, i.message) for i in mcpb.safe_parse_manifest(bad).issues] == [
        (("server", "mcp_config", "platform_overrides", "win32"), "Unrecognized key(s) in object: 'cmd'"),
    ]


# ── mcpb.py: config generation ───────────────────────────────────────────────

def test_replace_variables_strings_arrays_and_objects() -> None:
    variables: dict[str, str | list[str]] = {
        "__dirname": "/ext", "user_config.dirs": ["/a", "/b"], "user_config.flag": "true", "user_config.e": "",
    }
    value = {
        "cmd": "${__dirname}/bin",
        "args": ["--x", "${user_config.dirs}", "${user_config.flag}", "${user_config.e}", "${user_config.none}",
                 ["${__dirname}"]],
        "s": "dirs=${user_config.dirs}",
        "n": 3,
    }
    assert mcpb.replace_variables(value, variables) == {
        "cmd": "/ext/bin",
        "args": ["--x", "/a", "/b", "true", "${user_config.e}", "${user_config.none}", ["/ext"]],
        "s": "dirs=${user_config.dirs}",
        "n": 3,
    }


def test_replace_variables_honours_js_replacement_patterns() -> None:
    assert mcpb.replace_variables("a ${x} b", {"x": "$$-$&"}) == "a $-${x} b"


def test_get_mcp_config_substitutes_defaults_user_values_and_platform() -> None:
    m = manifest(user_config={
        "dirs": {"type": "directory", "title": "D", "description": "d", "multiple": True, "default": ["/d"]},
        "verbose": {"type": "boolean", "title": "V", "description": "d", "default": False},
        "port": {"type": "number", "title": "P", "description": "d"},
    })
    m["server"]["mcp_config"] = {
        "command": "node",
        "args": ["${__dirname}${/}index.js", "${user_config.dirs}", "--port=${user_config.port}"],
        "env": {"VERBOSE": "${user_config.verbose}", "HOME_DIR": "${HOME}"},
        "platform_overrides": {"win32": {"command": "node.exe"}},
    }
    config = run(mcpb.get_mcp_config_for_manifest(
        m, "/ext", {"HOME": "/home/u"}, {"port": 8080.0}, "/", platform="win32"
    ))
    assert config == {
        "command": "node.exe",
        "args": ["/ext/index.js", "/d", "--port=8080"],
        "env": {"VERBOSE": "false", "HOME_DIR": "/home/u"},
        "platform_overrides": {"win32": {"command": "node.exe"}},
    }
    linux = run(mcpb.get_mcp_config_for_manifest(m, "/ext", {}, {"port": 1}, "/", platform="linux"))
    assert linux is not None and linux["command"] == "node"


def test_get_mcp_config_none_when_required_missing_or_no_config() -> None:
    m = manifest(user_config={"k": {"type": "string", "title": "K", "description": "d", "required": True}})
    assert run(mcpb.get_mcp_config_for_manifest(m, "/e", {}, {}, "/")) is None
    assert run(mcpb.get_mcp_config_for_manifest(m, "/e", {}, {"k": ["a", ""]}, "/")) is None
    assert run(mcpb.get_mcp_config_for_manifest(m, "/e", {}, {"k": "v"}, "/")) is not None
    assert run(mcpb.get_mcp_config_for_manifest({"server": {}}, "/e", {}, None, "/")) is None


def test_node_platform_names() -> None:
    assert mcpb.node_platform("linux") == "linux"
    assert mcpb.node_platform("win32") == "win32"
    assert mcpb.node_platform("darwin") == "darwin"


# ── helpers.py ────────────────────────────────────────────────────────────────

def test_validate_manifest_flattens_issues() -> None:
    bad = manifest(author={"name": 1, "url": "x"}, extra=True)
    with pytest.raises(ValueError) as excinfo:
        run(helpers.validate_manifest(bad))
    assert str(excinfo.value) == (
        "Invalid manifest: author: Expected string, received number, Invalid url; "
        "Unrecognized key(s) in object: 'extra'"
    )


def test_parse_from_text_and_bytes() -> None:
    with pytest.raises(ValueError, match="^Invalid JSON in manifest.json: "):
        run(helpers.parse_and_validate_manifest_from_text("{"))
    data = "﻿".encode() + json.dumps(manifest()).encode()
    assert run(helpers.parse_and_validate_manifest_from_bytes(data))["name"] == "weather"


def test_generate_extension_id() -> None:
    m = manifest(name="  My   Weather!! Tool--", author={"name": "Ada  Lovelace_Jr."})
    assert helpers.generate_extension_id(m) == "ada-lovelace_jr..my-weather-tool"
    assert helpers.generate_extension_id(m, "local.dxt") == "local.dxt.ada-lovelace_jr..my-weather-tool"


# ── system_directories.py ─────────────────────────────────────────────────────

def test_system_directories_per_platform() -> None:
    env = {"XDG_DOWNLOAD_DIR": "/dl", "USERPROFILE": "/profile"}
    assert sysdirs.get_system_directories(env=env, homedir="/h", platform="linux") == {
        "HOME": "/h", "DESKTOP": "/h/Desktop", "DOCUMENTS": "/h/Documents", "DOWNLOADS": "/dl",
    }
    windows = sysdirs.get_system_directories(env=env, homedir="/h", platform="windows")
    assert windows["DESKTOP"].replace("\\", "/") == "/profile/Desktop"
    assert sysdirs.get_system_directories(env={}, homedir="/h", platform="macos")["DOWNLOADS"] == "/h/Downloads"
    assert sysdirs.get_system_directories(env={}, homedir="/h", platform="unknown")["HOME"] == "/h"


def test_detect_platform(tmp_path) -> None:
    proc = tmp_path / "version"
    proc.write_text("Linux version 6.1 (microsoft-standard-WSL2)")
    assert sysdirs._detect_platform("linux", str(proc)) == "wsl"
    proc.write_text("Linux version 6.1 generic")
    assert sysdirs._detect_platform("linux", str(proc)) == "linux"
    assert sysdirs._detect_platform("linux", str(tmp_path / "missing")) == "linux"
    assert sysdirs._detect_platform("darwin") == "macos"
    assert sysdirs._detect_platform("win32") == "windows"
    assert sysdirs._detect_platform("freebsd14") == "unknown"
