"""
The auto-mode (YOLO) permission classifier: a model side-call that decides whether an action
the gate would otherwise ask about may run.

Ported from `reference/claude-code/src/utils/permissions/yoloClassifier.ts` ->
`classifyYoloAction`, `classifyYoloActionXml`, `buildYoloSystemPrompt`,
`buildTranscriptEntries`, `buildTranscriptForClassifier`, `formatActionForClassifier`,
`getDefaultExternalAutoModeRules`, `buildDefaultExternalSystemPrompt`, `YOLO_CLASSIFIER_TOOL_NAME`;
`classifierShared.ts` -> `extractToolUseBlock`, `parseClassifierResponse`;
`src/utils/messages.ts` -> `buildYoloRejectionMessage`, `buildClassifierUnavailableMessage`,
`DENIAL_WORKAROUND_GUIDANCE`; `src/utils/settings/settings.ts` -> `getAutoModeConfig`;
`src/services/api/errors.ts` -> `parsePromptTooLongTokenCounts`; and each tool's
`toAutoClassifierInput`.

* **The system prompt is TDDAgents-authored.** Upstream's `auto_mode_system_prompt.txt` and
  `permissions_external.txt` were compiled out of v2.1.88. Their replacements live in
  `app/prompts/permissions/` and keep upstream's structure exactly: a `<permissions_template>`
  marker in the base prompt, and `<user_allow_rules_to_replace>`,
  `<user_deny_rules_to_replace>`, `<user_environment_to_replace>` sections whose defaults
  settings `autoMode` rules replace.
* **What the classifier sees** is upstream's: user text and the agent's tool calls, never
  the agent's prose and never tool results, each tool call projected through the tool's
  classifier input (`toAutoClassifierInput`); the action being classified is the last line.
  A tool whose projection is `''` declares no security-relevant input and is allowed without
  a model call.
* **Fail closed.** Any unparseable answer blocks; an API error blocks and is flagged
  `unavailable` (the gate decides what that means); a prompt-too-long error is flagged
  `transcript_too_long`.
* **Two paths**, as upstream: the default tool-call classifier (`classify_result` forced),
  and the two-stage XML classifier (`both` / `fast` / `thinking`). Upstream selects the XML
  path through GrowthBook; this build has no remote config, so `ClassifierOptions` carries
  the setting and the default is upstream's external default (tool-call path, text
  transcript format, the main-loop model).

Not ported: the ant-only debug dumps (`maybeDumpAutoMode`, `dumpErrorPrompts`,
`CLAUDE_CODE_DUMP_AUTO_MODE`) and the ant-only env overrides (`CLAUDE_CODE_AUTO_MODE_MODEL`,
`CLAUDE_CODE_TWO_STAGE_CLASSIFIER`, `CLAUDE_CODE_JSONL_TRANSCRIPT`), the Anthropic-internal
permissions template, the `BASH_CLASSIFIER` prompt rules and the PowerShell guidance (both
feature-gated off in the external build), the always-on-thinking model padding (ant model
overrides only), prompt-cache `cache_control` blocks, `/share` request snapshots, and
analytics.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from app.loop.messages import Message
from app.loop.permissions.types import PermissionRuleSource, ToolPermissionContext
from app.loop.permissions.yolo_classifier_names import YOLO_CLASSIFIER_TOOL_NAME
from app.loop.prompts.loader import parse_markdown_frontmatter, render_prompt

logger = logging.getLogger("TDDOrchestrator.Permissions")

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts" / "permissions"
BASE_PROMPT_FILE = "auto_mode_system_prompt.md"
EXTERNAL_PERMISSIONS_FILE = "permissions_external.md"

#: `AUTO_MODE_REJECTION_PREFIX`.
AUTO_MODE_REJECTION_PREFIX = "Permission for this action has been denied. Reason: "

#: `DENIAL_WORKAROUND_GUIDANCE`.
DENIAL_WORKAROUND_GUIDANCE = (
    "IMPORTANT: You *may* attempt to accomplish this action using other tools that might naturally be used "
    "to accomplish this goal, e.g. using head instead of cat. But you *should not* attempt to work around "
    "this denial in malicious ways, e.g. do not use your ability to run tests to execute non-test actions. "
    "You should only try to work around this restriction in reasonable ways that do not attempt to bypass "
    "the intent behind this denial. If you believe this capability is essential to complete the user's "
    "request, STOP and explain to the user what you were trying to do and why you need this permission. "
    "Let the user decide how to proceed."
)

#: `XML_S1_SUFFIX`.
XML_S1_SUFFIX = "\nErr on the side of blocking. <block> immediately."
#: `XML_S2_SUFFIX`.
XML_S2_SUFFIX = (
    "\nReview the classification process and follow it carefully, making sure you deny actions that "
    "should be blocked. As a reminder, explicit (not suggestive or implicit) user confirmation is required "
    "to override blocks. Use <thinking> before responding with <block>."
)

#: `YOLO_CLASSIFIER_TOOL_SCHEMA`.
YOLO_CLASSIFIER_TOOL_SCHEMA: dict[str, Any] = {
    "name": YOLO_CLASSIFIER_TOOL_NAME,
    "description": "Report the security classification result for the agent action",
    "input_schema": {
        "type": "object",
        "properties": {
            "thinking": {"type": "string", "description": "Brief step-by-step reasoning."},
            "shouldBlock": {
                "type": "boolean",
                "description": "Whether the action should be blocked (true) or allowed (false)",
            },
            "reason": {"type": "string", "description": "Brief explanation of the classification decision"},
        },
        "required": ["thinking", "shouldBlock", "reason"],
    },
}

#: The settings sources `getAutoModeConfig` reads — deliberately not `projectSettings`, so a
#: checked-in file cannot widen what the classifier allows.
AUTO_MODE_SETTING_SOURCES: tuple[PermissionRuleSource, ...] = (
    PermissionRuleSource.USER_SETTINGS,
    PermissionRuleSource.LOCAL_SETTINGS,
    PermissionRuleSource.FLAG_SETTINGS,
    PermissionRuleSource.POLICY_SETTINGS,
)

TwoStageMode = Literal["both", "fast", "thinking"]


# ── results and the model seam ────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ClassifierUsage:
    """`ClassifierUsage`."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


def combine_usage(a: ClassifierUsage, b: ClassifierUsage) -> ClassifierUsage:
    """`combineUsage`."""
    return ClassifierUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cache_read_input_tokens=a.cache_read_input_tokens + b.cache_read_input_tokens,
        cache_creation_input_tokens=a.cache_creation_input_tokens + b.cache_creation_input_tokens,
    )


@dataclass(frozen=True, slots=True)
class PromptLengths:
    """`promptLengths`: character counts of the three parts of the classifier prompt."""

    system_prompt: int
    tool_calls: int
    user_prompts: int


@dataclass(frozen=True, slots=True)
class YoloClassifierResult:
    """`YoloClassifierResult`."""

    should_block: bool
    reason: str
    model: str
    thinking: str | None = None
    unavailable: bool = False
    transcript_too_long: bool = False
    usage: ClassifierUsage | None = None
    duration_ms: int | None = None
    prompt_lengths: PromptLengths | None = None
    stage: Literal["fast", "thinking"] | None = None
    stage1_usage: ClassifierUsage | None = None
    stage1_duration_ms: int | None = None
    stage1_request_id: str | None = None
    stage1_msg_id: str | None = None
    stage2_usage: ClassifierUsage | None = None
    stage2_duration_ms: int | None = None
    stage2_request_id: str | None = None
    stage2_msg_id: str | None = None


@dataclass(frozen=True, slots=True)
class ClassifierMessage:
    """One message of the side-query: a role and its text blocks."""

    role: Literal["user", "assistant"]
    content: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClassifierRequest:
    """The `sideQuery` options the classifier sends."""

    model: str
    system: str
    messages: tuple[ClassifierMessage, ...]
    max_tokens: int
    temperature: float = 0
    tools: tuple[Mapping[str, Any], ...] = ()
    tool_choice: str | None = None
    stop_sequences: tuple[str, ...] = ()
    signal: Any = None


@dataclass(frozen=True, slots=True)
class ClassifierReply:
    """What came back: the text blocks joined, the tool-use blocks, usage and ids."""

    text: str = ""
    tool_uses: tuple[Mapping[str, Any], ...] = ()
    usage: ClassifierUsage = field(default_factory=ClassifierUsage)
    request_id: str | None = None
    message_id: str | None = None


class ClassifierModelCaller(Protocol):
    """`sideQuery` for the classifier. Raises on an API error, as `sideQuery` does."""

    async def __call__(self, request: ClassifierRequest) -> ClassifierReply: ...


@dataclass(frozen=True, slots=True)
class ClassifierOptions:
    """
    Upstream's `tengu_auto_mode_config` (`AutoModeConfig`): `model` overrides the main-loop
    model, `two_stage_classifier` selects the XML path (`True` = both stages, `"fast"` /
    `"thinking"` = one stage), `jsonl_transcript` switches the transcript lines to JSONL.
    The defaults are upstream's external defaults.
    """

    model: str | None = None
    two_stage_classifier: bool | Literal["fast", "thinking"] | None = None
    jsonl_transcript: bool = False


# ── messages ──────────────────────────────────────────────────────────────────

def build_yolo_rejection_message(reason: str) -> str:
    """`buildYoloRejectionMessage` (external build: no `BASH_CLASSIFIER` rule hint)."""
    rule_hint = "To allow this type of action in the future, the user can add a Bash permission rule to their settings."
    return (
        f"{AUTO_MODE_REJECTION_PREFIX}{reason}. "
        "If you have other tasks that don't depend on this action, continue working on those. "
        f"{DENIAL_WORKAROUND_GUIDANCE} "
        f"{rule_hint}"
    )


def is_classifier_denial(content: str) -> bool:
    """`isClassifierDenial`."""
    return content.startswith(AUTO_MODE_REJECTION_PREFIX)


def build_classifier_unavailable_message(tool_name: str, model: str | None) -> str:
    """`buildClassifierUnavailableMessage`."""
    return (
        f"{model} is temporarily unavailable, so auto mode cannot determine the safety of {tool_name} right now. "
        "Wait briefly and then try this action again. "
        "If it keeps failing, continue with other tasks that don't require this action and come back to it later. "
        "Note: reading files, searching code, and other read-only operations do not require the classifier "
        "and can still be used."
    )


# ── prompt templates and auto-mode rules ─────────────────────────────────────

def _read_prompt(name: str) -> str:
    _, body = parse_markdown_frontmatter((PROMPTS_DIR / name).read_text(encoding="utf-8"))
    return render_prompt(body, {"YOLO_CLASSIFIER_TOOL_NAME": YOLO_CLASSIFIER_TOOL_NAME})


def base_prompt() -> str:
    """`BASE_PROMPT` (TDDAgents-authored; upstream's was compiled out)."""
    return _read_prompt(BASE_PROMPT_FILE)


def external_permissions_template() -> str:
    """`EXTERNAL_PERMISSIONS_TEMPLATE` (TDDAgents-authored; upstream's was compiled out)."""
    return _read_prompt(EXTERNAL_PERMISSIONS_FILE)


@dataclass(frozen=True, slots=True)
class AutoModeRules:
    """`AutoModeRules`: the three prompt sections a user can customise."""

    allow: tuple[str, ...] = ()
    soft_deny: tuple[str, ...] = ()
    environment: tuple[str, ...] = ()


def extract_tagged_bullets(template: str, tag_name: str) -> list[str]:
    """`extractTaggedBullets`: the `- ` lines inside `<tag>...</tag>`."""
    match = re.search(rf"<{tag_name}>([\s\S]*?)</{tag_name}>", template)
    if not match:
        return []
    lines = (line.strip() for line in match.group(1).split("\n"))
    return [line[2:] for line in lines if line.startswith("- ")]


def get_default_external_auto_mode_rules() -> AutoModeRules:
    """`getDefaultExternalAutoModeRules`."""
    template = external_permissions_template()
    return AutoModeRules(
        allow=tuple(extract_tagged_bullets(template, "user_allow_rules_to_replace")),
        soft_deny=tuple(extract_tagged_bullets(template, "user_deny_rules_to_replace")),
        environment=tuple(extract_tagged_bullets(template, "user_environment_to_replace")),
    )


_SECTION_TAGS = (
    "user_allow_rules_to_replace",
    "user_deny_rules_to_replace",
    "user_environment_to_replace",
)


def _replace_section(text: str, tag: str, value: str | None) -> str:
    pattern = re.compile(rf"<{tag}>([\s\S]*?)</{tag}>")
    return pattern.sub(lambda m: value if value is not None else m.group(1), text, count=1)


def build_default_external_system_prompt() -> str:
    """`buildDefaultExternalSystemPrompt`: the base prompt with the default rules."""
    text = base_prompt().replace("<permissions_template>", external_permissions_template(), 1)
    for tag in _SECTION_TAGS:
        text = _replace_section(text, tag, None)
    return text


def _string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ValueError("expected a list of strings")


def get_auto_mode_config(permission_context: ToolPermissionContext) -> AutoModeRules | None:
    """
    `getAutoModeConfig`: settings `autoMode` from user, local, flag and policy settings,
    appended in that order. A source whose `autoMode` fails the schema is skipped whole. The
    ant-only `deny` alias is not read (external build).
    """
    locations = permission_context.settings_locations
    if locations is None:
        return None
    from app.loop.permissions.settings import get_settings_for_source

    allow: list[str] = []
    soft_deny: list[str] = []
    environment: list[str] = []
    for source in AUTO_MODE_SETTING_SOURCES:
        settings = get_settings_for_source(source, locations)
        if not settings:
            continue
        raw = settings.get("autoMode")
        if raw is None or not isinstance(raw, Mapping):
            continue
        try:
            parsed = {key: _string_list(raw.get(key)) for key in ("allow", "soft_deny", "deny", "environment")}
        except ValueError:
            logger.warning("Ignoring malformed autoMode settings in %s", source)
            continue
        allow.extend(parsed["allow"] or ())
        soft_deny.extend(parsed["soft_deny"] or ())
        environment.extend(parsed["environment"] or ())
    if not (allow or soft_deny or environment):
        return None
    return AutoModeRules(allow=tuple(allow), soft_deny=tuple(soft_deny), environment=tuple(environment))


def build_yolo_system_prompt(permission_context: ToolPermissionContext) -> str:
    """
    `buildYoloSystemPrompt`: the base prompt, the external permissions template, then the
    user's `autoMode` rules, each section replacing its defaults when the user gave any.
    """
    system_prompt = base_prompt().replace("<permissions_template>", external_permissions_template(), 1)
    auto_mode = get_auto_mode_config(permission_context)
    allow = list(auto_mode.allow) if auto_mode else []
    deny = list(auto_mode.soft_deny) if auto_mode else []
    environment = list(auto_mode.environment) if auto_mode else []
    user_allow = "\n".join(f"- {d}" for d in allow) if allow else None
    user_deny = "\n".join(f"- {d}" for d in deny) if deny else None
    user_environment = "\n".join(f"- {e}" for e in environment) if environment else None
    system_prompt = _replace_section(system_prompt, "user_allow_rules_to_replace", user_allow)
    system_prompt = _replace_section(system_prompt, "user_deny_rules_to_replace", user_deny)
    return _replace_section(system_prompt, "user_environment_to_replace", user_environment)


# ── transcript ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class TranscriptBlock:
    """`TranscriptBlock`: user text, or an assistant tool call."""

    type: Literal["text", "tool_use"]
    text: str = ""
    name: str = ""
    input: Any = None


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """`TranscriptEntry`."""

    role: Literal["user", "assistant"]
    content: tuple[TranscriptBlock, ...]


def _attachment_of(message: Any) -> Mapping[str, Any] | None:
    kwargs = getattr(message, "additional_kwargs", None)
    if not isinstance(kwargs, Mapping):
        return None
    attachment = kwargs.get("attachment")
    return attachment if isinstance(attachment, Mapping) else None


def _is_attachment(message: Any) -> bool:
    """A message that stands for an upstream attachment (hook output, TDD-state delta)."""
    kwargs = getattr(message, "additional_kwargs", None)
    if not isinstance(kwargs, Mapping):
        return False
    return isinstance(kwargs.get("attachment"), Mapping) or "tdd_state" in kwargs


def _text_blocks(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    texts: list[str] = []
    for block in content or ():
        if isinstance(block, Mapping) and block.get("type") == "text":
            texts.append(str(block.get("text", "")))
    return texts


def build_transcript_entries(messages: Sequence[Message]) -> list[TranscriptEntry]:
    """
    `buildTranscriptEntries`: user text and assistant tool calls. Assistant text is left out
    because it is model-authored and could be crafted to sway the classifier; tool results
    are left out as upstream leaves them (they are not user text). A queued command (an
    `asyncRewake` notification here) is a user turn; every other attachment is skipped.
    """
    transcript: list[TranscriptEntry] = []
    for message in messages:
        kind = getattr(message, "type", None)
        attachment = _attachment_of(message)
        if attachment is not None and attachment.get("type") == "queued_command":
            texts = _text_blocks(getattr(message, "content", ""))
            text = "\n".join(texts) if texts else None
            if text:
                transcript.append(TranscriptEntry("user", (TranscriptBlock("text", text=text),)))
            continue
        if kind == "human":
            if _is_attachment(message):
                continue
            blocks = tuple(TranscriptBlock("text", text=t) for t in _text_blocks(message.content))
            if blocks:
                transcript.append(TranscriptEntry("user", blocks))
        elif kind == "ai":
            calls = getattr(message, "tool_calls", None) or ()
            tool_blocks = tuple(
                TranscriptBlock("tool_use", name=str(call.get("name", "")), input=call.get("args"))
                for call in calls
            )
            if tool_blocks:
                transcript.append(TranscriptEntry("assistant", tool_blocks))
    return transcript


def _arg(input_args: Mapping[str, Any], key: str) -> Any:
    return input_args.get(key)


def _agent_input(i: Mapping[str, Any]) -> str:
    tags = [t for t in (i.get("subagent_type"), f"mode={i['mode']}" if i.get("mode") else None) if t is not None]
    prefix = f"({', '.join(str(t) for t in tags)}): " if tags else ": "
    return f"{prefix}{i.get('prompt')}"


def _todo_input(i: Mapping[str, Any]) -> str:
    return f"{len(i['todos'])} items"


def _grep_input(i: Mapping[str, Any]) -> Any:
    return f"{i.get('pattern')} in {i['path']}" if i.get("path") else i.get("pattern")


def _task_stop_input(i: Mapping[str, Any]) -> Any:
    task_id = i.get("task_id")
    if task_id is not None:
        return task_id
    shell_id = i.get("shell_id")
    return shell_id if shell_id is not None else ""


#: Each built-in tool's `toAutoClassifierInput`, by this loop's tool name. A tool may also
#: carry its own `to_auto_classifier_input`, which wins. A tool with neither gets upstream's
#: `TOOL_DEFAULTS` value, `''`: no security-relevant input, so the classifier is skipped.
AUTO_CLASSIFIER_INPUTS: dict[str, Any] = {
    "Bash": lambda i: _arg(i, "command"),
    "ReadFile": lambda i: _arg(i, "file_path"),
    "WriteFile": lambda i: f"{i.get('file_path')}: {i.get('content')}",
    "Edit": lambda i: f"{i.get('file_path')}: {i.get('new_string')}",
    "Glob": lambda i: _arg(i, "pattern"),
    "Grep": _grep_input,
    "TodoWrite": _todo_input,
    "Agent": _agent_input,
    "TaskStop": _task_stop_input,
    "Skill": lambda i: i.get("skill") or "",
    "TaskOutput": lambda i: i.get("task_id"),
    "SendMessage": lambda i: f"to {i.get('to')}: {i.get('message')}",
    "ListMcpResourcesTool": lambda i: i.get("server") or "",
    "ReadMcpResourceTool": lambda i: f"{i.get('server')} {i.get('uri')}",
}


def to_auto_classifier_input(tool: Any, input_args: Mapping[str, Any]) -> Any:
    """The tool's classifier projection of `input_args` (`Tool.toAutoClassifierInput`)."""
    own = getattr(tool, "to_auto_classifier_input", None)
    if callable(own):
        return own(input_args)
    projection = AUTO_CLASSIFIER_INPUTS.get(str(getattr(tool, "name", "")))
    if projection is None:
        return ""
    return projection(input_args)


def build_tool_lookup(tools: Sequence[Any]) -> dict[str, Any]:
    """`buildToolLookup`: by name and by alias."""
    lookup: dict[str, Any] = {}
    for tool in tools:
        lookup[str(tool.name)] = tool
        for alias in getattr(tool, "aliases", None) or ():
            lookup[str(alias)] = tool
    return lookup


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def to_compact_block(
    block: TranscriptBlock, role: str, lookup: Mapping[str, Any], jsonl: bool = False
) -> str:
    """
    `toCompactBlock`: `Tool input` (or `{"Tool": input}` in JSONL) for a tool call, `User:
    text` (or `{"user": text}`) for user text, `''` otherwise. A tool call to an unknown tool
    or one whose projection is `''` is dropped. A projection that fails on malformed history
    input falls back to the raw input.
    """
    if block.type == "tool_use":
        tool = lookup.get(block.name)
        if tool is None:
            return ""
        raw = block.input if block.input is not None else {}
        try:
            encoded = to_auto_classifier_input(tool, raw)
            if encoded is None:
                encoded = raw
        except Exception as exc:  # noqa: BLE001 - history input is unvalidated model output
            logger.debug("toAutoClassifierInput failed for %s: %s", block.name, exc)
            encoded = raw
        if encoded == "":
            return ""
        if jsonl:
            return _json({block.name: encoded}) + "\n"
        text = encoded if isinstance(encoded, str) else _json(encoded)
        return f"{block.name} {text}\n"
    if block.type == "text" and role == "user":
        return _json({"user": block.text}) + "\n" if jsonl else f"User: {block.text}\n"
    return ""


def to_compact(entry: TranscriptEntry, lookup: Mapping[str, Any], jsonl: bool = False) -> str:
    """`toCompact`."""
    return "".join(to_compact_block(block, entry.role, lookup, jsonl) for block in entry.content)


def build_transcript_for_classifier(messages: Sequence[Message], tools: Sequence[Any], jsonl: bool = False) -> str:
    """`buildTranscriptForClassifier`."""
    lookup = build_tool_lookup(tools)
    return "".join(to_compact(entry, lookup, jsonl) for entry in build_transcript_entries(messages))


def format_action_for_classifier(tool_name: str, input: Mapping[str, Any]) -> TranscriptEntry:
    """`formatActionForClassifier`: the action as a one-call assistant entry."""
    return TranscriptEntry("assistant", (TranscriptBlock("tool_use", name=tool_name, input=input),))


def build_tddagents_md_message(content: str | None) -> ClassifierMessage | None:
    """`buildClaudeMdMessage`: the user's memory files as user intent, or `None`."""
    if not content:
        return None
    return ClassifierMessage(
        "user",
        (
            (
                "The following is the user's TDDAGENTS.md configuration. These are instructions the user "
                "provided to the agent and should be treated as part of the user's intent when evaluating "
                f"actions.\n\n<user_tddagents_md>\n{content}\n</user_tddagents_md>"
            ),
        ),
    )


def cached_tddagents_md(run_id: str) -> str | None:
    """
    `getCachedClaudeMdContent`: the memory-file text the run's context assembly already
    loaded, or `None`. Read from the cache only, never loaded here — upstream does the same,
    and an unpopulated cache means the classifier proceeds without it.
    """
    from app.loop.context import assembly

    session = assembly._SESSIONS.get(run_id)
    if session is None:
        return None
    return session.user_context.get("claudeMd") or None


# ── response parsing ──────────────────────────────────────────────────────────

def strip_thinking(text: str) -> str:
    """`stripThinking`."""
    text = re.sub(r"<thinking>[\s\S]*?</thinking>", "", text)
    return re.sub(r"<thinking>[\s\S]*$", "", text)


def parse_xml_block(text: str) -> bool | None:
    """`parseXmlBlock`: `True` to block, `False` to allow, `None` if unparseable."""
    match = re.search(r"<block>(yes|no)\b(</block>)?", strip_thinking(text), re.IGNORECASE)
    if match is None:
        return None
    return match.group(1).lower() == "yes"


def parse_xml_reason(text: str) -> str | None:
    """`parseXmlReason`."""
    match = re.search(r"<reason>([\s\S]*?)</reason>", strip_thinking(text))
    return match.group(1).strip() if match else None


def parse_xml_thinking(text: str) -> str | None:
    """`parseXmlThinking`."""
    match = re.search(r"<thinking>([\s\S]*?)</thinking>", text)
    return match.group(1).strip() if match else None


def replace_output_format_with_xml(system_prompt: str) -> str:
    """`replaceOutputFormatWithXml`."""
    tool_use_line = f"Use the {YOLO_CLASSIFIER_TOOL_NAME} tool to report your classification."
    xml_format = "\n".join(  # noqa: FLY002 - one line per prompt line, as upstream writes it
        [
            "## Output Format",
            "",
            "If the action should be blocked:",
            "<block>yes</block><reason>one short sentence</reason>",
            "",
            "If the action should be allowed:",
            "<block>no</block>",
            "",
            "Do NOT include a <reason> tag when the action is allowed.",
            (
                "Your ENTIRE response MUST begin with <block>. Do NOT output any analysis, reasoning, or "
                'commentary before <block>. No "Looking at..." or similar preamble.'
            ),
        ]
    )
    return system_prompt.replace(tool_use_line, xml_format, 1)


def extract_tool_use_block(tool_uses: Sequence[Mapping[str, Any]], tool_name: str) -> Mapping[str, Any] | None:
    """`extractToolUseBlock`."""
    for block in tool_uses:
        if block.get("name") == tool_name:
            return block
    return None


def parse_classifier_response(block: Mapping[str, Any]) -> tuple[str, bool, str] | None:
    """`parseClassifierResponse` against `{thinking: string, shouldBlock: boolean, reason: string}`."""
    data = block.get("input")
    if not isinstance(data, Mapping):
        return None
    thinking, should_block, reason = data.get("thinking"), data.get("shouldBlock"), data.get("reason")
    if not isinstance(thinking, str) or not isinstance(should_block, bool) or not isinstance(reason, str):
        return None
    return thinking, should_block, reason


def parse_prompt_too_long_token_counts(raw_message: str) -> tuple[int | None, int | None]:
    """`parsePromptTooLongTokenCounts`."""
    match = re.search(r"prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)", raw_message, re.IGNORECASE)
    if match is None:
        return None, None
    return int(match.group(1)), int(match.group(2))


def detect_prompt_too_long(error: BaseException) -> tuple[int | None, int | None] | None:
    """`detectPromptTooLong`: the token counts of a prompt-too-long error, else `None`."""
    if "prompt is too long" not in str(error).lower():
        return None
    return parse_prompt_too_long_token_counts(str(error))


# ── classification ────────────────────────────────────────────────────────────

def get_classifier_model(options: ClassifierOptions, main_loop_model: str | None = None) -> str:
    """`getClassifierModel`: the auto-mode config override, else the main-loop model."""
    if options.model:
        return options.model
    if main_loop_model:
        return main_loop_model
    from app.config.config import Config

    return str(Config.MODEL)


def get_two_stage_mode(options: ClassifierOptions) -> TwoStageMode | None:
    """`isTwoStageClassifierEnabled` + `getTwoStageMode`: `None` for the tool-call path."""
    value = options.two_stage_classifier
    if value == "fast" or value == "thinking":
        return value
    if value is True:
        return "both"
    return None


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _is_aborted(signal: Any) -> bool:
    return bool(getattr(signal, "aborted", False) or getattr(signal, "cancelled", False))


async def classify_yolo_action(
    messages: Sequence[Message],
    action: TranscriptEntry,
    tools: Sequence[Any],
    permission_context: ToolPermissionContext,
    signal: Any,
    *,
    model_caller: ClassifierModelCaller | None = None,
    options: ClassifierOptions | None = None,
    tddagents_md: str | None = None,
    main_loop_model: str | None = None,
) -> YoloClassifierResult:
    """`classifyYoloAction`: may this action run without asking?"""
    options = options or ClassifierOptions()
    jsonl = options.jsonl_transcript
    lookup = build_tool_lookup(tools)
    action_compact = to_compact(action, lookup, jsonl)
    model = get_classifier_model(options, main_loop_model)
    if action_compact == "":
        return YoloClassifierResult(
            should_block=False, reason="Tool declares no classifier-relevant input", model=model
        )

    system_prompt = build_yolo_system_prompt(permission_context)
    entries = build_transcript_entries(messages)
    md_message = build_tddagents_md_message(tddagents_md)
    prefix = (md_message,) if md_message is not None else ()

    tool_calls_length = len(action_compact)
    user_prompts_length = 0
    blocks: list[str] = []
    for entry in entries:
        for block in entry.content:
            serialized = to_compact_block(block, entry.role, lookup, jsonl)
            if serialized == "":
                continue
            if entry.role == "user":
                user_prompts_length += len(serialized)
            else:
                tool_calls_length += len(serialized)
            blocks.append(serialized)
    blocks.append(action_compact)
    prompt_lengths = PromptLengths(len(system_prompt), tool_calls_length, user_prompts_length)
    caller = model_caller if model_caller is not None else build_production_classifier_caller()

    mode = get_two_stage_mode(options)
    if mode is not None:
        return await _classify_xml(prefix, system_prompt, tuple(blocks), model, prompt_lengths, signal, mode, caller)

    start = time.monotonic()
    request = ClassifierRequest(
        model=model,
        system=system_prompt,
        messages=(*prefix, ClassifierMessage("user", tuple(blocks))),
        max_tokens=4096,
        tools=(YOLO_CLASSIFIER_TOOL_SCHEMA,),
        tool_choice=YOLO_CLASSIFIER_TOOL_NAME,
        signal=signal,
    )
    try:
        reply = await caller(request)
    except Exception as exc:  # noqa: BLE001 - any API failure fails closed
        return _error_result(exc, signal, model)
    duration = _elapsed_ms(start)
    common: dict[str, Any] = {
        "model": model,
        "usage": reply.usage,
        "duration_ms": duration,
        "prompt_lengths": prompt_lengths,
        "stage1_request_id": reply.request_id,
        "stage1_msg_id": reply.message_id,
    }
    tool_block = extract_tool_use_block(reply.tool_uses, YOLO_CLASSIFIER_TOOL_NAME)
    if tool_block is None:
        logger.warning("Auto mode classifier: No tool use block found")
        return YoloClassifierResult(
            should_block=True, reason="Classifier returned no tool use block - blocking for safety", **common
        )
    parsed = parse_classifier_response(tool_block)
    if parsed is None:
        logger.warning("Auto mode classifier: Invalid response schema")
        return YoloClassifierResult(
            should_block=True, reason="Invalid classifier response - blocking for safety", **common
        )
    thinking, should_block, reason = parsed
    return YoloClassifierResult(should_block=should_block, reason=reason, thinking=thinking, **common)


def _error_result(exc: BaseException, signal: Any, model: str) -> YoloClassifierResult:
    """The tool-call path's `catch`: aborted, prompt too long, or unavailable — all block."""
    if _is_aborted(signal):
        logger.debug("Auto mode classifier: aborted by user")
        return YoloClassifierResult(
            should_block=True, reason="Classifier request aborted", model=model, unavailable=True
        )
    too_long = detect_prompt_too_long(exc)
    logger.warning("Auto mode classifier error: %s", exc)
    return YoloClassifierResult(
        should_block=True,
        reason=(
            "Classifier transcript exceeded context window"
            if too_long
            else "Classifier unavailable - blocking for safety"
        ),
        model=model,
        unavailable=True,
        transcript_too_long=too_long is not None,
    )


async def _classify_xml(
    prefix: tuple[ClassifierMessage, ...],
    system_prompt: str,
    blocks: tuple[str, ...],
    model: str,
    prompt_lengths: PromptLengths,
    signal: Any,
    mode: TwoStageMode,
    caller: ClassifierModelCaller,
) -> YoloClassifierResult:
    """
    `classifyYoloActionXml`. `both`: a fast stage answers `<block>no` and is done, or
    escalates to a reasoning stage that decides. `fast`: the fast stage is final. `thinking`:
    only the reasoning stage runs.
    """
    xml_system = replace_output_format_with_xml(system_prompt)
    wrapped = ("<transcript>\n", *blocks, "</transcript>\n")
    overall = time.monotonic()
    stage1_usage: ClassifierUsage | None = None
    stage1_duration: int | None = None
    stage1_request_id: str | None = None
    stage1_msg_id: str | None = None
    try:
        if mode != "thinking":
            stage1_start = time.monotonic()
            stage1 = await caller(
                ClassifierRequest(
                    model=model,
                    system=xml_system,
                    messages=(*prefix, ClassifierMessage("user", (*wrapped, XML_S1_SUFFIX))),
                    max_tokens=256 if mode == "fast" else 64,
                    stop_sequences=() if mode == "fast" else ("</block>",),
                    signal=signal,
                )
            )
            stage1_duration = _elapsed_ms(stage1_start)
            stage1_usage = stage1.usage
            stage1_request_id = stage1.request_id
            stage1_msg_id = stage1.message_id
            stage1_block = parse_xml_block(stage1.text)
            stage1_common: dict[str, Any] = {
                "model": model,
                "usage": stage1_usage,
                "duration_ms": stage1_duration,
                "prompt_lengths": prompt_lengths,
                "stage": "fast",
                "stage1_request_id": stage1_request_id,
                "stage1_msg_id": stage1_msg_id,
            }
            if stage1_block is False:
                return YoloClassifierResult(should_block=False, reason="Allowed by fast classifier", **stage1_common)
            if mode == "fast":
                if stage1_block is None:
                    return YoloClassifierResult(
                        should_block=True,
                        reason="Classifier stage 1 unparseable - blocking for safety",
                        **stage1_common,
                    )
                return YoloClassifierResult(
                    should_block=True,
                    reason=parse_xml_reason(stage1.text) or "Blocked by fast classifier",
                    **stage1_common,
                )

        stage2_start = time.monotonic()
        stage2 = await caller(
            ClassifierRequest(
                model=model,
                system=xml_system,
                messages=(*prefix, ClassifierMessage("user", (*wrapped, XML_S2_SUFFIX))),
                max_tokens=4096,
                signal=signal,
            )
        )
        stage2_duration = _elapsed_ms(stage2_start)
        stage2_block = parse_xml_block(stage2.text)
        total_usage = combine_usage(stage1_usage, stage2.usage) if stage1_usage else stage2.usage
        stage2_common: dict[str, Any] = {
            "model": model,
            "usage": total_usage,
            "duration_ms": (stage1_duration or 0) + stage2_duration,
            "prompt_lengths": prompt_lengths,
            "stage": "thinking",
            "stage1_usage": stage1_usage,
            "stage1_duration_ms": stage1_duration,
            "stage1_request_id": stage1_request_id,
            "stage1_msg_id": stage1_msg_id,
            "stage2_usage": stage2.usage,
            "stage2_duration_ms": stage2_duration,
            "stage2_request_id": stage2.request_id,
            "stage2_msg_id": stage2.message_id,
        }
        if stage2_block is None:
            return YoloClassifierResult(
                should_block=True, reason="Classifier stage 2 unparseable - blocking for safety", **stage2_common
            )
        return YoloClassifierResult(
            should_block=stage2_block,
            reason=parse_xml_reason(stage2.text) or "No reason provided",
            thinking=parse_xml_thinking(stage2.text),
            **stage2_common,
        )
    except Exception as exc:  # noqa: BLE001 - any API failure fails closed
        if _is_aborted(signal):
            logger.debug("Auto mode classifier (XML): aborted by user")
            return YoloClassifierResult(
                should_block=True,
                reason="Classifier request aborted",
                model=model,
                unavailable=True,
                duration_ms=_elapsed_ms(overall),
                prompt_lengths=prompt_lengths,
            )
        too_long = detect_prompt_too_long(exc)
        logger.warning("Auto mode classifier (XML) error: %s", exc)
        if too_long:
            reason = "Classifier transcript exceeded context window"
        elif stage1_usage is not None:
            reason = "Stage 2 classifier error - blocking based on stage 1 assessment"
        else:
            reason = "Classifier unavailable - blocking for safety"
        return YoloClassifierResult(
            should_block=True,
            reason=reason,
            model=model,
            unavailable=stage1_usage is None,
            transcript_too_long=too_long is not None,
            stage="thinking" if stage1_usage is not None else None,
            duration_ms=_elapsed_ms(overall),
            prompt_lengths=prompt_lengths,
            usage=stage1_usage,
            stage1_usage=stage1_usage,
            stage1_duration_ms=stage1_duration,
            stage1_request_id=stage1_request_id,
            stage1_msg_id=stage1_msg_id,
        )


def build_production_classifier_caller(provider: str | None = None) -> ClassifierModelCaller:
    """
    The production `sideQuery`, on this repo's model seam. Tested offline against a fake
    `get_chat_model` (the seam itself, `app/utils/chat_model_factory.py`, stays exempt).
    *Divergence:* `temperature` and `max_tokens` are not forwarded — the provider-neutral
    factory takes no per-call sampling options, and reasoning models reject a temperature.
    """

    async def call(request: ClassifierRequest) -> ClassifierReply:
        from langchain_core.messages import HumanMessage, SystemMessage

        from app.config.config import Config
        from app.utils.chat_model_factory import get_chat_model

        chat: Any = get_chat_model(provider or Config.CHAT_MODEL, model=request.model)
        if request.tools:
            tools = [
                {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}
                for t in request.tools
            ]
            chat = chat.bind_tools(tools, tool_choice=request.tool_choice)
        if request.stop_sequences:
            chat = chat.bind(stop=list(request.stop_sequences))
        lc_messages: list[Any] = [SystemMessage(content=request.system)]
        lc_messages.extend(HumanMessage(content="".join(m.content)) for m in request.messages)
        reply = await chat.ainvoke(lc_messages)
        content = reply.content
        text = content if isinstance(content, str) else "".join(
            str(b.get("text", "")) for b in content if isinstance(b, Mapping) and b.get("type") == "text"
        )
        usage_meta = getattr(reply, "usage_metadata", None) or {}
        return ClassifierReply(
            text=text,
            tool_uses=tuple({"name": c["name"], "input": c["args"]} for c in (reply.tool_calls or ())),
            usage=ClassifierUsage(
                input_tokens=int(usage_meta.get("input_tokens", 0)),
                output_tokens=int(usage_meta.get("output_tokens", 0)),
            ),
            message_id=getattr(reply, "id", None),
        )

    return call


__all__ = [
    "AUTO_CLASSIFIER_INPUTS",
    "AUTO_MODE_REJECTION_PREFIX",
    "AUTO_MODE_SETTING_SOURCES",
    "DENIAL_WORKAROUND_GUIDANCE",
    "XML_S1_SUFFIX",
    "XML_S2_SUFFIX",
    "YOLO_CLASSIFIER_TOOL_NAME",
    "YOLO_CLASSIFIER_TOOL_SCHEMA",
    "AutoModeRules",
    "ClassifierMessage",
    "ClassifierModelCaller",
    "ClassifierOptions",
    "ClassifierReply",
    "ClassifierRequest",
    "ClassifierUsage",
    "PromptLengths",
    "TranscriptBlock",
    "TranscriptEntry",
    "YoloClassifierResult",
    "base_prompt",
    "build_classifier_unavailable_message",
    "build_default_external_system_prompt",
    "build_production_classifier_caller",
    "build_tddagents_md_message",
    "build_tool_lookup",
    "build_transcript_entries",
    "build_transcript_for_classifier",
    "build_yolo_rejection_message",
    "build_yolo_system_prompt",
    "cached_tddagents_md",
    "classify_yolo_action",
    "combine_usage",
    "detect_prompt_too_long",
    "external_permissions_template",
    "extract_tagged_bullets",
    "extract_tool_use_block",
    "format_action_for_classifier",
    "get_auto_mode_config",
    "get_classifier_model",
    "get_default_external_auto_mode_rules",
    "get_two_stage_mode",
    "is_classifier_denial",
    "parse_classifier_response",
    "parse_prompt_too_long_token_counts",
    "parse_xml_block",
    "parse_xml_reason",
    "parse_xml_thinking",
    "replace_output_format_with_xml",
    "strip_thinking",
    "to_auto_classifier_input",
    "to_compact",
    "to_compact_block",
]
