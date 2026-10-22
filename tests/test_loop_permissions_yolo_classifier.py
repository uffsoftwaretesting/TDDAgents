"""
Tests for the auto-mode (YOLO) classifier port (`app/loop/permissions/yolo_classifier.py`).

Every model call goes through a scripted `ClassifierModelCaller`; nothing reaches a provider.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.loop.context import assembly
from app.loop.permissions import yolo_classifier as yc
from app.loop.permissions.settings import SettingsLocations
from app.loop.permissions.types import ToolPermissionContext
from app.loop.permissions.yolo_classifier_names import YOLO_CLASSIFIER_TOOL_NAME as NAME_CONSTANT

# ── helpers ───────────────────────────────────────────────────────────────────


def tool(name: str, aliases: tuple[str, ...] = (), **attrs: Any) -> Any:
    return SimpleNamespace(name=name, aliases=aliases, **attrs)


TOOLS = (
    tool("Bash"), tool("ReadFile"), tool("WriteFile"), tool("Edit"), tool("Glob"), tool("Grep"),
    tool("TodoWrite"), tool("Agent", aliases=("Task",)), tool("TaskStop"), tool("RunTests"),
)


class Caller:
    """Scripted classifier calls: replies (or exceptions) in order, requests recorded."""

    def __init__(self, *replies: Any, abort: Any = None) -> None:
        self.replies = list(replies)
        self.requests: list[yc.ClassifierRequest] = []
        self.abort = abort

    async def __call__(self, request: yc.ClassifierRequest) -> yc.ClassifierReply:
        self.requests.append(request)
        if self.abort is not None:
            self.abort.aborted = True
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        assert isinstance(reply, yc.ClassifierReply)
        return reply


def tool_reply(should_block: Any = False, reason: Any = "fine", thinking: Any = "t", **kw: Any) -> yc.ClassifierReply:
    return yc.ClassifierReply(
        tool_uses=({"name": "classify_result", "input": {"thinking": thinking, "shouldBlock": should_block,
                                                         "reason": reason}},),
        usage=yc.ClassifierUsage(10, 2, 3, 4),
        request_id="req_1",
        message_id="msg_1",
        **kw,
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def classify(action: Any = None, messages: Any = (), caller: Any = None, **kw: Any) -> yc.YoloClassifierResult:
    action = action if action is not None else yc.format_action_for_classifier("Bash", {"command": "ls"})
    perm = kw.pop("perm", ToolPermissionContext())
    signal = kw.pop("signal", SimpleNamespace(aborted=False))
    result: yc.YoloClassifierResult = run(
        yc.classify_yolo_action(messages, action, TOOLS, perm, signal, model_caller=caller, **kw)
    )
    return result


# ── constants and messages ───────────────────────────────────────────────────

def test_tool_name_is_reexported_from_the_names_module():
    assert yc.YOLO_CLASSIFIER_TOOL_NAME is NAME_CONSTANT
    assert yc.YOLO_CLASSIFIER_TOOL_NAME == "classify_result"
    assert "YOLO_CLASSIFIER_TOOL_NAME" in yc.__all__


def test_tool_schema_is_upstreams():
    schema = yc.YOLO_CLASSIFIER_TOOL_SCHEMA
    assert schema["name"] == "classify_result"
    assert schema["description"] == "Report the security classification result for the agent action"
    assert schema["input_schema"]["required"] == ["thinking", "shouldBlock", "reason"]
    props = schema["input_schema"]["properties"]
    assert props["thinking"] == {"type": "string", "description": "Brief step-by-step reasoning."}
    assert props["shouldBlock"]["type"] == "boolean"
    assert props["shouldBlock"]["description"] == "Whether the action should be blocked (true) or allowed (false)"
    assert props["reason"] == {"type": "string", "description": "Brief explanation of the classification decision"}


def test_rejection_message_is_verbatim():
    message = yc.build_yolo_rejection_message("touches ~/.ssh")
    assert message == (
        "Permission for this action has been denied. Reason: touches ~/.ssh. "
        "If you have other tasks that don't depend on this action, continue working on those. "
        + yc.DENIAL_WORKAROUND_GUIDANCE
        + " To allow this type of action in the future, the user can add a Bash permission rule to their settings."
    )
    assert yc.is_classifier_denial(message) is True
    assert yc.is_classifier_denial("Permission denied") is False
    assert yc.DENIAL_WORKAROUND_GUIDANCE.startswith("IMPORTANT: You *may* attempt to accomplish this action")
    assert yc.DENIAL_WORKAROUND_GUIDANCE.endswith("Let the user decide how to proceed.")
    assert "do not use your ability to run tests to execute non-test actions" in yc.DENIAL_WORKAROUND_GUIDANCE


def test_unavailable_message_is_verbatim():
    assert yc.build_classifier_unavailable_message("Bash", "o4-mini") == (
        "o4-mini is temporarily unavailable, so auto mode cannot determine the safety of Bash right now. "
        "Wait briefly and then try this action again. "
        "If it keeps failing, continue with other tasks that don't require this action and come back to it later. "
        "Note: reading files, searching code, and other read-only operations do not require the classifier "
        "and can still be used."
    )


def test_suffixes_are_upstreams():
    assert yc.XML_S1_SUFFIX == "\nErr on the side of blocking. <block> immediately."
    assert yc.XML_S2_SUFFIX.startswith("\nReview the classification process and follow it carefully")
    assert yc.XML_S2_SUFFIX.endswith("Use <thinking> before responding with <block>.")
    assert "explicit (not suggestive or implicit) user confirmation is required" in yc.XML_S2_SUFFIX


# ── prompt templates ─────────────────────────────────────────────────────────

def test_prompt_files_are_tddagents_authored_with_upstream_structure():
    raw_base = (yc.PROMPTS_DIR / yc.BASE_PROMPT_FILE).read_text()
    raw_perm = (yc.PROMPTS_DIR / yc.EXTERNAL_PERMISSIONS_FILE).read_text()
    assert "authored-by: TDDAgents" in raw_base and "authored-by: TDDAgents" in raw_perm
    base = yc.base_prompt()
    assert "authored-by" not in base  # frontmatter stripped
    assert "<permissions_template>" in base
    assert base.rstrip().endswith("Use the classify_result tool to report your classification.")
    assert "{{" not in base
    assert "phase gate" in base  # tells the classifier TDD order is not its job
    perm = yc.external_permissions_template()
    for tag in ("user_allow_rules_to_replace", "user_deny_rules_to_replace", "user_environment_to_replace"):
        assert f"<{tag}>" in perm and f"</{tag}>" in perm


def test_default_rules_are_the_tagged_bullets():
    rules = yc.get_default_external_auto_mode_rules()
    assert rules.allow and rules.soft_deny and rules.environment
    assert any(r.startswith("Code from External:") for r in rules.soft_deny)
    assert any(r.startswith("Irreversible Local Destruction:") for r in rules.soft_deny)
    assert any(r.startswith("Unauthorized Persistence:") for r in rules.soft_deny)
    assert any(r.startswith("Security Weaken:") for r in rules.soft_deny)
    assert all(not r.startswith("- ") for r in rules.allow)


def test_extract_tagged_bullets():
    template = "<x>\n- one\n  - two  \nnot a bullet\n-three\n</x>"
    assert yc.extract_tagged_bullets(template, "x") == ["one", "two"]
    assert yc.extract_tagged_bullets(template, "missing") == []
    assert yc.extract_tagged_bullets("<x></x>", "x") == []


def test_default_system_prompt_inlines_the_defaults():
    prompt = yc.build_default_external_system_prompt()
    assert "<permissions_template>" not in prompt
    assert "<user_" not in prompt
    for rule in yc.get_default_external_auto_mode_rules().soft_deny:
        assert f"- {rule}" in prompt


def _settings(tmp_path: Path, **sources: Any) -> ToolPermissionContext:
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    managed = tmp_path / "etc"
    paths = {
        "user": home / ".tddagents" / "settings.json",
        "project": proj / ".tddagents" / "settings.json",
        "local": proj / ".tddagents" / "settings.local.json",
        "flag": tmp_path / "flag.json",
        "policy": managed / "managed-settings.json",
    }
    for key, data in sources.items():
        paths[key].parent.mkdir(parents=True, exist_ok=True)
        paths[key].write_text(json.dumps(data))
    locations = SettingsLocations(project_root=proj, home=home, flag_settings_path=paths["flag"], managed_dir=managed)
    return ToolPermissionContext(settings_locations=locations)


def test_auto_mode_config_reads_user_local_flag_policy_in_order_and_never_project(tmp_path):
    ctx = _settings(
        tmp_path,
        user={"autoMode": {"allow": ["u-allow"], "environment": ["u-env"]}},
        project={"autoMode": {"allow": ["project-allow"], "soft_deny": ["project-deny"]}},
        local={"autoMode": {"allow": ["l-allow"], "soft_deny": ["l-deny"]}},
        flag={"autoMode": {"soft_deny": ["f-deny"], "deny": ["ant-only"]}},
        policy={"autoMode": {"environment": ["p-env"]}},
    )
    rules = yc.get_auto_mode_config(ctx)
    assert rules == yc.AutoModeRules(
        allow=("u-allow", "l-allow"), soft_deny=("l-deny", "f-deny"), environment=("u-env", "p-env")
    )


def test_auto_mode_config_none_cases(tmp_path, caplog):
    assert yc.get_auto_mode_config(ToolPermissionContext()) is None
    assert yc.get_auto_mode_config(_settings(tmp_path)) is None
    ctx = _settings(tmp_path, user={"autoMode": {}}, local={"autoMode": "nope"}, flag={"other": 1})
    assert yc.get_auto_mode_config(ctx) is None
    bad = _settings(tmp_path / "b", user={"autoMode": {"allow": [1], "soft_deny": ["kept?"]}},
                    local={"autoMode": {"allow": ["ok"]}})
    with caplog.at_level("WARNING", logger="TDDOrchestrator.Permissions"):
        assert yc.get_auto_mode_config(bad) == yc.AutoModeRules(allow=("ok",))
    assert any("Ignoring malformed autoMode settings" in r.getMessage() for r in caplog.records)


def test_system_prompt_user_rules_replace_each_section(tmp_path):
    ctx = _settings(tmp_path, user={"autoMode": {"allow": ["A1", "A2"], "soft_deny": ["D1"]}})
    prompt = yc.build_yolo_system_prompt(ctx)
    defaults = yc.get_default_external_auto_mode_rules()
    assert "- A1\n- A2" in prompt and "- D1" in prompt
    assert all(f"- {r}" not in prompt for r in defaults.allow)
    assert all(f"- {r}" not in prompt for r in defaults.soft_deny)
    assert all(f"- {r}" in prompt for r in defaults.environment)  # no user env: defaults stay
    assert "<user_" not in prompt and "<permissions_template>" not in prompt
    env_only = yc.build_yolo_system_prompt(_settings(tmp_path / "e", user={"autoMode": {"environment": ["E"]}}))
    assert "- E" in env_only and all(f"- {r}" not in env_only for r in defaults.environment)
    assert yc.build_yolo_system_prompt(ToolPermissionContext()) == yc.build_default_external_system_prompt()


# ── transcript ───────────────────────────────────────────────────────────────

def test_transcript_entries_keep_user_text_and_tool_calls_only():
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="build a calculator"),
        HumanMessage(content=[{"type": "text", "text": "a"}, {"type": "image_url", "image_url": "x"},
                              {"type": "text", "text": "b"}]),
        HumanMessage(content=[{"type": "image_url", "image_url": "x"}]),
        AIMessage(content="I will run ls", tool_calls=[{"name": "Bash", "args": {"command": "ls"}, "id": "1"}]),
        AIMessage(content="just talk"),
        ToolMessage(content="out", tool_call_id="1"),
        HumanMessage(content="<system-reminder>hook</system-reminder>",
                     additional_kwargs={"is_meta": True, "attachment": {"type": "hook_additional_context"}}),
        HumanMessage(content="tdd", additional_kwargs={"is_meta": True, "tdd_state": {}}),
        HumanMessage(content="Stop hook feedback:\nx", additional_kwargs={"is_meta": True}),
        HumanMessage(content="wake up", additional_kwargs={"attachment": {"type": "queued_command"}}),
        HumanMessage(content="", additional_kwargs={"attachment": {"type": "queued_command"}}),
    ]
    entries = yc.build_transcript_entries(messages)
    assert entries == [
        yc.TranscriptEntry("user", (yc.TranscriptBlock("text", text="build a calculator"),)),
        yc.TranscriptEntry("user", (yc.TranscriptBlock("text", text="a"), yc.TranscriptBlock("text", text="b"))),
        yc.TranscriptEntry("assistant", (yc.TranscriptBlock("tool_use", name="Bash", input={"command": "ls"}),)),
        yc.TranscriptEntry("user", (yc.TranscriptBlock("text", text="Stop hook feedback:\nx"),)),
        yc.TranscriptEntry("user", (yc.TranscriptBlock("text", text="wake up"),)),
    ]


def test_queued_command_with_block_content_joins_text_blocks():
    msg = HumanMessage(content=[{"type": "text", "text": "x"}, {"type": "image_url"}, {"type": "text", "text": "y"}],
                       additional_kwargs={"attachment": {"type": "queued_command"}})
    assert yc.build_transcript_entries([msg]) == [
        yc.TranscriptEntry("user", (yc.TranscriptBlock("text", text="x\ny"),))
    ]


def test_messages_without_kwargs_are_handled():
    msg: Any = SimpleNamespace(type="human", content="hi")
    assert yc.build_transcript_entries([msg]) == [yc.TranscriptEntry("user", (yc.TranscriptBlock("text", text="hi"),))]
    odd: Any = SimpleNamespace(type="human", content="x", additional_kwargs="not a mapping")
    assert len(yc.build_transcript_entries([odd])) == 1
    ai: Any = SimpleNamespace(type="ai", content="x")
    assert yc.build_transcript_entries([ai]) == []


@pytest.mark.parametrize("name, args, expected", [
    ("Bash", {"command": "rm -rf x"}, "rm -rf x"),
    ("ReadFile", {"file_path": "a.py"}, "a.py"),
    ("WriteFile", {"file_path": "a.py", "content": "x=1"}, "a.py: x=1"),
    ("Edit", {"file_path": "a.py", "old_string": "o", "new_string": "n"}, "a.py: n"),
    ("Glob", {"pattern": "**/*.py"}, "**/*.py"),
    ("Grep", {"pattern": "def"}, "def"),
    ("Grep", {"pattern": "def", "path": "src"}, "def in src"),
    ("TodoWrite", {"todos": [1, 2, 3]}, "3 items"),
    ("Agent", {"prompt": "p"}, ": p"),
    ("Agent", {"prompt": "p", "subagent_type": "tester"}, "(tester): p"),
    ("Agent", {"prompt": "p", "subagent_type": "tester", "mode": "plan"}, "(tester, mode=plan): p"),
    ("Agent", {"prompt": "p", "mode": "plan"}, "(mode=plan): p"),
    ("TaskStop", {"task_id": "t1", "shell_id": "s1"}, "t1"),
    ("TaskStop", {"shell_id": "s1"}, "s1"),
    ("TaskStop", {}, ""),
    ("RunTests", {"test_path": "tests"}, ""),
])
def test_auto_classifier_inputs(name, args, expected):
    assert yc.to_auto_classifier_input(tool(name), args) == expected


def test_a_tool_may_carry_its_own_projection():
    own = tool("Custom", to_auto_classifier_input=lambda i: f"custom:{i['x']}")
    assert yc.to_auto_classifier_input(own, {"x": 1}) == "custom:1"
    assert yc.to_auto_classifier_input(tool("Unknown"), {"x": 1}) == ""


def test_compact_lines_text_and_jsonl():
    lookup = yc.build_tool_lookup(TOOLS)
    bash = yc.TranscriptBlock("tool_use", name="Bash", input={"command": "ls\nrm"})
    assert yc.to_compact_block(bash, "assistant", lookup) == "Bash ls\nrm\n"
    assert yc.to_compact_block(bash, "assistant", lookup, jsonl=True) == '{"Bash":"ls\\nrm"}\n'
    text = yc.TranscriptBlock("text", text='say "hi"')
    assert yc.to_compact_block(text, "user", lookup) == 'User: say "hi"\n'
    assert yc.to_compact_block(text, "user", lookup, jsonl=True) == '{"user":"say \\"hi\\""}\n'
    assert yc.to_compact_block(text, "assistant", lookup) == ""
    unknown = yc.TranscriptBlock("tool_use", name="Nope", input={})
    assert yc.to_compact_block(unknown, "assistant", lookup) == ""
    silent = yc.TranscriptBlock("tool_use", name="RunTests", input={"test_path": "t"})
    assert yc.to_compact_block(silent, "assistant", lookup) == ""
    by_alias = yc.TranscriptBlock("tool_use", name="Task", input={"prompt": "p"})
    assert yc.to_compact_block(by_alias, "assistant", lookup) == "Task : p\n"
    unicode_text = yc.TranscriptBlock("text", text="café")
    assert yc.to_compact_block(unicode_text, "user", lookup, jsonl=True) == '{"user":"café"}\n'


def test_compact_non_string_projection_and_fallbacks():
    structured = tool("S", to_auto_classifier_input=lambda i: {"k": [1, 2]})
    none_projection = tool("N", to_auto_classifier_input=lambda i: None)
    broken = tool("B", to_auto_classifier_input=lambda i: i["missing"])
    lookup = yc.build_tool_lookup((structured, none_projection, broken))
    block = yc.TranscriptBlock("tool_use", name="S", input={})
    assert yc.to_compact_block(block, "assistant", lookup) == 'S {"k":[1,2]}\n'
    assert yc.to_compact_block(block, "assistant", lookup, jsonl=True) == '{"S":{"k":[1,2]}}\n'
    raw = {"a": "b"}
    assert yc.to_compact_block(yc.TranscriptBlock("tool_use", name="N", input=raw), "assistant", lookup) == \
        'N {"a":"b"}\n'
    assert yc.to_compact_block(yc.TranscriptBlock("tool_use", name="B", input=raw), "assistant", lookup) == \
        'B {"a":"b"}\n'
    assert yc.to_compact_block(yc.TranscriptBlock("tool_use", name="N", input=None), "assistant", lookup) == "N {}\n"


def test_transcript_for_classifier_and_tool_lookup():
    messages = [HumanMessage(content="go"),
                AIMessage(content="", tool_calls=[{"name": "Bash", "args": {"command": "ls"}, "id": "1"},
                                                  {"name": "Glob", "args": {"pattern": "*"}, "id": "2"}])]
    assert yc.build_transcript_for_classifier(messages, TOOLS) == "User: go\nBash ls\nGlob *\n"
    assert yc.build_transcript_for_classifier(messages, TOOLS, jsonl=True) == \
        '{"user":"go"}\n{"Bash":"ls"}\n{"Glob":"*"}\n'
    lookup = yc.build_tool_lookup((tool("A", aliases=("B",)), SimpleNamespace(name="C")))
    assert set(lookup) == {"A", "B", "C"}


def test_format_action_for_classifier():
    entry = yc.format_action_for_classifier("Bash", {"command": "ls"})
    assert entry == yc.TranscriptEntry("assistant", (yc.TranscriptBlock("tool_use", name="Bash",
                                                                        input={"command": "ls"}),))


def test_tddagents_md_message_and_cache():
    assert yc.build_tddagents_md_message(None) is None
    assert yc.build_tddagents_md_message("") is None
    msg = yc.build_tddagents_md_message("Use tabs.")
    assert msg == yc.ClassifierMessage("user", (
        "The following is the user's TDDAGENTS.md configuration. These are instructions the user provided to "
        "the agent and should be treated as part of the user's intent when evaluating actions.\n\n"
        "<user_tddagents_md>\nUse tabs.\n</user_tddagents_md>",
    ))
    assert yc.cached_tddagents_md("no-such-run") is None
    assembly._SESSIONS["yc-run"] = assembly.SessionContext(user_context={"claudeMd": "rules"}, system_context={})
    assembly._SESSIONS["yc-empty"] = assembly.SessionContext(user_context={"claudeMd": ""}, system_context={})
    try:
        assert yc.cached_tddagents_md("yc-run") == "rules"
        assert yc.cached_tddagents_md("yc-empty") is None
    finally:
        assembly.reset_session_context("yc-run")
        assembly.reset_session_context("yc-empty")


# ── parsing ──────────────────────────────────────────────────────────────────

def test_strip_thinking_and_xml_parsers():
    text = "<thinking>maybe <block>no</block></thinking><block>yes</block><reason> bad </reason>"
    assert yc.strip_thinking(text) == "<block>yes</block><reason> bad </reason>"
    assert yc.strip_thinking("a<thinking>unterminated <block>no") == "a"
    assert yc.parse_xml_block(text) is True
    assert yc.parse_xml_block("<block>NO</block>") is False
    assert yc.parse_xml_block("<block>Yes") is True
    assert yc.parse_xml_block("<block>yesterday</block>") is None
    assert yc.parse_xml_block("<block>no</block><block>yes</block>") is False
    assert yc.parse_xml_block("nothing") is None
    assert yc.parse_xml_reason(text) == "bad"
    assert yc.parse_xml_reason("<thinking><reason>x</reason></thinking>") is None
    assert yc.parse_xml_reason("<reason>a</reason><reason>b</reason>") == "a"
    assert yc.parse_xml_thinking(text) == "maybe <block>no</block>"
    assert yc.parse_xml_thinking("none") is None


def test_replace_output_format_with_xml():
    replaced = yc.replace_output_format_with_xml("head\nUse the classify_result tool to report your classification.")
    assert replaced == "head\n" + "\n".join([
        "## Output Format", "", "If the action should be blocked:",
        "<block>yes</block><reason>one short sentence</reason>", "", "If the action should be allowed:",
        "<block>no</block>", "", "Do NOT include a <reason> tag when the action is allowed.",
        "Your ENTIRE response MUST begin with <block>. Do NOT output any analysis, reasoning, or commentary "
        'before <block>. No "Looking at..." or similar preamble.',
    ])
    assert yc.replace_output_format_with_xml("no marker") == "no marker"


def test_tool_use_extraction_and_response_parsing():
    blocks = ({"name": "other", "input": {}}, {"name": "classify_result", "input": {"x": 1}},
              {"name": "classify_result", "input": {"y": 2}})
    assert yc.extract_tool_use_block(blocks, "classify_result") == {"name": "classify_result", "input": {"x": 1}}
    assert yc.extract_tool_use_block(blocks[:1], "classify_result") is None
    good = {"input": {"thinking": "t", "shouldBlock": True, "reason": "r"}}
    assert yc.parse_classifier_response(good) == ("t", True, "r")
    for bad in ({"input": "str"}, {}, {"input": {"thinking": 1, "shouldBlock": True, "reason": "r"}},
                {"input": {"thinking": "t", "shouldBlock": "yes", "reason": "r"}},
                {"input": {"thinking": "t", "shouldBlock": False, "reason": None}}):
        assert yc.parse_classifier_response(bad) is None


def test_prompt_too_long_detection():
    assert yc.parse_prompt_too_long_token_counts("prompt is too long: 250000 tokens > 200000 maximum") == \
        (250000, 200000)
    assert yc.parse_prompt_too_long_token_counts("Prompt Is Too Long 5 token>4") == (5, 4)
    assert yc.parse_prompt_too_long_token_counts("other") == (None, None)
    assert yc.detect_prompt_too_long(RuntimeError("PROMPT IS TOO LONG")) == (None, None)
    assert yc.detect_prompt_too_long(RuntimeError("prompt is too long: 9 tokens > 8")) == (9, 8)
    assert yc.detect_prompt_too_long(RuntimeError("rate limited")) is None


def test_usage_combination():
    assert yc.combine_usage(yc.ClassifierUsage(1, 2, 3, 4), yc.ClassifierUsage(10, 20, 30, 40)) == \
        yc.ClassifierUsage(11, 22, 33, 44)


def test_model_and_mode_resolution(monkeypatch):
    assert yc.get_classifier_model(yc.ClassifierOptions(model="override"), "main") == "override"
    assert yc.get_classifier_model(yc.ClassifierOptions(), "main") == "main"
    from app.config.config import Config

    monkeypatch.setattr(Config, "MODEL", "cfg-model")
    assert yc.get_classifier_model(yc.ClassifierOptions()) == "cfg-model"
    assert yc.get_two_stage_mode(yc.ClassifierOptions()) is None
    assert yc.get_two_stage_mode(yc.ClassifierOptions(two_stage_classifier=False)) is None
    assert yc.get_two_stage_mode(yc.ClassifierOptions(two_stage_classifier=True)) == "both"
    assert yc.get_two_stage_mode(yc.ClassifierOptions(two_stage_classifier="fast")) == "fast"
    assert yc.get_two_stage_mode(yc.ClassifierOptions(two_stage_classifier="thinking")) == "thinking"
    assert yc.ClassifierOptions() == yc.ClassifierOptions(model=None, two_stage_classifier=None,
                                                          jsonl_transcript=False)


# ── classify_yolo_action: tool-call path ─────────────────────────────────────

def test_an_action_with_no_classifier_input_is_allowed_without_a_call():
    caller = Caller()
    result = classify(yc.format_action_for_classifier("RunTests", {"test_path": "t"}), caller=caller,
                      main_loop_model="m")
    assert result == yc.YoloClassifierResult(should_block=False, reason="Tool declares no classifier-relevant input",
                                             model="m")
    assert caller.requests == []


def test_tool_call_path_request_and_allow():
    caller = Caller(tool_reply(False, "safe read", "looks fine"))
    signal = SimpleNamespace(aborted=False)
    messages = [HumanMessage(content="list files"),
                AIMessage(content="", tool_calls=[{"name": "Glob", "args": {"pattern": "*"}, "id": "1"}])]
    result = classify(messages=messages, caller=caller, main_loop_model="m", signal=signal, tddagents_md="rules")
    assert result.should_block is False and result.reason == "safe read" and result.thinking == "looks fine"
    assert result.model == "m" and result.usage == yc.ClassifierUsage(10, 2, 3, 4)
    assert (result.stage1_request_id, result.stage1_msg_id) == ("req_1", "msg_1")
    assert result.unavailable is False and result.transcript_too_long is False and result.stage is None
    assert isinstance(result.duration_ms, int) and result.duration_ms >= 0
    system = yc.build_yolo_system_prompt(ToolPermissionContext())
    assert result.prompt_lengths == yc.PromptLengths(len(system), len("Bash ls\n") + len("Glob *\n"),
                                                     len("User: list files\n"))
    (request,) = caller.requests
    assert request.model == "m" and request.system == system and request.max_tokens == 4096
    assert request.temperature == 0 and request.signal is signal and request.stop_sequences == ()
    assert request.tools == (yc.YOLO_CLASSIFIER_TOOL_SCHEMA,) and request.tool_choice == "classify_result"
    assert request.messages == (yc.build_tddagents_md_message("rules"),
                                yc.ClassifierMessage("user", ("User: list files\n", "Glob *\n", "Bash ls\n")))


def test_tool_call_path_block_and_no_prefix_without_md():
    caller = Caller(tool_reply(True, "deletes files"))
    result = classify(caller=caller, main_loop_model="m")
    assert result.should_block is True and result.reason == "deletes files"
    assert len(caller.requests[0].messages) == 1


def test_tool_call_path_uses_jsonl_when_configured():
    caller = Caller(tool_reply())
    classify(messages=[HumanMessage(content="go")], caller=caller, options=yc.ClassifierOptions(jsonl_transcript=True))
    assert caller.requests[0].messages[-1].content == ('{"user":"go"}\n', '{"Bash":"ls"}\n')


def test_tool_call_path_missing_or_invalid_tool_use_blocks():
    no_tool = classify(caller=Caller(yc.ClassifierReply(text="hmm", usage=yc.ClassifierUsage(1, 1))),
                       main_loop_model="m")
    assert no_tool.should_block is True
    assert no_tool.reason == "Classifier returned no tool use block - blocking for safety"
    assert no_tool.usage == yc.ClassifierUsage(1, 1) and no_tool.unavailable is False
    invalid = classify(caller=Caller(tool_reply("maybe")), main_loop_model="m")
    assert invalid.should_block is True and invalid.reason == "Invalid classifier response - blocking for safety"
    assert invalid.stage1_request_id == "req_1" and invalid.prompt_lengths is not None


def test_tool_call_path_errors_fail_closed():
    error = classify(caller=Caller(RuntimeError("boom")), main_loop_model="m")
    assert error == yc.YoloClassifierResult(should_block=True, reason="Classifier unavailable - blocking for safety",
                                            model="m", unavailable=True)
    too_long = classify(caller=Caller(RuntimeError("prompt is too long: 9 tokens > 8")), main_loop_model="m")
    assert too_long == yc.YoloClassifierResult(should_block=True, model="m", unavailable=True,
                                               reason="Classifier transcript exceeded context window",
                                               transcript_too_long=True)
    signal = SimpleNamespace(aborted=False)
    aborted = classify(caller=Caller(RuntimeError("cancelled"), abort=signal), signal=signal, main_loop_model="m")
    assert aborted == yc.YoloClassifierResult(should_block=True, reason="Classifier request aborted", model="m",
                                              unavailable=True)
    token = SimpleNamespace(cancelled=True)
    assert classify(caller=Caller(RuntimeError("x")), signal=token).reason == "Classifier request aborted"


def test_the_default_caller_is_the_production_one(monkeypatch):
    caller = Caller(tool_reply())
    monkeypatch.setattr(yc, "build_production_classifier_caller", lambda: caller)
    classify(caller=None)
    assert len(caller.requests) == 1


# ── classify_yolo_action: XML path ───────────────────────────────────────────

def xml(text: str, usage: tuple[int, ...] = (1, 1, 0, 0), rid: str = "r", mid: str = "m") -> yc.ClassifierReply:
    return yc.ClassifierReply(text=text, usage=yc.ClassifierUsage(*usage), request_id=rid, message_id=mid)


def xml_classify(mode: Any, *replies: Any, **kw: Any) -> tuple[yc.YoloClassifierResult, Caller]:
    caller = Caller(*replies, abort=kw.pop("abort", None))
    result = classify(caller=caller, options=yc.ClassifierOptions(two_stage_classifier=mode),
                      main_loop_model="m", messages=[HumanMessage(content="go")], **kw)
    return result, caller


def test_xml_both_fast_allow_returns_after_stage_one():
    result, caller = xml_classify(True, xml("<block>no</block>", rid="r1", mid="m1"))
    assert result.should_block is False and result.reason == "Allowed by fast classifier"
    assert result.stage == "fast" and result.usage == yc.ClassifierUsage(1, 1, 0, 0)
    assert (result.stage1_request_id, result.stage1_msg_id) == ("r1", "m1")
    assert result.prompt_lengths is not None and result.duration_ms is not None
    (request,) = caller.requests
    system = yc.replace_output_format_with_xml(yc.build_yolo_system_prompt(ToolPermissionContext()))
    assert request.system == system and request.max_tokens == 64 and request.stop_sequences == ("</block>",)
    assert request.tools == () and request.tool_choice is None
    assert request.messages[-1].content == ("<transcript>\n", "User: go\n", "Bash ls\n", "</transcript>\n",
                                            yc.XML_S1_SUFFIX)


def test_xml_both_escalates_to_stage_two():
    result, caller = xml_classify(
        True, xml("<block>yes", usage=(1, 2, 3, 4), rid="r1", mid="m1"),
        xml("<thinking>hmm</thinking><block>no</block>", usage=(10, 20, 30, 40), rid="r2", mid="m2"),
    )
    assert result.should_block is False and result.reason == "No reason provided" and result.thinking == "hmm"
    assert result.stage == "thinking" and result.usage == yc.ClassifierUsage(11, 22, 33, 44)
    assert result.stage1_usage == yc.ClassifierUsage(1, 2, 3, 4)
    assert result.stage2_usage == yc.ClassifierUsage(10, 20, 30, 40)
    assert (result.stage1_request_id, result.stage1_msg_id, result.stage2_request_id, result.stage2_msg_id) == \
        ("r1", "m1", "r2", "m2")
    assert result.duration_ms == (result.stage1_duration_ms or 0) + (result.stage2_duration_ms or 0)
    stage2 = caller.requests[1]
    assert stage2.max_tokens == 4096 and stage2.stop_sequences == ()
    assert stage2.messages[-1].content[-1] == yc.XML_S2_SUFFIX


def test_xml_stage_two_block_with_reason_and_unparseable():
    result, _ = xml_classify(True, xml("garbage"), xml("<block>yes</block><reason>exfil</reason>"))
    assert result.should_block is True and result.reason == "exfil"
    result, _ = xml_classify(True, xml("<block>yes"), xml("no tags"))
    assert result.should_block is True and result.reason == "Classifier stage 2 unparseable - blocking for safety"
    assert result.stage == "thinking"


def test_xml_fast_only_mode():
    allowed, caller = xml_classify("fast", xml("<block>no</block>"))
    assert allowed.should_block is False
    assert caller.requests[0].max_tokens == 256 and caller.requests[0].stop_sequences == ()
    blocked, _ = xml_classify("fast", xml("<block>yes</block><reason>bad</reason>"))
    assert blocked.should_block is True and blocked.reason == "bad" and blocked.stage == "fast"
    default_reason, _ = xml_classify("fast", xml("<block>yes</block>"))
    assert default_reason.reason == "Blocked by fast classifier"
    unparseable, _ = xml_classify("fast", xml("??"))
    assert unparseable.should_block is True
    assert unparseable.reason == "Classifier stage 1 unparseable - blocking for safety"


def test_xml_thinking_only_mode_skips_stage_one():
    result, caller = xml_classify("thinking", xml("<block>no</block>", usage=(5, 5, 0, 0)))
    assert len(caller.requests) == 1 and caller.requests[0].messages[-1].content[-1] == yc.XML_S2_SUFFIX
    assert result.should_block is False and result.stage1_usage is None
    assert result.usage == yc.ClassifierUsage(5, 5, 0, 0) and result.stage1_duration_ms is None
    assert result.duration_ms == result.stage2_duration_ms


def test_xml_errors_fail_closed():
    unavailable, _ = xml_classify(True, RuntimeError("boom"))
    assert unavailable.should_block is True and unavailable.unavailable is True
    assert unavailable.reason == "Classifier unavailable - blocking for safety"
    assert unavailable.stage is None and unavailable.usage is None and unavailable.prompt_lengths is not None
    after_stage1, _ = xml_classify(True, xml("<block>yes", usage=(1, 2, 3, 4), rid="r1"), RuntimeError("boom"))
    assert after_stage1.unavailable is False and after_stage1.stage == "thinking"
    assert after_stage1.reason == "Stage 2 classifier error - blocking based on stage 1 assessment"
    assert after_stage1.usage == yc.ClassifierUsage(1, 2, 3, 4) == after_stage1.stage1_usage
    assert after_stage1.stage1_request_id == "r1" and after_stage1.stage1_duration_ms is not None
    too_long, _ = xml_classify(True, xml("<block>yes"), RuntimeError("prompt is too long: 3 tokens > 2"))
    assert too_long.reason == "Classifier transcript exceeded context window" and too_long.transcript_too_long
    signal = SimpleNamespace(aborted=False)
    aborted, _ = xml_classify(True, RuntimeError("x"), abort=signal, signal=signal)
    assert aborted.reason == "Classifier request aborted" and aborted.unavailable is True
    assert aborted.prompt_lengths is not None and aborted.duration_ms is not None
