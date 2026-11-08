"""
The Python bwrap argv against the package's own: `wrapCommandWithSandboxLinux` from
`reference/sandbox-runtime/dist/` run through `node` over the same real directory tree, compared word by
word (each nested `bash -c` script parsed too). Skipped without the reference package (with its
`node_modules`) or without `node`, so a checkout without `reference/` still passes.

What legitimately differs is normalized first: the product rename (`.claude/` -> `.tddagents/`) and
where each side keeps its vendored seccomp binaries.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.utils.ripgrep import builtin_ripgrep_path
from app.utils.sandbox.runtime import linux_sandbox_utils as lsu
from app.utils.sandbox.runtime.generate_seccomp_filter import (
    get_apply_seccomp_binary_path,
    get_pre_generated_bpf_path,
)
from app.utils.sandbox.runtime.sandbox_config import RipgrepConfig
from app.utils.sandbox.runtime.sandbox_schemas import FsReadRestrictionConfig, FsWriteRestrictionConfig

REFERENCE = (Path(__file__).resolve().parents[1] / "reference" / "sandbox-runtime").resolve()
LINUX_UTILS = REFERENCE / "dist" / "sandbox" / "linux-sandbox-utils.js"

NODE_SCRIPT = """
import { pathToFileURL } from 'node:url';
const [modulePath, paramsJson, cwd] = process.argv.slice(2);
process.chdir(cwd);
const { wrapCommandWithSandboxLinux } = await import(pathToFileURL(modulePath).href);
const params = JSON.parse(paramsJson);
process.stdout.write(await wrapCommandWithSandboxLinux(params));
"""

pytestmark = pytest.mark.skipif(
    not LINUX_UTILS.is_file() or not (REFERENCE / "node_modules").is_dir() or shutil.which("node") is None,
    reason="needs reference/sandbox-runtime (npm pack + npm install) and node")


def _expand(word: str) -> Any:
    """A `bash -c <script>` word becomes its parsed words, the script's lines parsed one by one."""
    words = shlex.split(word)
    if len(words) == 3 and words[1] == "-c" and "\n" in words[2]:
        return [words[0], "-c", [shlex.split(line) for line in words[2].split("\n")]]
    return words


def _normalize(wrapped: str, seccomp: dict[str, str]) -> list[Any]:
    for path, placeholder in seccomp.items():
        wrapped = wrapped.replace(path, placeholder)
    argv = shlex.split(wrapped.replace("/.claude/", "/.tddagents/"))
    return [*argv[:-1], _expand(argv[-1])]


@pytest.fixture
def tree(tmp_path: Path) -> dict[str, Path]:
    home = tmp_path / "home"
    project = home / "ws" / "project"
    for directory in (project / ".git" / "hooks", project / ".tddagents", project / ".claude",
                      project / "sub" / ".vscode", tmp_path / "tmpdir"):
        directory.mkdir(parents=True)
    (project / ".git" / "config").write_text("[core]\n")
    (project / ".tddagents" / "project.json").write_text("{}")
    (project / "sub" / ".vscode" / "settings.json").write_text("{}")
    # One nested match only: ripgrep lists in parallel, so the order of several varies run to run.
    for name in ("http.sock", "socks.sock"):
        (tmp_path / name).write_text("")
    return {"root": tmp_path, "home": home, "project": project, "tmpdir": tmp_path / "tmpdir"}


@pytest.mark.parametrize("network", [True, False])
def test_the_argv_matches_the_packages_own(tree: dict[str, Path], network: bool) -> None:
    home, project, tmpdir, root = tree["home"], tree["project"], tree["tmpdir"], tree["root"]
    rg = builtin_ripgrep_path()
    read = {"denyOnly": [str(home)], "allowWithinDeny": [str(project)]}
    write = {"allowOnly": ["/dev/null", str(tmpdir), str(project)],
             "denyWithinAllow": [str(project / ".tddagents" / "project.json")]}
    sockets = {"httpSocketPath": str(root / "http.sock"), "socksSocketPath": str(root / "socks.sock")}
    params = {"command": "pip install -r requirements.txt && echo 'done'", "needsNetworkRestriction": network,
              "readConfig": read, "writeConfig": write, "binShell": "/bin/bash",
              "ripgrepConfig": {"command": rg}, **(sockets if network else {})}
    env = {**os.environ, "HOME": str(home), "CLAUDE_TMPDIR": str(tmpdir)}
    script = root / "wrap.mjs"
    script.write_text(NODE_SCRIPT)
    node = subprocess.run(["node", str(script), str(LINUX_UTILS), json.dumps(params), str(project)],
                          capture_output=True, text=True, env=env, timeout=60, check=True)

    lsu._bwrap_mount_points.clear()
    ours = lsu.wrap_command_with_sandbox_linux(lsu.LinuxSandboxParams(
        command=str(params["command"]), needs_network_restriction=network, cwd=str(project), home=str(home),
        tmpdir=str(tmpdir), http_socket_path=sockets["httpSocketPath"] if network else None,
        socks_socket_path=sockets["socksSocketPath"] if network else None,
        read_config=FsReadRestrictionConfig(deny_only=read["denyOnly"],
                                            allow_within_deny=read["allowWithinDeny"]),
        write_config=FsWriteRestrictionConfig(allow_only=write["allowOnly"],
                                              deny_within_allow=write["denyWithinAllow"]),
        bin_shell="/bin/bash", ripgrep_config=RipgrepConfig(command=rg)))
    lsu._bwrap_mount_points.clear()

    vendor_ref = REFERENCE / "dist" / "vendor" / "seccomp"
    ours_apply, ours_bpf = get_apply_seccomp_binary_path(), get_pre_generated_bpf_path()
    assert ours_apply and ours_bpf
    arch = Path(ours_apply).parent.name
    seccomp_ref = {str(vendor_ref / arch / "apply-seccomp"): "<apply>",
                   str(vendor_ref / arch / "unix-block.bpf"): "<bpf>",
                   str(REFERENCE / "vendor" / "seccomp" / arch / "apply-seccomp"): "<apply>",
                   str(REFERENCE / "vendor" / "seccomp" / arch / "unix-block.bpf"): "<bpf>"}
    expected = _normalize(node.stdout, seccomp_ref)
    actual = _normalize(ours, {ours_apply: "<apply>", ours_bpf: "<bpf>"})
    assert actual == expected
    # The comparison covered the interesting parts: home hidden, project re-bound, the nested deny found.
    flat = shlex.split(ours)
    assert ["--tmpfs", str(home)] == flat[flat.index("--tmpfs"):flat.index("--tmpfs") + 2]
    assert str(project / "sub" / ".vscode") in flat
