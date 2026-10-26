"""
Pre-push gate, cluster B: mutation-driven pins for `app/loop/permissions/yolo_classifier.py`.

Every model call is scripted; the production caller runs against a fake `get_chat_model`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

import app.loop.permissions.yolo_classifier as yc
from app.loop.permissions.settings import SettingsLocations
from app.loop.permissions.types import ToolPermissionContext

TOOLS = (SimpleNamespace(name="Bash", aliases=()), SimpleNamespace(name="RunTests", aliases=()))


class Caller:
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


class Clock:
    """`time.monotonic` that advances one second per call."""

    def __init__(self) -> None:
        self.now = -1.0

    def monotonic(self) -> float:
        self.now += 1.0
        return self.now


def xml(text: str, rid: str = "r", mid: str = "m") -> yc.ClassifierReply:
    return yc.ClassifierReply(text=text, usage=yc.ClassifierUsage(1, 1, 0, 0), request_id=rid, message_id=mid)


def classify(mode: Any, caller: Caller, signal: Any = None, messages: Any = None) -> yc.YoloClassifierResult:
    signal = signal if signal is not None else SimpleNamespace(aborted=False)
    result: yc.YoloClassifierResult = asyncio.run(
        yc.classify_yolo_action(
            messages if messages is not None else [HumanMessage(content="go")],
            yc.format_action_for_classifier("Bash", {"command": "ls"}),
            TOOLS,
            ToolPermissionContext(),
            signal,
            model_caller=caller,
            options=yc.ClassifierOptions(two_stage_classifier=mode),
            main_loop_model="m",
        )
    )
    return result


def _settings(tmp_path: Path, **sources: Any) -> ToolPermissionContext:
    home, proj, managed = tmp_path / "home", tmp_path / "proj", tmp_path / "etc"
    paths = {
        "user": home / ".tddagents" / "settings.json",
        "local": proj / ".tddagents" / "settings.local.json",
    }
    for key, data in sources.items():
        paths[key].parent.mkdir(parents=True, exist_ok=True)
        paths[key].write_text(json.dumps(data))
    locations = SettingsLocations(project_root=proj, home=home, managed_dir=managed)
    return ToolPermissionContext(settings_locations=locations)


# ── prompt templates and rules ────────────────────────────────────────────────

def test_replace_section_replaces_only_the_first_section():
    assert yc._replace_section("<t>a</t>|<t>b</t>", "t", "X") == "X|<t>b</t>"
    assert yc._replace_section("<t>a</t>|<t>b</t>", "t", None) == "a|<t>b</t>"


def test_the_permissions_template_is_inlined_once(monkeypatch):
    monkeypatch.setattr(yc, "base_prompt", lambda: "P <permissions_template> Q <permissions_template>")
    monkeypatch.setattr(yc, "external_permissions_template", lambda: "T")
    assert yc.build_default_external_system_prompt() == "P T Q <permissions_template>"
    assert yc.build_yolo_system_prompt(ToolPermissionContext()) == "P T Q <permissions_template>"


def test_the_output_format_is_replaced_once():
    line = f"Use the {yc.YOLO_CLASSIFIER_TOOL_NAME} tool to report your classification."
    replaced = yc.replace_output_format_with_xml(f"{line}\n{line}")
    assert replaced.startswith("## Output Format\n")
    assert replaced.endswith(f"\n{line}")
    assert replaced.count(line) == 1


def test_auto_mode_config_skips_a_missing_source_and_reads_the_next(tmp_path):
    assert yc.get_auto_mode_config(_settings(tmp_path, local={"autoMode": {"allow": ["L"]}})) == (
        yc.AutoModeRules(allow=("L",))
    )


def test_auto_mode_config_skips_a_source_without_auto_mode_and_reads_the_next(tmp_path):
    ctx = _settings(tmp_path, user={"other": 1}, local={"autoMode": {"allow": ["L"]}})
    assert yc.get_auto_mode_config(ctx) == yc.AutoModeRules(allow=("L",))


def test_the_ant_only_deny_key_is_still_validated(tmp_path):
    """Upstream's schema validates `deny` (the ant-only alias); a bad one sinks its source."""
    ctx = _settings(tmp_path, user={"autoMode": {"deny": [1], "allow": ["u"]}}, local={"autoMode": {"allow": ["L"]}})
    assert yc.get_auto_mode_config(ctx) == yc.AutoModeRules(allow=("L",))


def test_user_rules_are_one_bullet_per_line(tmp_path):
    ctx = _settings(tmp_path, user={"autoMode": {"soft_deny": ["D1", "D2"], "environment": ["E1", "E2"]}})
    prompt = yc.build_yolo_system_prompt(ctx)
    assert "- D1\n- D2" in prompt
    assert "- E1\n- E2" in prompt


# ── transcript ───────────────────────────────────────────────────────────────

def test_a_text_block_without_text_is_empty():
    assert yc._text_blocks([{"type": "text"}]) == [""]


def test_a_message_without_a_type_is_skipped():
    assert yc.build_transcript_entries([SimpleNamespace(content="x")]) == []  # type: ignore[list-item]


def test_a_queued_command_without_content_is_skipped():
    queued = SimpleNamespace(additional_kwargs={"attachment": {"type": "queued_command"}})
    assert yc.build_transcript_entries([queued]) == []  # type: ignore[list-item]


def test_a_queued_command_does_not_end_the_transcript():
    queued = HumanMessage(content="note", additional_kwargs={"attachment": {"type": "queued_command"}})
    entries = yc.build_transcript_entries([queued, HumanMessage(content="go")])
    assert entries == [
        yc.TranscriptEntry("user", (yc.TranscriptBlock("text", text="note"),)),
        yc.TranscriptEntry("user", (yc.TranscriptBlock("text", text="go"),)),
    ]


def test_a_tool_call_without_a_name_has_an_empty_name():
    ai = SimpleNamespace(type="ai", tool_calls=[{"args": {"a": 1}}])
    assert yc.build_transcript_entries([ai]) == [  # type: ignore[list-item]
        yc.TranscriptEntry("assistant", (yc.TranscriptBlock("tool_use", name="", input={"a": 1}),))
    ]


def test_a_tool_without_a_name_has_no_classifier_input():
    assert yc.to_auto_classifier_input(SimpleNamespace(), {}) == ""


# ── helpers ───────────────────────────────────────────────────────────────────

def test_elapsed_ms_is_milliseconds_since_start(monkeypatch):
    monkeypatch.setattr(yc, "time", SimpleNamespace(monotonic=lambda: 3.5))
    assert yc._elapsed_ms(1.0) == 2500


def test_is_aborted_reads_either_flag():
    assert yc._is_aborted(SimpleNamespace(cancelled=False)) is False
    assert yc._is_aborted(SimpleNamespace(aborted=False)) is False
    assert yc._is_aborted(SimpleNamespace(cancelled=True)) is True


# ── classify_yolo_action ─────────────────────────────────────────────────────

def test_blocks_skip_empty_projections_and_keep_the_rest():
    ai = AIMessage(content="", tool_calls=[
        {"name": "RunTests", "args": {}, "id": "1"}, {"name": "Bash", "args": {"command": "echo"}, "id": "2"}])
    caller = Caller(yc.ClassifierReply(tool_uses=(
        {"name": "classify_result", "input": {"thinking": "t", "shouldBlock": False, "reason": "ok"}},)))
    result: yc.YoloClassifierResult = asyncio.run(yc.classify_yolo_action(
        [ai, HumanMessage(content="a"), HumanMessage(content="bb")],
        yc.format_action_for_classifier("Bash", {"command": "ls"}), TOOLS, ToolPermissionContext(),
        SimpleNamespace(aborted=False), model_caller=caller, main_loop_model="m",
    ))
    assert caller.requests[0].messages[-1].content == ("Bash echo\n", "User: a\n", "User: bb\n", "Bash ls\n")
    assert result.prompt_lengths is not None
    assert result.prompt_lengths.user_prompts == len("User: a\n") + len("User: bb\n")
    assert result.prompt_lengths.tool_calls == len("Bash ls\n") + len("Bash echo\n")


# ── the XML path ─────────────────────────────────────────────────────────────

def test_xml_requests_carry_model_system_signal_and_role(monkeypatch):
    signal = SimpleNamespace(aborted=False)
    caller = Caller(xml("<block>yes"), xml("<block>no</block>"))
    result = classify(True, caller, signal)
    system = yc.replace_output_format_with_xml(yc.build_yolo_system_prompt(ToolPermissionContext()))
    assert result.model == "m"
    for request in caller.requests:
        assert request.model == "m"
        assert request.system == system
        assert request.signal is signal
        assert request.messages[-1].role == "user"


def test_xml_durations_add_up(monkeypatch):
    monkeypatch.setattr(yc, "time", Clock())
    result = classify(True, Caller(xml("<block>yes"), xml("<block>no</block>")))
    assert (result.stage1_duration_ms, result.stage2_duration_ms, result.duration_ms) == (1000, 1000, 2000)


def test_thinking_only_has_no_stage_one_ids():
    result = classify("thinking", Caller(xml("<block>no</block>")))
    assert result.stage1_request_id is None and result.stage1_msg_id is None


def test_an_aborted_xml_call_blocks_with_the_model():
    signal = SimpleNamespace(aborted=False)
    result = classify(True, Caller(RuntimeError("x"), abort=signal), signal)
    assert (result.should_block, result.model, result.unavailable) == (True, "m", True)


def test_xml_errors_block_with_model_duration_and_stage_one_ids(monkeypatch):
    monkeypatch.setattr(yc, "time", Clock())
    unavailable = classify(True, Caller(RuntimeError("boom")))
    assert (unavailable.should_block, unavailable.model, unavailable.duration_ms) == (True, "m", 2000)
    after_stage1 = classify(True, Caller(xml("<block>yes", rid="r1", mid="m1"), RuntimeError("boom")))
    assert (after_stage1.should_block, after_stage1.stage1_msg_id, after_stage1.stage1_request_id) == (
        True, "m1", "r1")


# ── the production caller, against a fake model factory ──────────────────────

class _Reply:
    def __init__(self, content: Any, tool_calls: Any = None, usage: Any = None, msg_id: Any = "id-1") -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.usage_metadata = usage
        if msg_id is not None:
            self.id = msg_id


def _fake_factory(monkeypatch: Any, reply: _Reply) -> list[Any]:
    import app.utils.chat_model_factory as factory

    seen: list[Any] = []

    class Chat:
        def bind_tools(self, tools: Any, tool_choice: Any = None) -> Chat:
            seen.append(("bind_tools", tools, tool_choice))
            return self

        def bind(self, **kwargs: Any) -> Chat:
            seen.append(("bind", kwargs))
            return self

        async def ainvoke(self, messages: Any) -> _Reply:
            seen.append(("invoke", messages))
            return reply

    def get_chat_model(provider: Any, **kwargs: Any) -> Chat:
        seen.append(("model", provider, kwargs))
        return Chat()

    monkeypatch.setattr(factory, "get_chat_model", get_chat_model)
    return seen


def _request(**kw: Any) -> yc.ClassifierRequest:
    defaults: dict[str, Any] = {
        "model": "mx", "system": "SYS", "max_tokens": 9,
        "messages": (yc.ClassifierMessage("user", ("a", "b")), yc.ClassifierMessage("user", ("c",))),
    }
    defaults.update(kw)
    return yc.ClassifierRequest(**defaults)


def test_production_caller_binds_tools_and_stops_and_reads_the_reply(monkeypatch):
    from app.config.config import Config

    reply = _Reply(
        [{"type": "text", "text": "x"}, {"type": "tool_use", "text": "no"}, "raw", {"type": "text", "text": "y"}],
        tool_calls=[{"name": "classify_result", "args": {"shouldBlock": True}, "id": "t"}],
        usage={"input_tokens": 7, "output_tokens": 3},
    )
    seen = _fake_factory(monkeypatch, reply)
    request = _request(tools=(yc.YOLO_CLASSIFIER_TOOL_SCHEMA,), tool_choice="classify_result",
                       stop_sequences=("</block>",))
    out = asyncio.run(yc.build_production_classifier_caller()(request))
    assert seen[0] == ("model", Config.CHAT_MODEL, {"model": "mx"})
    assert seen[1] == ("bind_tools", [{
        "name": "classify_result",
        "description": yc.YOLO_CLASSIFIER_TOOL_SCHEMA["description"],
        "parameters": yc.YOLO_CLASSIFIER_TOOL_SCHEMA["input_schema"],
    }], "classify_result")
    assert seen[2] == ("bind", {"stop": ["</block>"]})
    kind, messages = seen[3]
    assert kind == "invoke" and len(messages) == 3
    assert (messages[0].type, messages[0].content) == ("system", "SYS")
    assert [(m.type, m.content) for m in messages[1:]] == [("human", "ab"), ("human", "c")]
    assert out == yc.ClassifierReply(
        text="xy",
        tool_uses=({"name": "classify_result", "input": {"shouldBlock": True}},),
        usage=yc.ClassifierUsage(input_tokens=7, output_tokens=3),
        message_id="id-1",
    )


def test_production_caller_without_tools_or_stops(monkeypatch):
    seen = _fake_factory(monkeypatch, _Reply("plain", tool_calls=None, usage=None, msg_id=None))
    out = asyncio.run(yc.build_production_classifier_caller("anthropic")(_request()))
    assert seen[0] == ("model", "anthropic", {"model": "mx"})
    assert [s[0] for s in seen] == ["model", "invoke"]
    assert out == yc.ClassifierReply(text="plain", tool_uses=(), usage=yc.ClassifierUsage(0, 0), message_id=None)


def test_production_caller_defaults_missing_usage_fields(monkeypatch):
    _fake_factory(monkeypatch, _Reply("t", usage={"output_tokens": 4}))
    out = asyncio.run(yc.build_production_classifier_caller()(_request()))
    assert out.usage == yc.ClassifierUsage(input_tokens=0, output_tokens=4)
    _fake_factory(monkeypatch, _Reply("t", usage={"input_tokens": 5}))
    out = asyncio.run(yc.build_production_classifier_caller()(_request()))
    assert out.usage == yc.ClassifierUsage(input_tokens=5, output_tokens=0)


def test_production_caller_tolerates_a_bare_reply(monkeypatch):
    """A text block without `text` adds nothing; a reply without `usage_metadata` is zero usage."""
    bare = SimpleNamespace(content=[{"type": "text"}, {"type": "text", "text": "z"}], tool_calls=None)
    _fake_factory(monkeypatch, bare)  # type: ignore[arg-type]
    out = asyncio.run(yc.build_production_classifier_caller()(_request()))
    assert out == yc.ClassifierReply(text="z", usage=yc.ClassifierUsage(0, 0), message_id=None)
