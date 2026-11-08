"""Test doubles for the sandbox runtime: an in-memory `SandboxFs` and a recording ripgrep."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

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
