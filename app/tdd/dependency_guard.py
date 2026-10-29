"""
A dependency is installed, never faked (2026-10-28, user decision).

The CPF live run's tests failed on `ModuleNotFoundError: No module named 'fastapi'`. RunTests
called that "the code under test does not exist yet", so in GREEN the model wrote the "missing
code": a `fastapi.py` standing in for the package, and the tests passed against it.

Claude-code has no test-runner tool, and nothing in it reasons about packages. Its answer to a
path that must not be written silently is the path safety check (`checkPathSafetyForAutoEdit`,
`utils/permissions/filesystem.ts`), language-agnostic and decided by name. So the split is:

* A name alone cannot tell a dependency from code not written yet: in RED,
  `from calculator import add` fails exactly like `import fastapi`. The language-neutral signal is
  the session's specification. Its "Core Dependencies" section, approved by the user before
  development, declares the dependencies (`declared_dependencies`, user decision 2026-10-28).
* RunTests, the runner seam, reads its runner's output. Today that is pytest: a collection
  error importing a declared dependency the project does not define is a **missing
  dependency**. That is an environment error, the ledger is left untouched, and the name is
  recorded on `ToolPermissionContext.missing_dependencies`. Any other missing import keeps its
  RED meaning, with a neutral message (`collection_error_header`).
* The path safety check (`app/utils/permissions/filesystem.py`) asks before a file stands in for
  a recorded name, in any language, through every write tool and Bash redirect.
* RunTests refuses to count a run while such a file exists. This catches `cp`/`mv`, whose
  targets the redirect check does not extract (upstream does not extract them either).

A new runner brings its own `missing_dependency` reading. The gate never needs one.
"""

from __future__ import annotations

import re
from typing import Any

from app.tdd.suite_fingerprint import IGNORED_SEGMENTS, LISTING_DEPTH
from app.utils.permissions.filesystem import (
    dependency_directory_in,
    dependency_stood_in_for,
    normalize_case_for_comparison,
    normalize_dependency_name,
)

#: The engineer specification's dependency section (`app/prompts/session/engineer/system.md`).
_CORE_DEPENDENCIES = re.compile(r"^#{1,6}\s*(?:\d+\.\s*)?Core Dependencies\s*$", re.IGNORECASE | re.MULTILINE)
_NEXT_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
#: Where a line's names end and its description starts: `:`, ` - `, ` — `, `(`.
_DESCRIPTION = re.compile(r":|\s[-\u2013\u2014]\s|\(")
#: A package name: what package managers accept, before any version or extras.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: pytest's collection error for a module that cannot be found, with the module's dotted name.
_NO_MODULE_NAMED = re.compile(r"^E\s+ModuleNotFoundError: No module named '([^']+)'", re.MULTILINE)
_COLLECTION_BANNER = "ERROR collecting"


def declared_dependencies(specification: str) -> frozenset[str]:
    """
    The names the specification's "Core Dependencies" section lists: on each line, the names
    before its description (`- FastAPI (>=0.110): web framework`, `pytest, pytest-cov`), without
    versions or extras. Markdown emphasis and code marks are ignored. No such section, no names.
    """
    found = _CORE_DEPENDENCIES.search(specification)
    if found is None:
        return frozenset()
    body = specification[found.end():]
    end = _NEXT_HEADING.search(body)
    names: set[str] = set()
    for raw in (body[:end.start()] if end else body).splitlines():
        line = _LIST_MARKER.sub("", raw.replace("*", "").replace("`", "")).strip()
        if not line:
            continue
        head = _DESCRIPTION.split(line, maxsplit=1)[0]
        for piece in re.split(r",|/|\band\b", head):
            name = _NAME.match(piece.strip())
            if name is not None:
                names.add(name.group(0).rstrip("._-"))
    return frozenset(names)


def is_declared(name: str, declared: frozenset[str]) -> bool:
    """Whether `name` is one of `declared`, compared as `normalize_dependency_name` does."""
    wanted = normalize_dependency_name(name)
    return any(normalize_dependency_name(entry) == wanted for entry in declared)


def _project_entries(workspace: Any) -> list[tuple[str, bool]] | None:
    """The project's own files and directories: no caches, no `.tddagents`, no dependencies."""
    lister = getattr(workspace, "list_files", None)
    if not callable(lister):
        return None
    try:
        entries = lister(".", depth=LISTING_DEPTH)
    except Exception:  # noqa: BLE001 - an unlistable workspace gives no evidence either way
        return None
    out = []
    for entry in entries:
        path = str(entry.path).replace("\\", "/")
        segments = path.split("/")
        if any(segment in IGNORED_SEGMENTS for segment in segments) or dependency_directory_in(path):
            continue
        out.append((path, bool(entry.is_dir)))
    return out


def _project_defines(name: str, entries: list[tuple[str, bool]]) -> bool:
    """Whether a project file's stem, or one of its directories, is named `name`."""
    wanted = normalize_dependency_name(name)
    for path, _is_dir in entries:
        segments = path.split("/")
        names = [*segments[:-1], segments[-1].split(".", 1)[0]]
        if any(normalize_dependency_name(segment) == wanted for segment in names):
            return True
    return False


def missing_dependency(stdout: str, stderr: str, exit_code: int, workspace: Any,
                       declared: frozenset[str]) -> str | None:
    """
    The declared dependency a pytest run could not import, or `None`.

    It is a collection error (exit 2) importing a module whose top-level name the specification
    declares (`declared`) and the project does not define. With an unlistable workspace nothing
    is classified, and the run is judged as before.
    """
    if exit_code != 2 or not declared:
        return None
    text = f"{stdout}\n{stderr}"
    if _COLLECTION_BANNER not in text:
        return None
    entries = _project_entries(workspace)
    if entries is None:
        return None
    for match in _NO_MODULE_NAMED.finditer(text):
        top_level = match.group(1).split(".", 1)[0]
        if is_declared(top_level, declared) and not _project_defines(top_level, entries):
            return top_level
    return None


def unimportable_module(stdout: str, stderr: str) -> str | None:
    """The first module pytest reports it could not find, by its dotted name."""
    found = _NO_MODULE_NAMED.search(f"{stdout}\n{stderr}")
    return found.group(1) if found else None


def stand_ins(workspace: Any, missing_dependencies: frozenset[str]) -> tuple[tuple[str, str], ...]:
    """
    Each project file or directory standing in for a missing dependency, as `(path, name)`: the
    shallowest path named like it (`pydantic`, not every file below it).
    """
    if not missing_dependencies:
        return ()
    found: dict[str, str] = {}
    for path, _is_dir in _project_entries(workspace) or []:
        segments = path.split("/")
        for depth in range(1, len(segments) + 1):
            prefix = "/".join(segments[:depth])
            name = dependency_stood_in_for(prefix, missing_dependencies)
            if name is not None:
                found.setdefault(prefix, name)
                break
    return tuple(sorted(found.items()))


def missing_dependency_message(name: str) -> str:
    return (f"pytest could not import '{name}', which is not part of this project: it is not installed in the "
            "project's environment. The TDD ledger was not changed.")


def stand_in_message(found: tuple[tuple[str, str], ...]) -> str:
    listed = ", ".join(f"{path} stands in for '{name}'" for path, name in found)
    return (f"This run does not count: {listed}, a dependency the tests could not import. "
            "The TDD ledger was not changed.")


def record_missing(state: Any, name: str) -> Any:
    """`AppState` with `name` added to the permission context's missing dependencies."""
    from dataclasses import replace

    context = state.tool_permission_context
    if normalize_case_for_comparison(name) in {normalize_case_for_comparison(n) for n in context.missing_dependencies}:
        return state
    return replace(state, tool_permission_context=replace(
        context, missing_dependencies=context.missing_dependencies | {name}))


__all__ = [
    "declared_dependencies",
    "is_declared",
    "missing_dependency",
    "missing_dependency_message",
    "record_missing",
    "stand_in_message",
    "stand_ins",
    "unimportable_module",
]
