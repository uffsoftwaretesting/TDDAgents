"""
Memory files: the instruction hierarchy the model reads as `claudeMd` user context.

Ported from `reference/claude-code/src/utils/claudemd.ts` (`getMemoryFiles`,
`processMemoryFile`, `processMdRules`, `parseMemoryFileContent`, `stripHtmlComments`,
`extractIncludePathsFromTokens`, `getClaudeMds`), `src/memdir/memdir.ts`
(`truncateEntrypointContent`), `src/utils/frontmatterParser.ts` (`FRONTMATTER_REGEX`,
`splitPathInFrontmatter`) and `src/utils/format.ts` (`formatFileSize`).

Load order, lowest priority first (the model weighs later files more):

1. Managed  `/etc/tddagents/TDDAGENTS.md`, `/etc/tddagents/.tddagents/rules/**/*.md`
2. User     `~/.tddagents/TDDAGENTS.md`, `~/.tddagents/rules/**/*.md`
3. Project  `TDDAGENTS.md`, `.tddagents/TDDAGENTS.md`, `.tddagents/rules/**/*.md`
4. Local    `TDDAGENTS.local.md`
5. AutoMem  `~/.tddagents/projects/<project>/memory/MEMORY.md` — **off unless
   `TDDAGENTS_ENABLE_AUTO_MEMORY` is truthy**, because Part I5 requires every run to be an
   independent sample and upstream's AutoMem persists across conversations.

Names are TDDAgents' (`TDDAGENTS.md`, `.tddagents/`) where upstream's are product-specific
(`CLAUDE.md`, `.claude/`); everything else follows upstream: `@include` directives (max depth
5, text extensions only, external includes only from User memory), conditional rules
(frontmatter `paths:`) excluded from eager loading, block-level HTML comment stripping, and
the `getClaudeMds` rendering.

Divergences forced by this architecture:

* Managed/User/AutoMem files are read from the host; Project/Local files are read through
  the `Workspace` protocol, so they work in the sandbox. The workspace root is the original
  cwd, and the protocol cannot see above it, so the upward walk is the root alone.
* Upstream lexes with `marked`; here a CommonMark block scanner finds fenced and indented
  code and type-2 HTML (comment) blocks, and inline code spans are skipped for `@include`.
* `claudeMdExcludes`, the `InstructionsLoaded` hook and team memory are not ported (no
  settings key, hook event or feature for them exists here). Workspace rules directories
  are listed without following symlinks, since the protocol does not expose them.
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, Sequence

import yaml

from app.workspace.base import WorkspaceError, normalize_path

MemoryType = Literal["Managed", "User", "Project", "Local", "AutoMem"]

MEMORY_INSTRUCTION_PROMPT = (
    "Codebase and user instructions are shown below. Be sure to adhere to these instructions. "
    "IMPORTANT: These instructions OVERRIDE any default behavior and you MUST follow them exactly as written."
)
#: Recommended max character count for a memory file (`getLargeMemoryFiles`).
MAX_MEMORY_CHARACTER_COUNT = 40000
MAX_INCLUDE_DEPTH = 5

MEMORY_FILE_NAME = "TDDAGENTS.md"
LOCAL_MEMORY_FILE_NAME = "TDDAGENTS.local.md"
CONFIG_DIR_NAME = ".tddagents"
MANAGED_DIR = "/etc/tddagents"

ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000

#: "Unbounded" for `Workspace.list_files`: the largest depth a protobuf int32/uint32
#: accepts, since the sandbox forwards it to envd. Not a policy ceiling.
UNBOUNDED_DEPTH = 2**31 - 1

TEXT_FILE_EXTENSIONS = frozenset({
    ".md", ".txt", ".text", ".json", ".yaml", ".yml", ".toml", ".xml", ".csv",
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".mts", ".cts",
    ".py", ".pyi", ".pyw", ".rb", ".erb", ".rake", ".go", ".rs",
    ".java", ".kt", ".kts", ".scala", ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx",
    ".cs", ".swift", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    ".env", ".ini", ".cfg", ".conf", ".config", ".properties", ".sql", ".graphql", ".gql",
    ".proto", ".vue", ".svelte", ".astro", ".ejs", ".hbs", ".pug", ".jade",
    ".php", ".pl", ".pm", ".lua", ".r", ".R", ".dart", ".ex", ".exs", ".erl", ".hrl",
    ".clj", ".cljs", ".cljc", ".edn", ".hs", ".lhs", ".elm", ".ml", ".mli",
    ".f", ".f90", ".f95", ".for", ".cmake", ".make", ".makefile", ".gradle", ".sbt",
    ".rst", ".adoc", ".asciidoc", ".org", ".tex", ".latex", ".lock", ".log", ".diff", ".patch",
})

FRONTMATTER_REGEX = re.compile(r"^---\s*\n([\s\S]*?)---\s*\n?")
_COMMENT_SPAN = re.compile(r"<!--[\s\S]*?-->")
_INCLUDE_RE = re.compile(r"(?:^|\s)@((?:[^\s\\]|\\ )+)")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HTML_COMMENT_START_RE = re.compile(r"^ {0,3}<!--")
_CODE_SPAN_RE = re.compile(r"(`+)[\s\S]*?\1")


@dataclass(frozen=True, slots=True)
class MemoryFileInfo:
    path: str
    type: MemoryType
    content: str
    parent: str | None = None
    globs: tuple[str, ...] | None = None
    content_differs_from_disk: bool = False
    raw_content: str | None = None


# ── sources: where a memory file lives ───────────────────────────────────────

class MemorySource(Protocol):
    def key(self, path: str) -> str: ...
    def read(self, path: str) -> str | None: ...
    def rule_files(self, rules_dir: str) -> list[str]: ...
    def resolve_include(self, include: str, base: str) -> tuple[MemorySource, str] | None: ...
    def is_internal(self) -> bool: ...


class HostSource:
    """Host filesystem (Managed, User, AutoMem). Symlinks are resolved for dedup."""

    def key(self, path: str) -> str:
        return "host:" + os.path.normpath(path)

    def real_key(self, path: str) -> str:
        return "host:" + os.path.realpath(path)

    def read(self, path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def rule_files(self, rules_dir: str) -> list[str]:
        """`processMdRules`: every `.md` below `rules_dir`, following symlinks once."""
        out: list[str] = []
        visited: set[str] = set()

        def walk(directory: str) -> None:
            real = os.path.realpath(directory)
            if real in visited:
                return
            visited.add(real)
            try:
                names = sorted(os.listdir(directory))
            except OSError:
                return
            for name in names:
                entry = os.path.join(directory, name)
                if os.path.isdir(entry):
                    walk(entry)
                elif os.path.isfile(entry) and name.endswith(".md"):
                    out.append(entry)

        walk(rules_dir)
        return out

    def resolve_include(self, include: str, base: str) -> tuple[MemorySource, str] | None:
        if include.startswith("~/"):
            return self, os.path.join(os.path.expanduser("~"), include[2:])
        if include.startswith("/"):
            return self, os.path.normpath(include)
        return self, os.path.normpath(os.path.join(os.path.dirname(base), include))

    def is_internal(self) -> bool:
        return False  # host files are outside the workspace (the original cwd)


class WorkspaceSource:
    """Project/Local files, through the `Workspace` protocol."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws

    def key(self, path: str) -> str:
        return "ws:" + normalize_path(path)

    def read(self, path: str) -> str | None:
        try:
            content: str = self.ws.read_file(path)
        except (WorkspaceError, OSError, UnicodeDecodeError):
            return None
        return content

    def rule_files(self, rules_dir: str) -> list[str]:
        try:
            entries = self.ws.list_files(rules_dir, depth=UNBOUNDED_DEPTH)
        except (WorkspaceError, OSError):
            return []
        return sorted(e.path for e in entries if not e.is_dir and e.path.endswith(".md"))

    def resolve_include(self, include: str, base: str) -> tuple[MemorySource, str] | None:
        if include.startswith("~/"):
            return HostSource().resolve_include(include, base)
        if include.startswith("/"):
            return HostSource(), os.path.normpath(include)
        try:
            return self, normalize_path(posixpath.join(posixpath.dirname(base), include))
        except WorkspaceError:
            return None  # escapes the workspace: not readable through the protocol

    def is_internal(self) -> bool:
        return True


# ── parsing ──────────────────────────────────────────────────────────────────

def expand_braces(pattern: str) -> list[str]:
    match = re.match(r"^([^{]*)\{([^}]+)\}(.*)$", pattern)
    if not match:
        return [pattern]
    prefix, alternatives, suffix = match.groups()
    out: list[str] = []
    for part in alternatives.split(","):
        out.extend(expand_braces(prefix + part.strip() + suffix))
    return out


def split_path_in_frontmatter(value: Any) -> list[str]:
    """`frontmatterParser.ts` -> `splitPathInFrontmatter`: comma split outside braces, then expand."""
    if isinstance(value, list):
        return [p for item in value for p in split_path_in_frontmatter(item)]
    if not isinstance(value, str):
        return []
    parts: list[str] = []
    current = ""
    depth = 0
    for char in value:
        if char == "{":
            depth += 1
            current += char
        elif char == "}":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            if current.strip():
                parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return [e for p in parts if p for e in expand_braces(p)]


def parse_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    """`parseFrontmatter`: YAML between leading `---` lines; content is the rest, untouched."""
    match = FRONTMATTER_REGEX.match(markdown)
    if not match:
        return {}, markdown
    try:
        parsed = yaml.safe_load(match.group(1) or "")
    except yaml.YAMLError:
        parsed = None
    return (parsed if isinstance(parsed, dict) else {}), markdown[match.end():]


def parse_frontmatter_paths(raw: str) -> tuple[str, tuple[str, ...] | None]:
    frontmatter, content = parse_frontmatter(raw)
    if not frontmatter.get("paths"):
        return content, None
    patterns = [p[:-3] if p.endswith("/**") else p for p in split_path_in_frontmatter(frontmatter["paths"])]
    patterns = [p for p in patterns if p]
    if not patterns or all(p == "**" for p in patterns):
        return content, None
    return content, tuple(patterns)


def lex_blocks(content: str) -> list[tuple[str, str]]:
    """
    Split markdown into ('code' | 'html' | 'text', raw) blocks, enough of CommonMark for
    what upstream uses `marked` for: fenced/indented code and type-2 (comment) HTML blocks.
    """
    lines = content.splitlines(keepends=True)
    blocks: list[tuple[str, str]] = []
    i = 0
    prev_blank = True
    while i < len(lines):
        line = lines[i]
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            closing = re.compile(r"^ {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$")
            j = i + 1
            while j < len(lines) and not closing.match(lines[j]):
                j += 1
            end = min(j + 1, len(lines))
            blocks.append(("code", "".join(lines[i:end])))
            i, prev_blank = end, False
            continue
        if prev_blank and (line.startswith("    ") or line.startswith("\t")) and line.strip():
            j = i
            while j < len(lines) and (lines[j].startswith("    ") or lines[j].startswith("\t") or not lines[j].strip()):
                j += 1
            blocks.append(("code", "".join(lines[i:j])))
            i, prev_blank = j, not lines[j - 1].strip()
            continue
        if _HTML_COMMENT_START_RE.match(line):
            j = i
            while j < len(lines) and "-->" not in lines[j]:
                j += 1
            end = min(j + 1, len(lines))
            blocks.append(("html", "".join(lines[i:end])))
            i, prev_blank = end, False
            continue
        if blocks and blocks[-1][0] == "text":
            blocks[-1] = ("text", blocks[-1][1] + line)
        else:
            blocks.append(("text", line))
        prev_blank = not line.strip()
        i += 1
    return blocks


def _strip_comment_blocks(blocks: list[tuple[str, str]]) -> tuple[str, bool]:
    result = ""
    stripped = False
    for kind, raw in blocks:
        trimmed = raw.lstrip()
        if kind == "html" and trimmed.startswith("<!--") and "-->" in trimmed:
            residue = _COMMENT_SPAN.sub("", raw)
            stripped = True
            if residue.strip():
                result += residue
            continue
        result += raw
    return result, stripped


def strip_html_comments(content: str) -> tuple[str, bool]:
    """
    `stripHtmlComments`: remove block-level `<!-- ... -->` comments; comments inside code
    and inline in a paragraph are kept, and an unclosed comment is left in place.
    """
    if "<!--" not in content:
        return content, False
    return _strip_comment_blocks(lex_blocks(content))


def _is_valid_include(path: str) -> bool:
    return bool(
        path.startswith("./")
        or path.startswith("~/")
        or (path.startswith("/") and path != "/")
        or (not path.startswith("@") and not re.match(r"^[#%^&*()]+", path) and re.match(r"^[a-zA-Z0-9._-]", path))
    )


def extract_include_paths(blocks: list[tuple[str, str]]) -> list[str]:
    """`extractIncludePathsFromTokens`: raw `@path` references from text (never code)."""
    found: list[str] = []

    def scan(text: str) -> None:
        for match in _INCLUDE_RE.finditer(text):
            path = match.group(1).split("#", 1)[0]
            path = path.replace("\\ ", " ")
            if path and _is_valid_include(path) and path not in found:
                found.append(path)

    for kind, raw in blocks:
        if kind == "code":
            continue
        if kind == "html":
            trimmed = raw.lstrip()
            if trimmed.startswith("<!--") and "-->" in trimmed:
                residue = _COMMENT_SPAN.sub("", raw)
                if residue.strip():
                    scan(residue)
            continue
        scan(_CODE_SPAN_RE.sub(" ", raw))
    return found


def format_file_size(size: int) -> str:
    """`format.ts` -> `formatFileSize`."""
    def fmt(value: float, unit: str) -> str:
        text = f"{value:.1f}"
        return (text[:-2] if text.endswith(".0") else text) + unit

    kb = size / 1024
    if kb < 1:
        return f"{size} bytes"
    if kb < 1024:
        return fmt(kb, "KB")
    mb = kb / 1024
    if mb < 1024:
        return fmt(mb, "MB")
    return fmt(mb / 1024, "GB")


def truncate_entrypoint_content(raw: str) -> str:
    """`memdir.ts` -> `truncateEntrypointContent`: line cap, then byte cap, then a warning."""
    trimmed = raw.strip()
    lines = trimmed.split("\n")
    line_count, byte_count = len(lines), len(trimmed)
    line_cut, byte_cut = line_count > MAX_ENTRYPOINT_LINES, byte_count > MAX_ENTRYPOINT_BYTES
    if not line_cut and not byte_cut:
        return trimmed
    truncated = "\n".join(lines[:MAX_ENTRYPOINT_LINES]) if line_cut else trimmed
    if len(truncated) > MAX_ENTRYPOINT_BYTES:
        cut = truncated.rfind("\n", 0, MAX_ENTRYPOINT_BYTES + 1)
        truncated = truncated[:cut if cut > 0 else MAX_ENTRYPOINT_BYTES]
    if byte_cut and not line_cut:
        reason = (f"{format_file_size(byte_count)} (limit: {format_file_size(MAX_ENTRYPOINT_BYTES)}) "
                  "— index entries are too long")
    elif line_cut and not byte_cut:
        reason = f"{line_count} lines (limit: {MAX_ENTRYPOINT_LINES})"
    else:
        reason = f"{line_count} lines and {format_file_size(byte_count)}"
    return (truncated + f"\n\n> WARNING: {ENTRYPOINT_NAME} is {reason}. Only part of it was loaded. "
            "Keep index entries to one line under ~200 chars; move detail into topic files.")


def parse_memory_file_content(raw: str, path: str, type: MemoryType) -> tuple[MemoryFileInfo | None, list[str]]:
    """`parseMemoryFileContent`: frontmatter, comment strip, @includes, MEMORY.md truncation."""
    ext = os.path.splitext(path)[1]
    if ext and ext.lower() not in TEXT_FILE_EXTENSIONS:
        return None, []
    content, globs = parse_frontmatter_paths(raw)
    blocks = lex_blocks(content)
    stripped = _strip_comment_blocks(blocks)[0] if "<!--" in content else content
    includes = extract_include_paths(blocks)
    final = truncate_entrypoint_content(stripped) if type == "AutoMem" else stripped
    differs = final != raw
    info = MemoryFileInfo(
        path=path, type=type, content=final, globs=globs,
        content_differs_from_disk=differs, raw_content=raw if differs else None,
    )
    return info, includes


# ── loading ──────────────────────────────────────────────────────────────────

def process_memory_file(
    source: MemorySource,
    path: str,
    type: MemoryType,
    processed: set[str],
    include_external: bool,
    depth: int = 0,
    parent: str | None = None,
) -> list[MemoryFileInfo]:
    """`processMemoryFile`: the file, then its @includes (recursively), parent first."""
    key = source.key(path)
    if key in processed or depth >= MAX_INCLUDE_DEPTH:
        return []
    processed.add(key)
    if isinstance(source, HostSource):
        processed.add(source.real_key(path))

    raw = source.read(path)
    if raw is None:
        return []
    info, includes = parse_memory_file_content(raw, path, type)
    if info is None or not info.content.strip():
        return []
    if parent is not None:
        info = replace(info, parent=parent)

    result = [info]
    for include in includes:
        resolved = source.resolve_include(include, path)
        if resolved is None:
            continue
        inc_source, inc_path = resolved
        if not inc_source.is_internal() and not include_external:
            continue
        result.extend(process_memory_file(inc_source, inc_path, type, processed, include_external, depth + 1, path))
    return result


def process_md_rules(
    source: MemorySource, rules_dir: str, type: MemoryType, processed: set[str], include_external: bool,
    conditional_rule: bool = False,
) -> list[MemoryFileInfo]:
    """`processMdRules`: unconditional rules eagerly; conditional (`paths:`) ones are skipped."""
    out: list[MemoryFileInfo] = []
    for path in source.rule_files(rules_dir):
        files = process_memory_file(source, path, type, processed, include_external)
        out.extend(f for f in files if (f.globs is not None) == conditional_rule)
    return out


def is_env_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in ("1", "true", "yes", "on")


def is_auto_memory_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Off unless explicitly enabled (Part I5: runs must stay independent samples)."""
    return is_env_truthy((env if env is not None else os.environ).get("TDDAGENTS_ENABLE_AUTO_MEMORY"))


def sanitize_path(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def get_auto_mem_entrypoint(project: str, home: str | None = None) -> str:
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, CONFIG_DIR_NAME, "projects", sanitize_path(project), "memory", ENTRYPOINT_NAME)


def get_memory_files(
    ws: Any,
    *,
    home: str | None = None,
    managed_dir: str = MANAGED_DIR,
    env: Mapping[str, str] | None = None,
) -> list[MemoryFileInfo]:
    """`getMemoryFiles`: Managed, User, Project, Local, then AutoMem when enabled."""
    host = HostSource()
    user_dir = os.path.join(home if home is not None else os.path.expanduser("~"), CONFIG_DIR_NAME)
    processed: set[str] = set()
    result: list[MemoryFileInfo] = []

    result += process_memory_file(host, os.path.join(managed_dir, MEMORY_FILE_NAME), "Managed", processed, False)
    result += process_md_rules(host, os.path.join(managed_dir, CONFIG_DIR_NAME, "rules"), "Managed", processed, False)

    result += process_memory_file(host, os.path.join(user_dir, MEMORY_FILE_NAME), "User", processed, True)
    result += process_md_rules(host, os.path.join(user_dir, "rules"), "User", processed, True)

    if ws is not None:
        workspace = WorkspaceSource(ws)
        result += process_memory_file(workspace, MEMORY_FILE_NAME, "Project", processed, False)
        result += process_memory_file(workspace, f"{CONFIG_DIR_NAME}/{MEMORY_FILE_NAME}", "Project", processed, False)
        result += process_md_rules(workspace, f"{CONFIG_DIR_NAME}/rules", "Project", processed, False)
        result += process_memory_file(workspace, LOCAL_MEMORY_FILE_NAME, "Local", processed, False)

    if is_auto_memory_enabled(env):
        project = str(getattr(ws, "root", "sandbox"))
        entry = get_auto_mem_entrypoint(project, home)
        key = host.key(entry)
        raw = host.read(entry)
        if raw is not None and key not in processed:
            info, _ = parse_memory_file_content(raw, entry, "AutoMem")
            if info is not None:
                processed.add(key)
                result.append(info)
    return result


_DESCRIPTIONS: dict[str, str] = {
    "Project": " (project instructions, checked into the codebase)",
    "Local": " (user's private project instructions, not checked in)",
    "AutoMem": " (user's auto-memory, persists across conversations)",
}
_DEFAULT_DESCRIPTION = " (user's private global instructions for all projects)"


def get_claude_mds(files: list[MemoryFileInfo]) -> str:
    """`getClaudeMds`: the rendered `claudeMd` user-context value."""
    memories = [
        f"Contents of {f.path}{_DESCRIPTIONS.get(f.type, _DEFAULT_DESCRIPTION)}:\n\n{f.content.strip()}"
        for f in files
        if f.content
    ]
    if not memories:
        return ""
    return f"{MEMORY_INSTRUCTION_PROMPT}\n\n" + "\n\n".join(memories)


def extract_session_memory(
    messages: Sequence[Any],
    summary: str | None = None,
) -> list[str]:
    """
    Extract user preferences, conventions, and architectural decisions from a conversation (Plan C3).
    """
    memories: list[str] = []
    seen: set[str] = set()

    keywords = (
        "always",
        "never",
        "prefer",
        "use",
        "make sure",
        "ensure",
        "convention",
        "architecture",
        "pattern",
        "pytest",
        "async",
    )

    for msg in messages:
        # Check human messages for instructions / preferences
        msg_type = getattr(msg, "type", "")
        is_user = msg_type in ("human", "user") or msg.__class__.__name__ == "HumanMessage"
        if not is_user:
            continue

        content = str(getattr(msg, "content", ""))
        for sentence in re.split(r"(?<=[.!?\n])\s+", content):
            s = sentence.strip()
            if not s or len(s) < 10 or len(s) > 300:
                continue
            s_lower = s.lower()
            if any(kw in s_lower for kw in keywords):
                if s not in seen:
                    seen.add(s)
                    memories.append(s)

    if summary:
        summary_clean = summary.strip()
        if summary_clean and summary_clean not in seen:
            memories.append(f"Summary: {summary_clean[:200]}")

    return memories


def update_auto_memory(
    project: str,
    new_memories: Sequence[str],
    home: str | None = None,
) -> str:
    """
    Append new memories to the project's auto-memory entrypoint (MEMORY.md) within size limits (Plan C3).
    """
    entrypoint_path = Path(get_auto_mem_entrypoint(project, home))
    entrypoint_path.parent.mkdir(parents=True, exist_ok=True)

    header = "# Project Memory\n\nAuto-extracted preferences and architectural conventions.\n\n"
    existing_content = ""
    if entrypoint_path.is_file():
        existing_content = entrypoint_path.read_text(encoding="utf-8")
    else:
        existing_content = header

    # Append new bullet points
    new_bullets: list[str] = []
    for mem in new_memories:
        cleaned = mem.strip().lstrip("-* ").strip()
        if cleaned and cleaned not in existing_content:
            new_bullets.append(f"- {cleaned}")

    if new_bullets:
        updated = existing_content.rstrip() + "\n" + "\n".join(new_bullets) + "\n"
    else:
        updated = existing_content

    # Truncate to line & byte caps
    truncated = truncate_entrypoint_content(updated)
    entrypoint_path.write_text(truncated, encoding="utf-8")
    return truncated


def get_large_memory_files(files: list[MemoryFileInfo]) -> list[MemoryFileInfo]:
    return [f for f in files if len(f.content) > MAX_MEMORY_CHARACTER_COUNT]


__all__ = [
    "ENTRYPOINT_NAME",
    "MAX_ENTRYPOINT_BYTES",
    "MAX_ENTRYPOINT_LINES",
    "MEMORY_INSTRUCTION_PROMPT",
    "MemoryFileInfo",
    "extract_session_memory",
    "get_auto_mem_entrypoint",
    "get_claude_mds",
    "get_large_memory_files",
    "get_memory_files",
    "is_auto_memory_enabled",
    "strip_html_comments",
    "update_auto_memory",
]
