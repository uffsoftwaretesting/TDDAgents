"""`app/utils/secure_storage/`, `app/utils/lockfile.py`, `app/utils/browser.py`, `oauth_port.py`."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import time

import pytest

from app.services.mcp import oauth_port
from app.utils import lockfile
from app.utils.browser import open_browser, validate_url
from app.utils.secure_storage.index import clear_keychain_cache, get_secure_storage
from app.utils.secure_storage.plain_text_storage import (
    PLAINTEXT_WARNING,
    get_storage_path,
    plain_text_storage,
)


def test_the_store_is_the_credentials_file_in_the_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    assert get_storage_path() == (tmp_path, tmp_path / ".credentials.json")
    assert get_secure_storage() is plain_text_storage
    assert plain_text_storage.name == "plaintext"
    clear_keychain_cache()


def test_update_writes_json_with_mode_0600_and_read_returns_it(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(home))
    assert plain_text_storage.read() is None
    result = plain_text_storage.update({"mcpOAuth": {"k": {"accessToken": "a"}}})
    assert result.success and result.warning == PLAINTEXT_WARNING
    path = home / ".credentials.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"mcpOAuth": {"k": {"accessToken": "a"}}}
    assert plain_text_storage.read() == {"mcpOAuth": {"k": {"accessToken": "a"}}}
    assert asyncio.run(plain_text_storage.read_async()) == {"mcpOAuth": {"k": {"accessToken": "a"}}}


def test_unreadable_documents_read_as_none(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    (tmp_path / ".credentials.json").write_text("{not json")
    assert plain_text_storage.read() is None
    (tmp_path / ".credentials.json").write_text("[1, 2]")
    assert plain_text_storage.read() is None


def test_update_fails_without_a_parent_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "missing" / "deeper"))
    result = plain_text_storage.update({"a": 1})
    assert not result.success and result.warning is None


def test_delete_is_idempotent_and_reports_failures(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    assert plain_text_storage.delete() is True
    plain_text_storage.update({"a": 1})
    assert plain_text_storage.delete() is True
    assert not (tmp_path / ".credentials.json").exists()
    (tmp_path / ".credentials.json").mkdir()
    assert plain_text_storage.delete() is False


# ── lockfile ──


def test_a_held_lock_refuses_a_second_holder_until_released(tmp_path):
    target = tmp_path / "x.lock"

    async def scenario() -> None:
        release = await lockfile.lock(target, realpath=False)
        assert (tmp_path / "x.lock.lock").is_dir()
        with pytest.raises(lockfile.LockError) as caught:
            await lockfile.lock(target, realpath=False)
        assert caught.value.code == "ELOCKED"
        await release()
        assert not (tmp_path / "x.lock.lock").exists()
        with pytest.raises(lockfile.LockError) as again:
            await release()
        assert again.value.code == "ERELEASED"
        second = await lockfile.lock(target)
        await second()

    asyncio.run(scenario())


def test_a_stale_lock_is_taken_over(tmp_path):
    target = tmp_path / "s"
    stale_dir = lockfile.get_lock_path(target)
    stale_dir.mkdir()
    old = time.time() - 60
    os.utime(stale_dir, (old, old))

    async def scenario() -> None:
        release = await lockfile.lock(target, stale=10_000)
        assert stale_dir.stat().st_mtime > old
        await release()

    asyncio.run(scenario())


def test_a_fresh_lock_is_not_stale(tmp_path):
    target = tmp_path / "f"
    lockfile.get_lock_path(target).mkdir()

    async def scenario() -> None:
        with pytest.raises(lockfile.LockError):
            await lockfile.lock(target, stale=10_000)

    asyncio.run(scenario())


def test_the_held_lock_is_refreshed_and_a_lost_lock_is_reported(tmp_path):
    target = tmp_path / "r"
    compromised: list[Exception] = []

    async def scenario() -> None:
        release = await lockfile.lock(target, update=20, on_compromised=compromised.append)
        lock_dir = lockfile.get_lock_path(target)
        old = time.time() - 30
        os.utime(lock_dir, (old, old))
        await asyncio.sleep(0.1)
        assert lock_dir.stat().st_mtime > old
        lock_dir.rmdir()
        await asyncio.sleep(0.1)
        assert compromised
        with pytest.raises(FileNotFoundError):
            await release()

    asyncio.run(scenario())


# ── browser ──


@pytest.mark.parametrize("url, message", [
    ("not a url", "Invalid URL format: not a url"),
    ("file:///etc/passwd", "Invalid URL protocol: must use http:// or https://, got file:"),
    ("ftp://host/x", "Invalid URL protocol: must use http:// or https://, got ftp:"),
])
def test_validate_url_rejects(url, message):
    with pytest.raises(ValueError) as caught:
        validate_url(url)
    assert str(caught.value) == message


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell script")
def test_open_browser_runs_the_browser_variable_with_the_url(monkeypatch, tmp_path):
    out = tmp_path / "opened"
    script = tmp_path / "browser.sh"
    script.write_text(f'#!/bin/sh\nprintf "%s" "$1" > {out}\n')
    script.chmod(0o755)
    monkeypatch.setenv("BROWSER", str(script))
    assert asyncio.run(open_browser("https://example.com/a?b=c")) is True
    assert out.read_text() == "https://example.com/a?b=c"


def test_open_browser_answers_false_on_any_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("BROWSER", str(tmp_path / "no-such-browser"))
    assert asyncio.run(open_browser("https://example.com")) is False
    monkeypatch.setenv("BROWSER", "false")
    assert asyncio.run(open_browser("https://example.com")) is False
    assert asyncio.run(open_browser("javascript:alert(1)")) is False


# ── oauthPort ──


def test_the_redirect_uri_is_a_loopback_callback():
    assert oauth_port.build_redirect_uri(4567) == "http://localhost:4567/callback"
    assert oauth_port.build_redirect_uri() == "http://localhost:3118/callback"


@pytest.mark.parametrize("value, port", [("5555", 5555), (" 77abc", 77), ("0", None), ("-3", None), ("x", None),
                                         ("", None)])
def test_the_configured_callback_port(monkeypatch, value, port):
    monkeypatch.setenv("TDDAGENTS_MCP_OAUTH_CALLBACK_PORT", value)
    assert oauth_port.get_mcp_oauth_callback_port() == port


def test_find_available_port_prefers_the_configured_port(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_MCP_OAUTH_CALLBACK_PORT", "4242")
    assert asyncio.run(oauth_port.find_available_port()) == 4242


def test_find_available_port_picks_a_free_port_in_range(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_MCP_OAUTH_CALLBACK_PORT", raising=False)
    low, high = oauth_port.REDIRECT_PORT_RANGE
    port = asyncio.run(oauth_port.find_available_port())
    assert low <= port <= high


def test_find_available_port_falls_back_then_gives_up(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_MCP_OAUTH_CALLBACK_PORT", raising=False)
    tried: list[int] = []

    def busy_except_fallback(port: int) -> bool:
        tried.append(port)
        return port == oauth_port.REDIRECT_PORT_FALLBACK

    monkeypatch.setattr(oauth_port, "_port_is_free", busy_except_fallback)
    assert asyncio.run(oauth_port.find_available_port()) == oauth_port.REDIRECT_PORT_FALLBACK
    assert len(tried) == oauth_port.MAX_PORT_ATTEMPTS + 1
    monkeypatch.setattr(oauth_port, "_port_is_free", lambda port: False)
    with pytest.raises(OSError, match="No available ports for OAuth redirect"):
        asyncio.run(oauth_port.find_available_port())


def test_a_bound_port_is_not_free():
    import socket

    with socket.socket() as held:
        held.bind(("", 0))
        held.listen()
        assert oauth_port._port_is_free(held.getsockname()[1]) is False
