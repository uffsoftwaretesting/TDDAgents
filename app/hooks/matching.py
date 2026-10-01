"""
Hook matching, deduplication, and conditional filtering (Part H1).

Ported from:
- `reference/claude-code/src/utils/hooks.ts` -> `getMatchingHooks`, `hookDedupKey`, `matchesPattern`
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

from app.tools.rules import rule_matches

if TYPE_CHECKING:
    from app.hooks.schemas import HookDefinition


def eval_if_condition(
    if_condition: str | None,
    tool_name: str | None,
    command: str | None = None,
    tool_input: dict[str, Any] | None = None,
) -> bool:
    """
    Evaluates permission rule syntax (e.g. 'Bash(git *)', 'Read(*.py)') against
    the tool name and arguments.

    If if_condition is None or empty, the hook matches unconditionally.
    """
    if not if_condition or not if_condition.strip():
        return True

    if not tool_name:
        return False

    effective_content = command
    if effective_content is None and tool_input is not None:
        cmd_val = tool_input.get("command")
        if isinstance(cmd_val, str):
            effective_content = cmd_val
        else:
            path_val = tool_input.get("file_path") or tool_input.get("path")
            if isinstance(path_val, str):
                effective_content = path_val

    return rule_matches(if_condition, tool_name, effective_content)


def deduplicate_hooks(
    matched_hooks: Sequence[HookDefinition],
) -> tuple[HookDefinition, ...]:
    """
    Deduplicate hooks by command/prompt/url within the same source context.

    Ported from `reference/claude-code/src/utils/hooks.ts:1712-1806`:
    - Key is namespaced by source (user, project, local, plugin).
    - For command hooks: (source, shell, command, if_condition).
    - For prompt hooks: (source, prompt, if_condition).
    - For agent hooks: (source, prompt, if_condition).
    - For http hooks: (source, url, if_condition).
    - Last entry wins on collision (preserving last-merged scope order).
    """
    if not matched_hooks:
        return ()

    deduped: dict[str, HookDefinition] = {}

    for hook in matched_hooks:
        hook_type = getattr(hook, "type", "command")
        source = getattr(hook, "source", "")
        if_cond = getattr(hook, "if_condition", None) or ""

        if hook_type == "command":
            shell = getattr(hook, "shell", "bash")
            cmd = getattr(hook, "command", "")
            key = f"{source}\0command\0{shell}\0{cmd}\0{if_cond}"
        elif hook_type == "prompt":
            prompt = getattr(hook, "prompt", "")
            key = f"{source}\0prompt\0{prompt}\0{if_cond}"
        elif hook_type == "agent":
            prompt = getattr(hook, "prompt", "")
            key = f"{source}\0agent\0{prompt}\0{if_cond}"
        elif hook_type == "http":
            url = getattr(hook, "url", "")
            key = f"{source}\0http\0{url}\0{if_cond}"
        else:
            # Fallback identity for generic or custom hook definition
            key = f"{source}\0{hook_type}\0{id(hook)}"

        deduped[key] = hook

    return tuple(deduped.values())
