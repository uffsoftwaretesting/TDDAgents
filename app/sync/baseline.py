"""
The sync baseline: a `{path: sha256}` snapshot of what both sides agreed on at the
last successful sync, and the conflict rule built on top of it.

Without a baseline you cannot tell "changed on one side" from "changed on both" — only
that the two sides differ. Every propagation decision below is derived from comparing
each side against the baseline, never from comparing the sides against each other.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from app.workspace.base import Workspace

# Mirroring a repository into itself is never intended, so .git is excluded even when a
# generated .gitignore does not mention it.
ALWAYS_EXCLUDED = (".git",)


def content_hash(content: str) -> str:
    """The digest stored in the baseline."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Pattern:
    """One parsed gitignore line."""

    bare: str        # no leading "/", no trailing "/"
    dir_only: bool   # had a trailing "/", so it names a directory
    anchored: bool   # had a leading "/", or contains an interior "/"


class IgnoreRules:
    """
    gitignore-style matching over workspace-relative POSIX paths.

    A `.gitignore` written inside the generated workspace is preferred when present,
    which keeps sync consistent with what the generated project itself considers
    disposable. The fallback list applies when there is none.

    This is a pragmatic subset of the gitignore spec: comments, blank lines, directory
    suffixes, and a leading "/" anchor. Negation ("!") is deliberately unsupported —
    silently half-honoring it would be worse than not accepting it.
    """

    def __init__(self, patterns: list[str], source: str = "fallback") -> None:
        self.source = source
        self.patterns: list[_Pattern] = []
        for raw in patterns:
            self.add(raw)

    def add(self, raw: str) -> None:
        """
        Parses one gitignore line and records it.

        Parsing happens here and only here. An earlier version stripped the leading
        "/" at this point and re-derived each pattern's shape inside `matches`, which
        silently turned an anchored "/build/" into a match-at-any-depth "build/".
        """
        pattern = raw.strip()
        if not pattern or pattern.startswith("#") or pattern.startswith("!"):
            return

        anchored = pattern.startswith("/")
        body = pattern.lstrip("/")
        dir_only = body.endswith("/")
        bare = body.rstrip("/")

        if not bare:
            return

        # git treats any pattern containing a non-trailing slash as root-anchored.
        self.patterns.append(
            _Pattern(bare=bare, dir_only=dir_only, anchored=anchored or "/" in bare)
        )

    @classmethod
    def from_workspace(cls, workspace: Workspace, fallback: list[str]) -> "IgnoreRules":
        """Reads `.gitignore` from the workspace root, falling back when absent."""
        try:
            if workspace.exists(".gitignore"):
                text = workspace.read_file(".gitignore")
                return cls(text.splitlines(), source=".gitignore")
        except Exception:
            # An unreadable .gitignore must not stop a sync; the fallback is safe.
            pass
        return cls(list(fallback), source="fallback")

    def matches(self, path: str) -> bool:
        """Reports whether a workspace-relative path should be skipped."""
        segments = path.split("/")

        if any(segment in ALWAYS_EXCLUDED for segment in segments):
            return True

        for pattern in self.patterns:
            if pattern.anchored:
                # Anchored: match from the workspace root only.
                if path.startswith(pattern.bare + "/"):
                    return True
                if not pattern.dir_only and fnmatch.fnmatch(path, pattern.bare):
                    return True
                continue

            # Unanchored: match at any depth, as git does. A directory component
            # matching is enough to exclude everything beneath it.
            if any(fnmatch.fnmatch(segment, pattern.bare) for segment in segments[:-1]):
                return True
            if not pattern.dir_only and fnmatch.fnmatch(segments[-1], pattern.bare):
                return True

        return False


@dataclass
class Baseline:
    """What both sides agreed on at the last successful sync."""

    entries: dict[str, str] = field(default_factory=dict)
    taken_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({"taken_at": self.taken_at, "entries": self.entries}, indent=2)

    @classmethod
    def from_json(cls, payload: str) -> "Baseline":
        data = json.loads(payload)
        return cls(entries=dict(data.get("entries", {})), taken_at=data.get("taken_at", 0.0))

    @classmethod
    def load(cls, path: str | Path) -> "Baseline":
        """Returns an empty baseline when none has been written yet."""
        target = Path(path)
        if not target.exists():
            return cls(entries={}, taken_at=0.0)
        try:
            return cls.from_json(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt baseline degrades to "everything looks new", which the conflict
            # rule handles safely — it never degrades to silent data loss.
            return cls(entries={}, taken_at=0.0)

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")


class Action(Enum):
    """What to do with one path at a sync checkpoint."""

    NOOP = "noop"
    PROPAGATE = "propagate"          # the changed side wins outright
    CONFLICT = "conflict"            # both sides changed; the run's winner takes it
    DELETE = "delete"                # a deletion the baseline proves is safe to mirror
    SKIP_DELETE = "skip_delete"      # a deletion the other side contradicts


@dataclass(frozen=True)
class Decision:
    path: str
    action: Action
    reason: str


def snapshot(workspace: Workspace, ignore: IgnoreRules) -> dict[str, str]:
    """Hashes every non-ignored text file in a workspace."""
    digests: dict[str, str] = {}

    for entry in workspace.list_files(".", depth=64):
        if entry.is_dir or ignore.matches(entry.path):
            continue
        try:
            digests[entry.path] = content_hash(workspace.read_file(entry.path))
        except Exception:
            # A file that cannot be read as text is not tracked; it is still mirrored
            # verbatim by the engine, it just never participates in conflict detection.
            continue

    return digests


def classify(
    baseline: Baseline,
    sandbox_state: dict[str, str],
    local_state: dict[str, str],
    sandbox_wins: bool = True,
) -> list[Decision]:
    """
    Decides what happens to every path known to any of the three views.

    Args:
        sandbox_wins: True during a run, False outside one. This is the whole of the
                      "sandbox wins during a run, local wins outside one" rule.
    """
    decisions: list[Decision] = []
    paths = sorted(set(baseline.entries) | set(sandbox_state) | set(local_state))

    for path in paths:
        base = baseline.entries.get(path)
        sandbox = sandbox_state.get(path)
        local = local_state.get(path)

        if sandbox == local:
            decisions.append(Decision(path, Action.NOOP, "both sides agree"))
            continue

        sandbox_changed = sandbox != base
        local_changed = local != base

        # ── Deletions ────────────────────────────────────────────────────────
        # A deletion propagates only when the baseline proves the file was untouched
        # on the other side. Otherwise the "deletion" is really a one-sided create.
        if sandbox is None or local is None:
            deleted_side_had_it = base is not None
            surviving_changed = local_changed if sandbox is None else sandbox_changed

            if not deleted_side_had_it:
                decisions.append(
                    Decision(path, Action.PROPAGATE, "new file on one side")
                )
            elif not surviving_changed:
                decisions.append(
                    Decision(path, Action.DELETE, "deleted on one side, untouched on the other")
                )
            else:
                decisions.append(
                    Decision(path, Action.SKIP_DELETE, "deleted on one side, modified on the other")
                )
            continue

        # ── Modifications ────────────────────────────────────────────────────
        if sandbox_changed and local_changed:
            winner = "sandbox" if sandbox_wins else "local"
            decisions.append(Decision(path, Action.CONFLICT, f"both sides changed; {winner} wins"))
        else:
            decisions.append(Decision(path, Action.PROPAGATE, "changed on one side only"))

    return decisions
