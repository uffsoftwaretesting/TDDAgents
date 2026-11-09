"""Test doubles for the sandbox: an in-memory `SandboxFs`, a recording ripgrep, fake `bwrap` and `socat`."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.sandbox.runtime.sandbox_config import RipgrepConfig


@dataclass
class FakeFs:
    """
    A `SandboxFs` over declared paths: `dirs`, `files` and `links` (link -> target). Every ancestor of a
    declared path is a directory. `realpath` follows the links; a path that does not resolve raises.
    """

    dirs: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)
    links: dict[str, str] = field(default_factory=dict)
    created: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for path in [*self.dirs, *self.files, *self.links]:
            parent = posixpath.dirname(path)
            while parent not in ("/", ""):
                self.dirs.add(parent)
                parent = posixpath.dirname(parent)
        self.dirs.add("/")

    def _resolve(self, path: str, depth: int = 0) -> str:
        if depth > 20:
            raise OSError("loop")
        parts = [p for p in path.split("/") if p]
        current = ""
        for index, part in enumerate(parts):
            current = f"{current}/{part}"
            if current in self.links:
                target = self.links[current]
                if not target.startswith("/"):
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(current), target))
                rest = "/".join(parts[index + 1:])
                return self._resolve(posixpath.join(target, rest) if rest else target, depth + 1)
        return current or "/"

    def _kind(self, path: str) -> str | None:
        resolved = self._resolve(path)
        if resolved in self.dirs:
            return "dir"
        if resolved in self.files:
            return "file"
        return None

    def exists(self, path: str) -> bool:
        try:
            return self._kind(path) is not None
        except OSError:
            return False

    def is_dir(self, path: str) -> bool:
        kind = self._kind(path)
        if kind is None:
            raise FileNotFoundError(path)
        return kind == "dir"

    def is_file(self, path: str) -> bool:
        kind = self._kind(path)
        if kind is None:
            raise FileNotFoundError(path)
        return kind == "file"

    def is_symlink(self, path: str) -> bool:
        normalized = posixpath.normpath(path)
        if normalized in self.links:
            return True
        if self._kind(path) is None:
            raise FileNotFoundError(path)
        return False

    def realpath(self, path: str) -> str:
        if not self.exists(path):
            raise FileNotFoundError(path)
        return self._resolve(path)

    def listdir(self, path: str) -> list[str]:
        prefix = "/" if path == "/" else path.rstrip("/") + "/"
        names = {p[len(prefix):].split("/")[0] for p in [*self.dirs, *self.files, *self.links]
                 if p.startswith(prefix) and p != prefix}
        return sorted(n for n in names if n)

    def walk(self, path: str) -> list[str]:
        prefix = path.rstrip("/") + "/"
        return sorted(p for p in [*self.dirs, *self.files, *self.links] if p.startswith(prefix))

    def mkdtemp(self, prefix: str) -> str:
        made = f"/tmp/{prefix}{len(self.created)}"
        self.created.append(made)
        self.dirs.add(made)
        return made


@dataclass
class RecordingRipgrep:
    """A ripgrep runner returning `matches` and recording each call's (args, target, config)."""

    matches: list[str] = field(default_factory=list)
    error: Exception | None = None
    calls: list[tuple[list[str], str, RipgrepConfig]] = field(default_factory=list)

    def __call__(self, args: list[str], target: str, config: RipgrepConfig) -> list[str]:
        self.calls.append((args, target, config))
        if self.error is not None:
            raise self.error
        return list(self.matches)


#: A fake `bwrap`: records each argv as a JSON line in `$FAKE_BWRAP_LOG`; answers the probe
#: (`--ro-bind / / --unshare-net true`) with `$FAKE_BWRAP_PROBE_EXIT` (and a line on stderr when non-zero);
#: otherwise applies the `--setenv` pairs and runs what follows `--` on the host, so a wrapped command
#: really runs.
FAKE_BWRAP = '''#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_BWRAP_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if args == ["--ro-bind", "/", "/", "--unshare-net", "true"]:
    code = int(os.environ.get("FAKE_BWRAP_PROBE_EXIT", "0"))
    if code:
        sys.stderr.write("bwrap: No permissions to create new namespace\\n")
    sys.exit(code)
env = dict(os.environ)
i = 0
while i < len(args):
    a = args[i]
    if a == "--":
        os.execve(args[i + 1], args[i + 1:], env)
    if a == "--setenv":
        env[args[i + 1]] = args[i + 2]
        i += 3
    elif a in ("--ro-bind", "--bind", "--dev-bind"):
        i += 3
    elif a in ("--tmpfs", "--dev", "--proc"):
        i += 2
    else:
        i += 1
sys.exit(127)
'''

#: A fake `socat`: a bridge (`UNIX-LISTEN:<path>,...`) creates its socket path; any socat stays up until killed.
FAKE_SOCAT = """#!/bin/sh
case "$1" in UNIX-LISTEN:*) p="${1#UNIX-LISTEN:}"; p="${p%%,*}"; : > "$p";; esac
exec tail -f /dev/null
"""


def install_fake_sandbox_tools(bin_dir: Path, *, bwrap: bool = True, socat: bool = True) -> Path:
    """Write the fake `bwrap` / `socat` into `bin_dir`; returns the bwrap log's path (next to `bin_dir`)."""
    import sys

    bin_dir.mkdir(parents=True, exist_ok=True)
    if bwrap:
        script = bin_dir / "bwrap"
        script.write_text(FAKE_BWRAP.replace("{python}", sys.executable))
        script.chmod(0o755)
    if socat:
        script = bin_dir / "socat"
        script.write_text(FAKE_SOCAT)
        script.chmod(0o755)
    return bin_dir.parent / "bwrap.log"


def bwrap_calls(log: Path) -> list[list[str]]:
    """Every argv the fake bwrap recorded, the probe included."""
    import json

    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]


#: The flag settings (`--settings`) the suite opens sessions over a seeded project with: the shipped
#: `.tddagents/settings.json` enables a required sandbox, and the suite stays unsandboxed (upstream's default
#: is off). There is no environment switch, which would be a way for a real session to fail open.
SANDBOX_OFF = {"sandbox": {"enabled": False}}
_sandbox_off_path: list[str] = []


def sandbox_off_settings() -> str:
    """A `--settings` file holding `SANDBOX_OFF` (written once per test process)."""
    import json
    import tempfile

    if not _sandbox_off_path:
        directory = tempfile.mkdtemp(prefix="tddagents-test-flag-")
        path = Path(directory) / "sandbox-off.json"
        path.write_text(json.dumps(SANDBOX_OFF))
        _sandbox_off_path.append(str(path))
    return _sandbox_off_path[0]
