"""
Persisting oversized tool results, and the preview that replaces them (Phase 2).

Ported from:
- `reference/claude-code/src/utils/toolResultStorage.ts` -> `getPersistenceThreshold`,
  `persistToolResult`, `generatePreview`, `buildLargeToolResultMessage`,
  `maybePersistLargeToolResult`
- `reference/claude-code/src/constants/toolLimits.ts` -> `DEFAULT_MAX_RESULT_SIZE_CHARS`
- `reference/claude-code/src/utils/format.ts` -> `formatFileSize`

The shape is upstream's, including the parts that look like details and are not:

* **The preview is head-only.** 2000 bytes, cut at the last newline *only* if that newline
  is past the halfway mark, otherwise a hard cut. No tail.
* **`DEFAULT_MAX_RESULT_SIZE_CHARS` is a clamp, not a default.** A tool declaring more than
  50 000 still persists at 50 000; the declared number only bites when it is lower.
* **`math.inf` is a hard opt-out**, checked before anything else. `ReadFile` is the one
  tool that uses it, for upstream's reason: persisting a read creates a
  ReadFile -> file -> ReadFile loop, and the tool already bounds itself.
* **Persistence failure is fail-open.** If the file cannot be written the original result
  is returned untouched — a storage problem must not also destroy the output.
* **Writes are create-only.** Upstream opens with `'wx'` and swallows `EEXIST`, because a
  tool_use id is unique and its content deterministic, so a replay must not rewrite.

The directory layout mirrors upstream's — `<config>/projects/<sanitized cwd>/<run>/tool-results/`
— rather than living inside the workspace, so generated code never contains the agent's own
scratch. Upstream's `Read` is not workspace-bounded and can open that path directly; ours is,
so `app/loop/tools/fs.py` admits this one directory as an extra read root, which is what
keeps the path in the preview a path the model can actually use.
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.loop.context.tool_context import ContentReplacementState

logger = logging.getLogger(__name__)

#: `toolLimits.ts` -> `DEFAULT_MAX_RESULT_SIZE_CHARS`. A ceiling on every tool's own figure.
DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000

#: `toolLimits.ts`: `MAX_TOOL_RESULT_TOKENS * BYTES_PER_TOKEN`, the fallback threshold.
MAX_TOOL_RESULT_TOKENS = 100_000
BYTES_PER_TOKEN = 4
MAX_TOOL_RESULT_BYTES = MAX_TOOL_RESULT_TOKENS * BYTES_PER_TOKEN

#: `toolLimits.ts` -> `MAX_TOOL_RESULTS_PER_MESSAGE_CHARS`.
MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200_000

#: `toolResultStorage.ts` -> `PREVIEW_SIZE_BYTES`.
PREVIEW_SIZE_BYTES = 2000

#: `toolResultStorage.ts` -> the tags that wrap the replacement message.
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"

#: `toolResultStorage.ts` -> `TOOL_RESULTS_SUBDIR`.
TOOL_RESULTS_SUBDIR = "tool-results"

#: `sessionStoragePortable.ts` -> `MAX_SANITIZED_LENGTH`.
MAX_SANITIZED_LENGTH = 200

_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]")
_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


def config_home() -> Path:
    """`envUtils.ts` -> `getClaudeConfigHomeDir`, under this project's own name."""
    override = os.environ.get("TDDAGENTS_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".tddagents"


def sanitize_path(name: str) -> str:
    """`sessionStoragePortable.ts` -> `sanitizePath`, hash suffix included."""
    sanitized = _NON_ALNUM.sub("-", name)
    if len(sanitized) > MAX_SANITIZED_LENGTH:
        import hashlib

        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        sanitized = f"{sanitized[:MAX_SANITIZED_LENGTH]}-{digest}"
    return sanitized


def safe_file_stem(tool_use_id: str) -> str:
    """
    The filename for a tool_use id.

    Upstream writes `${id}.txt` with the id verbatim, because its ids come from the API and
    are already alphanumeric. Ours arrive from whichever provider answered, so anything that
    could leave the directory is replaced — for a well-formed id such as `toolu_01ABC` this
    is the identity, which is what keeps the layout upstream's.
    """
    cleaned = _UNSAFE_IN_FILENAME.sub("-", tool_use_id).lstrip(".")
    return cleaned[:MAX_SANITIZED_LENGTH] or "result"


def tool_results_dir(run_id: str, working_directory: str | Path) -> Path:
    """`<config>/projects/<sanitized cwd>/<run>/tool-results/`."""
    return (
        config_home()
        / "projects"
        / sanitize_path(str(working_directory))
        / sanitize_path(run_id)
        / TOOL_RESULTS_SUBDIR
    )


def get_persistence_threshold(declared_max_result_size_chars: float) -> float:
    """
    `getPersistenceThreshold`, minus the feature-flag override that has no analogue here.

    The infinity opt-out is checked first, as upstream checks it before the override so no
    remote configuration can switch persistence back on for a tool that must never have it.
    """
    if not math.isfinite(declared_max_result_size_chars):
        return declared_max_result_size_chars
    return min(declared_max_result_size_chars, DEFAULT_MAX_RESULT_SIZE_CHARS)


def format_file_size(size_bytes: int) -> str:
    """`format.ts` -> `formatFileSize`: `N bytes`, then KB/MB/GB at one decimal, `.0` stripped."""
    if size_bytes < 1024:
        return f"{size_bytes} bytes"
    for unit in ("KB", "MB", "GB"):
        size = size_bytes / 1024
        size_bytes = int(size_bytes / 1024)
        if size < 1024 or unit == "GB":
            rendered = f"{size:.1f}".removesuffix(".0")
            return f"{rendered}{unit}"
    return f"{size_bytes} bytes"  # pragma: no cover - the loop always returns


def generate_preview(content: str, max_bytes: int = PREVIEW_SIZE_BYTES) -> tuple[str, bool]:
    """
    `generatePreview` -> `(preview, has_more)`.

    The newline snap applies only past the halfway mark, so a long unbroken line is cut
    hard rather than collapsing the preview to almost nothing.
    """
    if len(content) <= max_bytes:
        return content, False
    head = content[:max_bytes]
    last_newline = head.rfind("\n")
    cut = last_newline if last_newline > max_bytes * 0.5 else max_bytes
    return content[:cut], True


def build_large_tool_result_message(
    original_size: int, filepath: Path | str, preview: str, has_more: bool
) -> str:
    """`buildLargeToolResultMessage`, wording and layout verbatim."""
    message = f"{PERSISTED_OUTPUT_TAG}\n"
    message += (
        f"Output too large ({format_file_size(original_size)}). "
        f"Full output saved to: {filepath}\n\n"
    )
    message += f"Preview (first {format_file_size(PREVIEW_SIZE_BYTES)}):\n"
    message += preview
    message += "\n...\n" if has_more else "\n"
    message += PERSISTED_OUTPUT_CLOSING_TAG
    return message


def persist_tool_result(
    content: str, tool_use_id: str, run_id: str, working_directory: str | Path
) -> Path | None:
    """
    Write the full result where the model can fetch it. `None` if it could not be written.

    Create-only, as upstream: an existing file for this tool_use id already holds exactly
    this content, so a replay reuses it instead of rewriting it.
    """
    directory = tool_results_dir(run_id, working_directory)
    filepath = directory / f"{safe_file_stem(tool_use_id)}.txt"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not create the tool-result store for %s: %s", tool_use_id, exc)
        return None

    try:
        # Create-only. An existing file for this id already holds exactly this content, so a
        # replay reuses it; `x` says that in one flag instead of a stat that could race.
        with open(filepath, "x", encoding="utf-8") as handle:
            handle.write(content)
    except FileExistsError:
        return filepath
    except OSError as exc:
        logger.warning("Could not persist tool result %s: %s", tool_use_id, exc)
        return None
    return filepath


def maybe_persist_large_result(
    content: str,
    *,
    tool_name: str,
    tool_use_id: str,
    run_id: str,
    working_directory: str | Path,
    max_result_size_chars: float = MAX_TOOL_RESULT_BYTES,
) -> str:
    """
    `maybePersistLargeToolResult`: the content to send the model, in upstream's order.

    Empty output is replaced first — upstream's inc-4586, where an empty tool result makes
    some models emit a stop sequence — then the threshold decides, then a write failure
    leaves the original content alone.
    """
    if not content.strip():
        return f"({tool_name} completed with no output)"

    threshold = get_persistence_threshold(max_result_size_chars)
    size = len(content)
    if size <= threshold:
        return content

    filepath = persist_tool_result(content, tool_use_id, run_id, working_directory)
    if filepath is None:
        return content

    preview, has_more = generate_preview(content)
    return build_large_tool_result_message(size, filepath, preview, has_more)


# ── Phase 5: the per-message aggregate budget ─────────────────────────────────
#
# Ported from `reference/claude-code/src/utils/toolResultStorage.ts` ->
# `getPerMessageBudgetLimit`, `provisionContentReplacementState`,
# `reconstructContentReplacementState`, `collectCandidatesByMessage`,
# `partitionByPriorDecision`, `selectFreshToReplace`, `replaceToolResultContents`,
# `buildReplacement`, `enforceToolResultBudget`, `applyToolResultBudget`.
#
# One "message" of tool results upstream is one API user message; here it is the run of
# `ToolMessage`s between two assistant responses. A result's fate is frozen once seen: a
# replaced one is re-applied byte-identically every turn, an unreplaced one is never
# replaced later (either change would rewrite a prefix the provider already cached).


@dataclass(frozen=True, slots=True)
class ToolResultReplacementRecord:
    """`ToolResultReplacementRecord` (`kind: 'tool-result'`)."""

    tool_use_id: str
    replacement: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    tool_use_id: str
    content: Any
    size: int


def get_per_message_budget_limit() -> int:
    """
    `getPerMessageBudgetLimit`: `MAX_TOOL_RESULTS_PER_MESSAGE_CHARS`. Upstream's override is
    the GrowthBook flag `tengu_hawthorn_window`, which this build does not have.
    """
    return MAX_TOOL_RESULTS_PER_MESSAGE_CHARS


def _is_content_already_compacted(content: Any) -> bool:
    return isinstance(content, str) and content.startswith(PERSISTED_OUTPUT_TAG)


def _has_image_block(content: Any) -> bool:
    return isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "image" for b in content)


def _content_size(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    return sum(len(str(b.get("text", ""))) for b in content if isinstance(b, dict) and b.get("type") == "text")


def _candidates_from(message: Any) -> list[_Candidate]:
    """`collectCandidatesFromMessage`: non-empty, non-image, not-yet-compacted results."""
    content = getattr(message, "content", None)
    if not content or _is_content_already_compacted(content) or _has_image_block(content):
        return []
    return [_Candidate(str(message.tool_call_id), content, _content_size(content))]


def _collect_candidates_by_message(messages: Sequence[Any]) -> list[list[_Candidate]]:
    """
    `collectCandidatesByMessage`: groups split only by a *new* assistant response (records
    split from one response share an id). *Divergence:* an assistant message with no id is
    always a new response — upstream's always carry one, ours may not, and treating a
    missing id as "same as the last" would merge whole turns into one group.
    """
    groups: list[list[_Candidate]] = []
    current: list[_Candidate] = []
    seen: set[str] = set()
    for message in messages:
        kind = getattr(message, "type", None)
        if kind == "tool":
            current.extend(_candidates_from(message))
        elif kind == "ai":
            message_id = getattr(message, "id", None)
            if not message_id or message_id not in seen:
                if current:
                    groups.append(current)
                current = []
                if message_id:
                    seen.add(message_id)
    if current:
        groups.append(current)
    return groups


def _tool_name_map(messages: Sequence[Any]) -> dict[str, str]:
    """`buildToolNameMap`: tool_use id -> tool name, from the assistants' tool calls."""
    names: dict[str, str] = {}
    for message in messages:
        if getattr(message, "type", None) != "ai":
            continue
        for call in getattr(message, "tool_calls", None) or ():
            names[str(call.get("id"))] = str(call.get("name"))
    return names


def _select_fresh_to_replace(fresh: list[_Candidate], frozen_size: int, limit: int) -> list[_Candidate]:
    """`selectFreshToReplace`: largest first until the visible total fits, or fresh runs out."""
    selected: list[_Candidate] = []
    remaining = frozen_size + sum(c.size for c in fresh)
    for candidate in sorted(fresh, key=lambda c: c.size, reverse=True):
        if remaining <= limit:
            break
        selected.append(candidate)
        remaining -= candidate.size
    return selected


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")


def _build_replacement(candidate: _Candidate, run_id: str, working_directory: str | Path) -> str | None:
    """`buildReplacement`: persist the full result; the preview message, or `None` on failure."""
    text = _content_text(candidate.content)
    filepath = persist_tool_result(text, candidate.tool_use_id, run_id, working_directory)
    if filepath is None:
        return None
    preview, has_more = generate_preview(text)
    return build_large_tool_result_message(len(text), filepath, preview, has_more)


def _replace_tool_result_contents(messages: Sequence[Any], replacements: dict[str, str]) -> list[Any]:
    """`replaceToolResultContents`: untouched messages are passed through by reference."""
    out: list[Any] = []
    for message in messages:
        replacement = (
            replacements.get(str(getattr(message, "tool_call_id", "")))
            if getattr(message, "type", None) == "tool"
            else None
        )
        out.append(message if replacement is None else message.model_copy(update={"content": replacement}))
    return out


def enforce_tool_result_budget(
    messages: Sequence[Any],
    state: ContentReplacementState,
    *,
    run_id: str,
    working_directory: str | Path,
    skip_tool_names: frozenset[str] = frozenset(),
) -> tuple[list[Any], list[ToolResultReplacementRecord]]:
    """`enforceToolResultBudget` -> `(messages, newly_replaced)`. Mutates `state`."""
    names = _tool_name_map(messages) if skip_tool_names else {}
    limit = get_per_message_budget_limit()
    replacements: dict[str, str] = {}
    to_persist: list[_Candidate] = []

    for candidates in _collect_candidates_by_message(messages):
        fresh: list[_Candidate] = []
        frozen: list[_Candidate] = []
        for candidate in candidates:
            prior = state.replacements.get(candidate.tool_use_id)
            if prior is not None:
                replacements[candidate.tool_use_id] = prior
            elif candidate.tool_use_id in state.seen_ids:
                frozen.append(candidate)
            else:
                fresh.append(candidate)
        if not fresh:
            state.seen_ids.update(c.tool_use_id for c in candidates)
            continue
        skipped = [c for c in fresh if names.get(c.tool_use_id, "") in skip_tool_names]
        state.seen_ids.update(c.tool_use_id for c in skipped)
        eligible = [c for c in fresh if names.get(c.tool_use_id, "") not in skip_tool_names]
        frozen_size = sum(c.size for c in frozen)
        fresh_size = sum(c.size for c in eligible)
        selected = _select_fresh_to_replace(eligible, frozen_size, limit) if frozen_size + fresh_size > limit else []
        selected_ids = {c.tool_use_id for c in selected}
        state.seen_ids.update(c.tool_use_id for c in candidates if c.tool_use_id not in selected_ids)
        to_persist.extend(selected)

    if not replacements and not to_persist:
        return list(messages), []

    newly: list[ToolResultReplacementRecord] = []
    for candidate in to_persist:
        state.seen_ids.add(candidate.tool_use_id)
        replacement = _build_replacement(candidate, run_id, working_directory)
        if replacement is None:
            continue
        replacements[candidate.tool_use_id] = replacement
        state.replacements[candidate.tool_use_id] = replacement
        newly.append(ToolResultReplacementRecord(tool_use_id=candidate.tool_use_id, replacement=replacement))

    if not replacements:
        return list(messages), []
    if newly:
        logger.debug("Per-message budget: persisted %d tool results", len(newly))
    return _replace_tool_result_contents(messages, replacements), newly


def apply_tool_result_budget(
    messages: Sequence[Any],
    state: ContentReplacementState | None,
    *,
    run_id: str,
    working_directory: str | Path,
    skip_tool_names: frozenset[str] = frozenset(),
) -> list[Any]:
    """
    `applyToolResultBudget`: no state means the feature is off (upstream's external build:
    see `provision_content_replacement_state`), and the messages pass through unchanged.
    """
    if state is None:
        return list(messages)
    out, _ = enforce_tool_result_budget(
        messages, state, run_id=run_id, working_directory=working_directory, skip_tool_names=skip_tool_names
    )
    return out


def reconstruct_content_replacement_state(
    messages: Sequence[Any], records: Sequence[ToolResultReplacementRecord]
) -> ContentReplacementState:
    """
    `reconstructContentReplacementState`: every candidate in `messages` is frozen as seen,
    and recorded replacements for those ids are restored.
    """
    from app.loop.context.tool_context import ContentReplacementState

    candidate_ids = {c.tool_use_id for group in _collect_candidates_by_message(messages) for c in group}
    state = ContentReplacementState(seen_ids=set(candidate_ids))
    for record in records:
        if record.tool_use_id in candidate_ids:
            state.replacements[record.tool_use_id] = record.replacement
    return state


def provision_content_replacement_state(
    initial_messages: Sequence[Any] | None = None,
    initial_replacements: Sequence[ToolResultReplacementRecord] | None = None,
    *,
    enabled: bool = False,
) -> ContentReplacementState | None:
    """
    `provisionContentReplacementState`. Upstream gates the feature on GrowthBook
    `tengu_hawthorn_steeple`, which defaults to `false`; with no GrowthBook here that default
    is the build's, so `enabled` is `False` unless a caller opts in. Disabled -> `None`
    (enforcement skipped); no history -> a fresh state; history -> reconstructed.
    """
    from app.loop.context.tool_context import ContentReplacementState

    if not enabled:
        return None
    if initial_messages is not None:
        return reconstruct_content_replacement_state(initial_messages, initial_replacements or [])
    return ContentReplacementState()


__all__ = [
    "BYTES_PER_TOKEN",
    "DEFAULT_MAX_RESULT_SIZE_CHARS",
    "MAX_TOOL_RESULTS_PER_MESSAGE_CHARS",
    "MAX_TOOL_RESULT_BYTES",
    "MAX_TOOL_RESULT_TOKENS",
    "PERSISTED_OUTPUT_CLOSING_TAG",
    "PERSISTED_OUTPUT_TAG",
    "PREVIEW_SIZE_BYTES",
    "TOOL_RESULTS_SUBDIR",
    "ToolResultReplacementRecord",
    "apply_tool_result_budget",
    "build_large_tool_result_message",
    "config_home",
    "enforce_tool_result_budget",
    "format_file_size",
    "generate_preview",
    "get_per_message_budget_limit",
    "get_persistence_threshold",
    "maybe_persist_large_result",
    "persist_tool_result",
    "provision_content_replacement_state",
    "reconstruct_content_replacement_state",
    "safe_file_stem",
    "sanitize_path",
    "tool_results_dir",
]
