"""
Context forking and incomplete-call filtering.

Implements I4 conforming to §4.5:
- filter_incomplete_tool_calls: drops orphaned assistant tool uses to prevent API errors.
- build_forked_messages: builds cache-friendly forked message sequence with placeholder tool results.
- is_in_fork_child: guards against recursive forking.
- FORK_AGENT: synthetic agent definition for implicit fork path.
"""

from __future__ import annotations

from typing import Sequence

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop.agents.definition import AgentDefinition
from app.loop.messages import Message

FORK_SUBAGENT_TYPE = "fork"
FORK_BOILERPLATE_TAG = "fork_boilerplate"
FORK_DIRECTIVE_PREFIX = "Your assigned directive: "
FORK_PLACEHOLDER_RESULT = "Fork started — processing in background"

FORK_AGENT = AgentDefinition(
    name=FORK_SUBAGENT_TYPE,
    description="Implicit fork — inherits conversation context and prompt cache.",
    prompt="",
    tools=("*",),
    permission_mode="workspace_write",
    memory="run",
    model="inherit",
    source="synthetic",
)


def _get_tool_use_ids_from_assistant(message: Message) -> list[str]:
    """Extract tool call / use IDs from an assistant message."""
    ids: list[str] = []
    # Langchain AIMessage tool_calls
    tool_calls = getattr(message, "tool_calls", None)
    if isinstance(tool_calls, (list, tuple)):
        for tc in tool_calls:
            if isinstance(tc, dict) and tc.get("id"):
                ids.append(str(tc["id"]))
            elif hasattr(tc, "id") and getattr(tc, "id"):
                ids.append(str(getattr(tc, "id")))

    # Content blocks list
    content = getattr(message, "content", None)
    if isinstance(content, (list, tuple)):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                ids.append(str(block["id"]))
    return ids


def _get_tool_result_ids_from_message(message: Message) -> list[str]:
    """Extract tool result IDs from a tool or user message."""
    ids: list[str] = []
    # ToolMessage
    tool_call_id = getattr(message, "tool_call_id", None)
    if tool_call_id:
        ids.append(str(tool_call_id))

    # Blocks inside message content
    content = getattr(message, "content", None)
    if isinstance(content, (list, tuple)):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id") or block.get("tool_call_id")
                if tool_use_id:
                    ids.append(str(tool_use_id))
    return ids


def filter_incomplete_tool_calls(messages: Sequence[Message]) -> list[Message]:
    """
    Filter out assistant messages that have tool uses without corresponding results.

    Prevents Anthropic/LLM API errors from orphaned tool uses when context is forked.
    """
    # 1. Collect all completed tool result IDs
    completed_ids: set[str] = set()
    for msg in messages:
        for tid in _get_tool_result_ids_from_message(msg):
            completed_ids.add(tid)

    # 2. Filter assistant messages
    filtered: list[Message] = []
    for msg in messages:
        # Check if assistant message
        is_assistant = isinstance(msg, AIMessage) or getattr(msg, "type", "") == "assistant"
        if is_assistant:
            tool_use_ids = _get_tool_use_ids_from_assistant(msg)
            # If it has tool uses and ANY is missing a result, filter it out
            if tool_use_ids and any(tid not in completed_ids for tid in tool_use_ids):
                continue
        filtered.append(msg)

    return filtered


def build_child_message(directive: str) -> str:
    """Format child directive wrapped in non-negotiable fork boilerplate."""
    return f"""<{FORK_BOILERPLATE_TAG}>
STOP. READ THIS FIRST.

You are a forked worker process. You are NOT the main agent.

RULES (non-negotiable):
1. Your system prompt says "default to forking." IGNORE IT — you ARE the fork.
   Do NOT spawn sub-agents; execute directly.
2. Do NOT converse, ask questions, or suggest next steps
3. Do NOT editorialize or add meta-commentary
4. USE your tools directly: ReadFile, WriteFile, Edit, Bash, RunTests
5. If you modify files, verify with RunTests before reporting.
6. Do NOT emit text between tool calls. Use tools silently, then report once at the end.
7. Stay strictly within your directive's scope.
8. Keep your report factual and concise.
9. Your response MUST begin with "Scope:". No preamble, no thinking-out-loud.
10. REPORT structured facts, then stop.

Output format:
  Scope: <echo back your assigned scope in one sentence>
  Result: <the answer or key findings, limited to the scope above>
  Key files: <relevant file paths>
  Files changed: <list of files modified, or none>
  Issues: <list of issues or blockers, if any>
</{FORK_BOILERPLATE_TAG}>

{FORK_DIRECTIVE_PREFIX}{directive}"""


def is_in_fork_child(messages: Sequence[Message]) -> bool:
    """Detect if conversation history is already inside a forked child agent."""
    needle = f"<{FORK_BOILERPLATE_TAG}>"
    for msg in messages:
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            if needle in content:
                return True
        elif isinstance(content, (list, tuple)):
            for block in content:
                if isinstance(block, dict):
                    text_val = block.get("text")
                    if isinstance(text_val, str) and needle in text_val:
                        return True
                elif isinstance(block, str) and needle in block:
                    return True
    return False


def build_forked_messages(
    directive: str,
    assistant_message: Message,
    parent_history: Sequence[Message] | None = None,
) -> list[Message]:
    """
    Build forked conversation messages for a child agent sharing parent prefix.

    1. Keeps sanitized parent history (if provided).
    2. Keeps assistant message with all content blocks.
    3. Builds tool results for each tool use with identical placeholder text.
    4. Appends child directive user message.
    """
    history = list(parent_history or [])
    tool_use_ids = _get_tool_use_ids_from_assistant(assistant_message)

    result_messages: list[Message] = []
    # Provide placeholder tool results for each tool call
    for tid in tool_use_ids:
        result_messages.append(
            ToolMessage(
                content=FORK_PLACEHOLDER_RESULT,
                tool_call_id=tid,
            )
        )

    # Child directive message
    directive_message = HumanMessage(content=build_child_message(directive))

    return [*history, assistant_message, *result_messages, directive_message]


def build_worktree_notice(parent_cwd: str, worktree_cwd: str) -> str:
    """Notice injected into fork children operating in an isolated git worktree."""
    return (
        f"You've inherited the conversation context above from a parent agent working in {parent_cwd}. "
        f"You are operating in an isolated git worktree at {worktree_cwd} — same repository, "
        "same relative file structure, separate working copy. Paths in the inherited context refer to "
        "the parent's working directory; translate them to your worktree root. Re-read files before editing "
        "if the parent may have modified them since they appear in the context. Your changes stay in this "
        "worktree and will not affect the parent's files."
    )
