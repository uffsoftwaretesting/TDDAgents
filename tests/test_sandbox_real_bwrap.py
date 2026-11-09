"""
The real sandbox (`@pytest.mark.sandbox`): the shipped settings' shape over a project under `~`, run by the
host's bubblewrap and socat. Skipped unless both work (`bwrap --ro-bind / / --unshare-net true` succeeds
and `socat` is on `PATH`), so the offline suite passes on any host.

What it pins: a write outside the project (and outside `$TMPDIR`) fails; `~/.cache` is invisible; the
project, though under `~`, is writable; `$TMPDIR` is writable; and the network goes through the proxy: an
allowed host answers, an unknown one is refused with the allowlist's 403.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from app.utils.sandbox.sandbox_adapter import SandboxAdapter
from app.utils.settings.settings import SettingsLocations
from app.workspace.local import LocalWorkspace


def _sandbox_works() -> bool:
    if shutil.which("bwrap") is None or shutil.which("socat") is None:
        return False
    probe = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--unshare-net", "true"], capture_output=True,
                           timeout=10, check=False)
    return probe.returncode == 0


pytestmark = [pytest.mark.sandbox,
              pytest.mark.skipif(not _sandbox_works(), reason="needs a working bubblewrap and socat")]


class _Hello(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"hello from the host")

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def sandboxed_project() -> Iterator[dict[str, Any]]:
    home = Path.home()
    root = Path(os.path.realpath(home)) / ".tddagents-sandbox-test"
    shutil.rmtree(root, ignore_errors=True)
    project = root / "project"
    (project / ".tddagents").mkdir(parents=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Hello)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (project / ".tddagents" / "settings.json").write_text(json.dumps({"sandbox": {
        "enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
        "filesystem": {"denyRead": ["~"], "allowRead": ["."], "denyWrite": [".tddagents/project.json"]},
        "network": {"allowedDomains": ["127.0.0.1"]}}}))
    (project / ".tddagents" / "project.json").write_text("{}")
    adapter = SandboxAdapter(SettingsLocations(project_root=project, home=root / "user-home"))
    adapter.initialize()
    workspace = LocalWorkspace(project)
    workspace.sandbox_manager = adapter
    try:
        yield {"ws": workspace, "project": project, "port": server.server_address[1], "home": home}
    finally:
        adapter.reset()
        server.shutdown()
        shutil.rmtree(root, ignore_errors=True)


def run(ws: LocalWorkspace, command: str) -> Any:
    return ws.execute(command, sandbox=True, timeout=60)


def test_the_project_under_home_is_writable_and_its_config_is_not(sandboxed_project: dict[str, Any]) -> None:
    ws = sandboxed_project["ws"]
    result = run(ws, "echo made > made.txt && cat made.txt")
    assert result.exit_code == 0, result.stderr
    assert (sandboxed_project["project"] / "made.txt").read_text() == "made\n"
    refused = run(ws, "echo x > .tddagents/project.json")
    config = sandboxed_project["project"] / ".tddagents" / "project.json"
    assert refused.exit_code != 0 and config.read_text() == "{}"


def test_a_write_outside_the_project_fails_and_tmpdir_is_writable(sandboxed_project: dict[str, Any]) -> None:
    ws = sandboxed_project["ws"]
    outside = run(ws, "touch /tmp/tddagents-outside-$$")
    assert outside.exit_code != 0
    inside = run(ws, 'touch "$TMPDIR/ok-$$" && echo ok')
    assert inside.exit_code == 0 and inside.stdout.strip() == "ok", inside.stderr


def test_the_home_is_private(sandboxed_project: dict[str, Any]) -> None:
    home = sandboxed_project["home"]
    (home / ".cache").mkdir(exist_ok=True)
    result = run(sandboxed_project["ws"],
                 'ls -A "$HOME/.cache" 2>/dev/null | wc -l; test -e "$HOME/.bashrc" || echo none')
    assert result.exit_code == 0
    assert result.stdout.split() == ["0", "none"]


def test_the_network_goes_through_the_proxy(sandboxed_project: dict[str, Any]) -> None:
    ws, port = sandboxed_project["ws"], sandboxed_project["port"]
    allowed = run(ws, f'curl -s -x "$HTTP_PROXY" http://127.0.0.1:{port}/')
    assert allowed.stdout == "hello from the host", allowed.stderr
    refused = run(ws, 'curl -s -o /dev/null -w "%{http_code}" -x "$HTTP_PROXY" http://unknown.invalid/')
    assert refused.stdout == "403"
    direct = run(ws, f"curl -s --noproxy '*' --max-time 3 http://127.0.0.1:{port}/ || echo unreachable")
    assert direct.stdout.strip() == "unreachable"
