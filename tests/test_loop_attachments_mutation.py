"""
Phase 5 pre-push gate: the `app/utils/attachments.py` behaviours mutation testing found unpinned.

Each test pins one upstream behaviour the earlier suites reached only through
`get_attachments`, whose `maybe` wrapper swallows a raising source, or did not reach at all:
the defensive `getattr` defaults, the exact wording joins, the scan-and-break loops of the
reminder counters, the include boundary of nested memory, and the snippet truncation.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import app.utils.attachments as dates_mod
import app.utils.attachments as pipeline
from app.state.app_state_store import AppState, AppStateStore
from app.tdd import tdd_state_attachment
from app.tdd.ledger import PhaseLedger, TestRunRecord
from app.tdd.tdd_state_attachment import create_tdd_state_attachment_message
from app.tool import tool_context_for
from app.types.permissions import (
    PermissionBehavior,
    PermissionRule,
    ToolPermissionContext,
)
from app.utils.attachments import (
    DIFF_SNIPPET_MAX_BYTES,
    Attachment,
    NestedMemoryState,
    _has_tool,
    _matches,
    agent_definitions_for,
    attachment_of,
    count_auto_mode_attachments_since_last_exit,
    create_attachment_messages,
    effective_context_window,
    get_agent_listing_delta_attachment,
    get_async_hook_response_attachments,
    get_auto_mode_attachment_turn_count,
    get_changed_files,
    get_directories_to_process,
    get_mcp_instructions_delta_attachment,
    get_nested_memory_attachments,
    get_nested_memory_source,
    get_queued_command_attachments,
    get_snippet_for_two_file_diff,
    get_todo_reminder_turn_counts,
    get_token_usage_attachment,
    get_tools_description,
    is_thinking_message,
    mcp_clients_for,
    normalize_attachment_for_api,
    process_conditioned_md_rules,
    token_count_from_last_api_response,
    token_count_from_usage,
    wrap_command_text,
)
from app.utils.claudemd import HostSource
from app.utils.file_state_cache import FileState
from app.utils.tool_result_storage import PREVIEW_SIZE_BYTES
from app.workspace.base import WorkspaceError
from tests.conftest import FakeWorkspace


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def att(kind: str, **data: Any) -> Any:
    return create_attachment_messages(Attachment(kind, data))[0]


def raw_att(kind: str) -> Any:
    """A message carrying an attachment record, whatever its rendered text."""
    return HumanMessage(content="r", additional_kwargs={"attachment": {"type": kind}})


@pytest.fixture(autouse=True)
def _fresh_dates():
    dates_mod.reset_last_emitted_dates()
    yield
    dates_mod.reset_last_emitted_dates()


# ── nested_memory ────────────────────────────────────────────────────────────

def ws_ctx(files: dict[str, str], **kw: Any) -> Any:
    return tool_context_for(AppStateStore(), workspace=FakeWorkspace(kind="local", files=files), **kw)


def nested(files: dict[str, str], triggers: set[str], tmp_path: Path, **kw: Any) -> tuple[list[Any], Any]:
    context = ws_ctx(files)
    got = run(get_nested_memory_attachments(context, NestedMemoryState(triggers=set(triggers)),
                                            home=str(tmp_path / "home"), managed_dir=str(tmp_path / "etc"), **kw))
    return got, context


def host_file(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return str(path)


def test_the_pipeline_source_reads_and_updates_the_live_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    context = ws_ctx({"src/TDDAGENTS.md": "src rules"})
    triggers = context.nested_memory_attachment_triggers
    loaded = context.loaded_nested_memory_paths
    triggers.add("src/a.py")
    got = run(get_nested_memory_source([], context))
    assert [a.data["path"] for a in got] == ["src/TDDAGENTS.md"]
    assert context.nested_memory_attachment_triggers is triggers and triggers == set()
    assert context.loaded_nested_memory_paths is loaded and loaded == {"src/TDDAGENTS.md"}


def test_the_pipeline_source_tolerates_contexts_without_its_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert run(get_nested_memory_source([], SimpleNamespace())) == []
    bare = SimpleNamespace(nested_memory_attachment_triggers={"src/a.py"}, read_file_state={},
                           workspace=FakeWorkspace(kind="local", files={"src/TDDAGENTS.md": "r"}))
    assert [a.data["path"] for a in run(get_nested_memory_source([], bare))] == ["src/TDDAGENTS.md"]
    assert run(get_nested_memory_attachments(SimpleNamespace(), NestedMemoryState())) == []


def test_user_rules_default_to_the_home_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rule = host_file(tmp_path / ".tddagents" / "rules" / "py.md", "---\npaths: '**/*.py'\n---\nhome rule")
    context = ws_ctx({})
    context.nested_memory_attachment_triggers.add("src/a.py")
    got = run(get_nested_memory_source([], context))
    assert [(a.data["path"], a.data["content"]) for a in got] == [(rule, "home rule")]


def test_only_the_rules_subdirectory_holds_host_conditional_rules(tmp_path, monkeypatch):
    """A `paths:` file elsewhere under the config dir (the user's own TDDAGENTS.md) is not a rule."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    text = "---\npaths: '**/*.py'\n---\nnot a rule"
    host_file(tmp_path / "home" / ".tddagents" / "TDDAGENTS.md", text)
    host_file(tmp_path / "etc" / ".tddagents" / "TDDAGENTS.md", text)
    context = ws_ctx({})
    state = NestedMemoryState(triggers={"src/a.py"})
    assert run(get_nested_memory_attachments(context, state, managed_dir=str(tmp_path / "etc"))) == []


def test_managed_rules_come_first_and_exclude_external_includes(tmp_path):
    inc = host_file(tmp_path / "inc" / "m_inc.md", "---\npaths: '**/*.py'\n---\nmanaged include")
    managed = host_file(tmp_path / "etc" / ".tddagents" / "rules" / "m.md",
                        f"---\npaths: 'src/*.py'\n---\nmanaged rule\n@{inc}")
    user_inc = host_file(tmp_path / "inc" / "u_inc.md", "---\npaths: '**/*.py'\n---\nuser include")
    user = host_file(tmp_path / "home" / ".tddagents" / "rules" / "u.md",
                     f"---\npaths: '**/*.py'\n---\nuser rule\n@{user_inc}")
    got, _ = nested({}, {"src/a.py"}, tmp_path)
    assert [a.data["path"] for a in got] == [managed, user, user_inc]


def test_every_trigger_contributes_and_a_bad_one_is_skipped(tmp_path):
    files = {"a/TDDAGENTS.md": "a rules", "b/TDDAGENTS.md": "b rules"}
    got, _ = nested(files, {"../x.py", "a/x.py", "b/y.py"}, tmp_path)
    assert [a.data["path"] for a in got] == ["a/TDDAGENTS.md", "b/TDDAGENTS.md"]


def test_every_host_rule_list_accumulates_across_triggers(tmp_path):
    rule = host_file(tmp_path / "home" / ".tddagents" / "rules" / "a.md", "---\npaths: 'a/*.py'\n---\nfor a")
    got, _ = nested({"b/TDDAGENTS.md": "b rules"}, {"a/x.py", "b/y.py"}, tmp_path)
    assert [a.data["path"] for a in got] == [rule, "b/TDDAGENTS.md"]


@pytest.mark.parametrize("site, conditional", [
    ("pkg/TDDAGENTS.md", False),
    ("pkg/.tddagents/TDDAGENTS.md", False),
    ("pkg/TDDAGENTS.local.md", False),
    ("pkg/.tddagents/rules/u.md", False),
    ("pkg/.tddagents/rules/c.md", True),
    (".tddagents/rules/c.md", True),
])
def test_workspace_memory_never_pulls_in_a_host_include(tmp_path, site, conditional):
    if conditional:
        inc = host_file(tmp_path / "inc.md", "---\npaths: '**/*.py'\n---\nhost include")
        text = f"---\npaths: '**/*.py'\n---\nworkspace\n@{inc}"
    else:
        inc = host_file(tmp_path / "inc.md", "host include")
        text = f"workspace\n@{inc}"
    got, _ = nested({site: text}, {"pkg/m.py"}, tmp_path)
    assert [a.data["path"] for a in got] == [site]


def test_nested_conditional_rules_match_relative_to_their_directory(tmp_path):
    files = {"pkg/.tddagents/rules/r.md": "---\npaths: 'sub/m.py'\n---\nanchored"}
    got, _ = nested(files, {"pkg/sub/m.py"}, tmp_path)
    assert [a.data["path"] for a in got] == ["pkg/.tddagents/rules/r.md"]


def test_attachment_records_and_the_read_state_they_leave(tmp_path):
    files = {"pkg/TDDAGENTS.md": "seen", "pkg/TDDAGENTS.local.md": "kept\n\n<!-- hidden -->\n"}
    context = ws_ctx(files)
    context.read_file_state["pkg/TDDAGENTS.md"] = FileState(content="seen")
    got = run(get_nested_memory_attachments(context, NestedMemoryState(triggers={"pkg/m.py"}),
                                            home=str(tmp_path), managed_dir=str(tmp_path / "etc")))
    (only,) = got
    assert only.data == {"path": "pkg/TDDAGENTS.local.md", "content": only.data["content"],
                         "displayPath": "pkg/TDDAGENTS.local.md"}
    assert "hidden" not in only.data["content"]
    assert context.read_file_state["pkg/TDDAGENTS.local.md"] == FileState(
        content="kept\n\n<!-- hidden -->\n", is_partial_view=True)


def test_directory_walk_stops_at_the_root():
    assert get_directories_to_process("./a.py") == ([], [""])


def test_glob_matching_is_relative_to_the_rules_base():
    assert _matches(None, "a.py", "") is False
    assert _matches(("*.py",), "other/a.py", "pkg") is False
    assert _matches(("sub/a.py",), "pkg/sub/a.py", "pkg") is True
    assert _matches(("sub/a.py",), "pkg/xsub/a.py", "pk") is False
    assert _matches(("*.py",), "../x.py", "") is False


def test_conditioned_rules_honour_the_base_dir(tmp_path):
    rules = tmp_path / "rules"
    host_file(rules / "r.md", "---\npaths: 'sub/*.py'\n---\nr")
    got = process_conditioned_md_rules(HostSource(), "pkg/sub/a.py", str(rules), "User", set(), True, "pkg")
    assert [f.content for f in got] == ["r"]
    assert process_conditioned_md_rules(HostSource(), "pkg/sub/a.py", str(rules), "User", set(), True, "") == []


# ── pipeline ─────────────────────────────────────────────────────────────────

SOURCE_LABELS = [
    ("queued_commands", "get_queued_command_attachments"),
    ("date_change", "get_date_change_attachments"),
    ("agent_listing_delta", "get_agent_listing_delta_attachment"),
    ("mcp_instructions_delta", "get_mcp_instructions_delta_attachment"),
    ("changed_files", "get_changed_files"),
    ("nested_memory", "get_nested_memory_source"),
    ("auto_mode", "get_auto_mode_attachments"),
    ("todo_reminders", "get_todo_reminder_attachments"),
    ("tdd_state", "get_tdd_state_attachments"),
    ("async_hook_responses", "get_async_hook_response_attachments"),
    ("token_usage", "get_token_usage_attachment"),
]


def _boom(*a: Any, **k: Any) -> Any:
    raise RuntimeError("source broke")


def _logged(caplog: Any) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "TDDOrchestrator.Attachments"]


def test_a_failing_source_is_logged_under_its_own_label(monkeypatch, caplog):
    for _, name in SOURCE_LABELS:
        # TDDAgents' own source lives in app/tdd/ and is imported by `get_attachments` itself.
        monkeypatch.setattr(tdd_state_attachment if name == "get_tdd_state_attachments" else pipeline, name, _boom)
    caplog.set_level(logging.ERROR)
    assert run(pipeline.get_attachments(tool_context_for(AppStateStore()), [])) == []
    assert _logged(caplog) == [f"Attachment error in {label}" for label, _ in SOURCE_LABELS]


@pytest.mark.parametrize("env", ["TDDAGENTS_DISABLE_ATTACHMENTS", "TDDAGENTS_SIMPLE"])
def test_disabled_attachments_run_only_the_queued_source(monkeypatch, caplog, env):
    seen: list[Any] = []

    def queued(messages: Any, context: Any) -> Any:
        seen.append(messages)
        raise RuntimeError("queued broke")

    monkeypatch.setattr(pipeline, "get_queued_command_attachments", queued)
    monkeypatch.setenv(env, "true")
    caplog.set_level(logging.ERROR)
    history = [HumanMessage(content="h")]
    assert run(pipeline.get_attachments(tool_context_for(AppStateStore()), history)) == []
    assert seen == [history]
    assert _logged(caplog) == ["Attachment error in queued_commands"]


def test_a_context_without_an_agent_id_is_the_main_thread():
    out = run(pipeline.get_attachments(SimpleNamespace(), []))
    assert out == []


def test_attachment_messages_pass_the_user_input_through(monkeypatch):
    seen: dict[str, Any] = {}

    async def fake(context: Any, messages: Any, *, user_input: Any = "unset") -> list[Attachment]:
        seen["user_input"] = user_input
        return []

    monkeypatch.setattr(pipeline, "get_attachments", fake)
    run(pipeline.get_attachment_messages(None, [], user_input="prompt"))
    assert seen == {"user_input": "prompt"}


# ── changed_files ────────────────────────────────────────────────────────────

class FlakyWorkspace(FakeWorkspace):
    """Raises a non-not-found workspace error on one path."""

    def __init__(self, files: dict[str, str], broken: str, **kw: Any) -> None:
        super().__init__(kind="local", files=files, **kw)
        self.broken = broken

    def read_file(self, path: str) -> str:
        if path == self.broken:
            raise WorkspaceError("transient")
        return super().read_file(path)


def changed(ws: Any, seen: dict[str, FileState], state: AppState | None = None) -> tuple[list[Any], Any]:
    context = tool_context_for(AppStateStore(state=state or AppState()), workspace=ws)
    context.read_file_state.update(seen)
    return get_changed_files([], context), context


def test_skipped_files_do_not_stop_the_scan():
    files = {"part.py": "new\n", "empty.py": "", "late.py": "b\n"}
    seen = {
        "part.py": FileState(content="old\n", offset=1),
        "gone.py": FileState(content="x\n"),
        "empty.py": FileState(content="x\n"),
        "late.py": FileState(content="a\n"),
    }
    got, context = changed(FakeWorkspace(kind="local", files=files), seen)
    assert [(a.data["filename"], a.data["snippet"]) for a in got] == [("late.py", "1\tb")]
    assert context.read_file_state["empty.py"] == FileState(content="")
    assert context.read_file_state["part.py"] == FileState(content="old\n", offset=1)


def test_an_unreadable_file_does_not_stop_the_scan():
    ws = FlakyWorkspace({"bad.py": "x", "late.py": "b\n"}, broken="bad.py")
    got, _ = changed(ws, {"bad.py": FileState(content="y"), "late.py": FileState(content="a\n")})
    assert [a.data["filename"] for a in got] == ["late.py"]


def test_read_denied_files_are_skipped():
    deny = PermissionRule(tool_name="Read", rule_behavior=PermissionBehavior.DENY, rule_content="secret.py")
    state = AppState(tool_permission_context=ToolPermissionContext(always_deny_rules=(deny,)))
    ws = FakeWorkspace(kind="local", files={"secret.py": "b\n", "open.py": "b\n"})
    seen = {"secret.py": FileState(content="a\n"), "open.py": FileState(content="a\n")}
    got, _ = changed(ws, seen, state)
    assert [a.data["filename"] for a in got] == ["open.py"]


def test_read_denial_is_resolved_against_the_workspace_root():
    deny = PermissionRule(tool_name="Read", rule_behavior=PermissionBehavior.DENY, rule_content="//proj/secret.py")
    state = AppState(tool_permission_context=ToolPermissionContext(always_deny_rules=(deny,)))
    ws = FakeWorkspace(kind="local", files={"secret.py": "b\n"})
    ws.root = "/proj"  # type: ignore[attr-defined]
    got, _ = changed(ws, {"secret.py": FileState(content="a\n")}, state)
    assert got == []


def test_a_permission_lookup_that_fails_denies_nothing():
    class Exploding(FakeWorkspace):
        @property
        def root(self) -> str:
            raise RuntimeError("no root")

    got, _ = changed(Exploding(kind="local", files={"a.py": "b\n"}), {"a.py": FileState(content="a\n")})
    assert [a.data["filename"] for a in got] == ["a.py"]


def test_changed_files_needs_both_a_workspace_and_a_read_state():
    ws = FakeWorkspace(kind="local", files={})
    assert get_changed_files([], SimpleNamespace(read_file_state={"a.py": FileState(content="x")})) == []
    assert get_changed_files([], SimpleNamespace(workspace=ws)) == []
    assert get_changed_files([], SimpleNamespace(workspace=None, read_file_state={"a": FileState(content="x")})) \
        == []


def test_snippet_joins_hunks_with_the_upstream_separator():
    before = "\n".join(f"line {i}" for i in range(1, 41))
    after = before.replace("line 2\n", "line 2 changed\n").replace("line 39", "line 39 changed")
    head = "\n".join(["1\tline 1", "2\tline 2 changed", *(f"{i}\tline {i}" for i in range(3, 11))])
    tail = "\n".join([*(f"{i}\tline {i}" for i in range(31, 39)), "39\tline 39 changed", "40\tline 40"])
    assert get_snippet_for_two_file_diff(before, after) == head + "\n...\n" + tail


def test_a_pure_deletion_has_no_snippet():
    assert get_snippet_for_two_file_diff("x", "") == ""


def test_snippet_matching_never_treats_popular_lines_as_junk():
    before = "\n".join(["x"] * 300)
    after = "\n".join(["x"] * 150 + ["y"] + ["x"] * 150)
    assert get_snippet_for_two_file_diff(before, after) == "\n".join(
        [*(f"{i}\tx" for i in range(143, 151)), "151\ty", *(f"{i}\tx" for i in range(152, 160))]
    )


def test_a_snippet_of_exactly_the_cap_is_kept_whole():
    first = "a" * (DIFF_SNIPPET_MAX_BYTES - 2)
    snippet = get_snippet_for_two_file_diff("", first)
    assert snippet == f"1\t{first}" and len(snippet) == DIFF_SNIPPET_MAX_BYTES


def test_truncation_keeps_whole_lines_up_to_the_cap():
    lines = [f"{i:03d}" + "x" * 96 for i in range(200)]
    snippet = get_snippet_for_two_file_diff("", "\n".join(lines))
    numbered = [f"{i + 1}\t{line}" for i, line in enumerate(lines)]
    kept, marker = snippet.split("\n\n... [")
    k = len(kept.split("\n"))
    assert kept == "\n".join(numbered[:k])
    assert len("\n".join(numbered[:k + 1])) > DIFF_SNIPPET_MAX_BYTES
    assert marker == f"{len(numbered) - k + 1} lines truncated] ..."


def test_truncation_cuts_at_a_newline_sitting_exactly_on_the_cap():
    """`full.lastIndexOf('\\n', DIFF_SNIPPET_MAX_BYTES)` includes that index (upstream)."""
    second = "a" * (DIFF_SNIPPET_MAX_BYTES - 6)
    snippet = get_snippet_for_two_file_diff("", f"A\n{second}\nb")
    assert snippet == f"1\tA\n2\t{second}\n\n... [2 lines truncated] ..."
    past = "a" * (DIFF_SNIPPET_MAX_BYTES - 5)
    assert get_snippet_for_two_file_diff("", f"A\n{past}\nb") == "1\tA\n\n... [3 lines truncated] ..."


# ── reminders ────────────────────────────────────────────────────────────────

def ai(text: str = "t") -> AIMessage:
    return AIMessage(content=text)


TODO_WRITE = AIMessage(content="", tool_calls=[{"id": "w", "name": "TodoWrite", "args": {}}])
THINKING = AIMessage(content=[{"type": "thinking", "thinking": "hm"}])


def test_is_thinking_message_needs_only_thinking_blocks():
    assert is_thinking_message(AIMessage(content=[{"type": "redacted_thinking", "data": "x"}])) is True
    assert is_thinking_message(THINKING) is True
    assert is_thinking_message(AIMessage(content=[])) is False
    assert is_thinking_message(AIMessage(content=["plain"])) is False
    assert is_thinking_message(AIMessage(content=[{"type": "text", "text": "x"}])) is False
    assert is_thinking_message(object()) is False


@pytest.mark.parametrize("history, counts", [
    ([ai(), THINKING, ai()], (2, 2)),
    ([ai(), HumanMessage(content="h"), ai(), ai()], (3, 3)),
    ([ai(), ai(), TODO_WRITE, ai()], (1, 4)),
    ([ai(), raw_att("todo_reminder"), ai()], (2, 1)),
    ([ai(), TODO_WRITE, raw_att("todo_reminder"), ai()], (1, 1)),
    ([object()], (0, 0)),
])
def test_todo_reminder_turn_counts(history, counts):
    assert get_todo_reminder_turn_counts(history) == counts


def test_has_tool_by_name_or_alias_tolerating_bare_objects():
    assert _has_tool(SimpleNamespace(tools=(SimpleNamespace(aliases=("TodoWrite",)),)), "TodoWrite") is True
    assert _has_tool(SimpleNamespace(tools=(SimpleNamespace(name="Other"),)), "TodoWrite") is False
    assert _has_tool(SimpleNamespace(tools=(SimpleNamespace(name="Other", aliases=()),)), "TodoWrite") is False
    assert _has_tool(SimpleNamespace(), "TodoWrite") is False


@pytest.mark.parametrize("history, expected", [
    ([HumanMessage(content="h")], (1, False)),
    ([HumanMessage(content="a"), HumanMessage(content="b")], (2, False)),
    ([raw_att("auto_mode"), raw_att("auto_mode_exit"), HumanMessage(content="h")], (1, False)),
    ([raw_att("auto_mode"), HumanMessage(content="h")], (1, True)),
    ([object(), SimpleNamespace(type="human")], (1, False)),
])
def test_auto_mode_turn_count(history, expected):
    assert get_auto_mode_attachment_turn_count(history) == expected


@pytest.mark.parametrize("history, expected", [
    ([raw_att("auto_mode")], 1),
    ([raw_att("auto_mode"), raw_att("auto_mode")], 2),
    ([raw_att("auto_mode"), raw_att("auto_mode_exit"), raw_att("auto_mode")], 1),
])
def test_auto_mode_count_since_last_exit(history, expected):
    assert count_auto_mode_attachments_since_last_exit(history) == expected


# ── usage ────────────────────────────────────────────────────────────────────

def test_token_count_treats_missing_fields_as_zero():
    assert token_count_from_usage({}) == 0
    assert token_count_from_usage({"input_tokens": 1, "output_tokens": 2, "input_token_details": {}}) == 3


def test_last_api_response_tolerates_bare_objects():
    bare: list[Any] = [object(), SimpleNamespace(type="ai")]
    assert token_count_from_last_api_response(bare) == 0


def test_effective_context_window_is_the_token_modules():
    from app.config.config import Config
    from app.utils import tokens

    assert effective_context_window(Config.MODEL) == tokens.get_effective_context_window_size(Config.MODEL)


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " TRUE "])
def test_token_usage_accepts_every_truthy_spelling(monkeypatch, value):
    import app.utils.attachments as usage
    from app.config.config import Config

    seen: list[str] = []

    def window(model: str) -> int:
        seen.append(model)
        return 100

    monkeypatch.setenv("TDDAGENTS_ENABLE_TOKEN_USAGE_ATTACHMENT", value)
    monkeypatch.setattr(usage, "effective_context_window", window)
    expected = Attachment("token_usage", {"used": 0, "total": 100, "remaining": 100})
    assert get_token_usage_attachment([], None) == [expected]
    assert seen == [Config.MODEL]


def test_token_usage_is_off_when_unset(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_ENABLE_TOKEN_USAGE_ATTACHMENT", raising=False)
    assert get_token_usage_attachment([], None) == []


# ── tdd_state ────────────────────────────────────────────────────────────────

def test_post_compaction_text_is_exact_and_tails_by_bytes():
    stdout = "é" * PREVIEW_SIZE_BYTES + "END"
    record = TestRunRecord(test_path="t.py", exit_code=1, stdout=stdout, stderr="boom")
    msg = create_tdd_state_attachment_message(PhaseLedger(), record, after_compaction=True)
    full = f"{stdout}\n--- STDERR ---\nboom".encode()
    tail = full[-PREVIEW_SIZE_BYTES:].decode("utf-8", errors="ignore")
    assert msg.content == (
        "<system-reminder>\nTDD phase ledger (written only by RunTests from observed test runs):\n"
        "Current Phase: RED\nTests Written: False\nGreen Passed: False\nRefactor Passed: False\n"
        "Coverage: not measured\n\n"
        "Last RunTests run (recorded by RunTests, not by the model): t.py exited 1\n"
        f"{tail}\n</system-reminder>"
    )
    assert msg.additional_kwargs["is_meta"] is True


def test_post_compaction_without_a_run_is_exact():
    msg = create_tdd_state_attachment_message(PhaseLedger(), None, after_compaction=True)
    assert msg.content == (
        "<system-reminder>\nTDD phase ledger (written only by RunTests from observed test runs):\n"
        "Current Phase: RED\nTests Written: False\nGreen Passed: False\nRefactor Passed: False\n"
        "Coverage: not measured\n\n"
        "No RunTests run has been observed yet.\n</system-reminder>"
    )


# ── types ────────────────────────────────────────────────────────────────────

def test_listings_join_each_item_on_its_own_line():
    listing = Attachment("agent_listing_delta", {
        "addedLines": ["- a: x", "- b: y"], "removedTypes": ["c", "d"], "isInitial": False,
    })
    assert normalize_attachment_for_api(listing) == [
        ("New agent types are now available for the Agent tool:\n- a: x\n- b: y\n\n"
         "The following agent types are no longer available:\n- c\n- d")
    ]
    mcp = Attachment("mcp_instructions_delta", {"addedBlocks": ["## a\n1", "## b\n2"], "removedNames": ["c", "d"]})
    assert normalize_attachment_for_api(mcp) == [
        ("# MCP Server Instructions\n\nThe following MCP servers have provided instructions for how to use "
         "their tools and resources:\n\n## a\n1\n\n## b\n2\n\nThe following MCP servers have disconnected. Their "
         "instructions above no longer apply:\nc\nd")
    ]


def test_async_hook_response_ignores_non_text_fields():
    response = {"systemMessage": 5, "hookSpecificOutput": {"additionalContext": ["x"]}}
    assert normalize_attachment_for_api(Attachment("async_hook_response", {"response": response})) == []


def test_a_queued_command_without_a_prompt_is_empty():
    assert normalize_attachment_for_api(Attachment("queued_command", {})) == [wrap_command_text("", None)]


def test_attachment_of_a_bare_object_is_none():
    assert attachment_of(object()) is None


# ── dates ────────────────────────────────────────────────────────────────────

def test_the_override_env_sets_the_date(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_OVERRIDE_DATE", "2030-01-02")
    assert dates_mod.get_local_iso_date() == "2030-01-02"


def _date(monkeypatch: Any, value: str) -> None:
    monkeypatch.setattr(dates_mod, "get_local_iso_date", lambda: value)


def test_every_new_day_is_announced(monkeypatch):
    ctx = SimpleNamespace(run_id="r")
    for day, expected in [("d1", []), ("d2", ["d2"]), ("d3", ["d3"])]:
        _date(monkeypatch, day)
        assert [a.data["newDate"] for a in dates_mod.get_date_change_attachments([], ctx)] == expected


def test_contexts_without_a_run_id_share_the_empty_key(monkeypatch):
    _date(monkeypatch, "d1")
    assert dates_mod.get_date_change_attachments([], SimpleNamespace()) == []
    _date(monkeypatch, "d2")
    assert dates_mod.get_date_change_attachments([], SimpleNamespace(run_id="")) == [
        Attachment("date_change", {"newDate": "d2"})]


def test_an_empty_run_id_is_its_own_key(monkeypatch):
    _date(monkeypatch, "d1")
    dates_mod.get_date_change_attachments([], SimpleNamespace(run_id="XXXX"))
    _date(monkeypatch, "d2")
    assert dates_mod.get_date_change_attachments([], SimpleNamespace(run_id="")) == []


# ── queued ───────────────────────────────────────────────────────────────────

def test_contexts_without_a_registry_queue_nothing():
    assert get_queued_command_attachments([], SimpleNamespace()) == []
    assert get_queued_command_attachments([], SimpleNamespace(hook_dispatcher=object())) == []


def test_async_hook_responses_drop_the_type_from_the_record():
    from app.utils.hooks.async_hook_registry import AsyncHookRegistry, AsyncHookResponse

    registry = AsyncHookRegistry()
    response = AsyncHookResponse(process_id="p", response={"systemMessage": "hi"}, hook_name="h",
                                 hook_event="PostToolUse", stdout="o", stderr="e", exit_code=0)

    async def responses() -> list[AsyncHookResponse]:
        return [response]

    registry.check_for_responses = responses  # type: ignore[method-assign]
    (got,) = run(get_async_hook_response_attachments([], SimpleNamespace(hook_dispatcher=SimpleNamespace(
        registry=registry))))
    expected = response.attachment()
    expected.pop("type")
    assert got == Attachment("async_hook_response", expected)


# ── deltas ───────────────────────────────────────────────────────────────────

def agent(name: str, **kw: Any) -> Any:
    from app.tools.agent_tool.load_agents_dir import AgentDefinition

    return AgentDefinition(name=name, description=f"{name} agent", prompt="p", **kw)


AGENT_TOOL = SimpleNamespace(name="Agent")


def listing_ctx(*agents: Any) -> Any:
    return SimpleNamespace(tools=(AGENT_TOOL,), agent_definitions=list(agents))


@pytest.fixture
def deltas_on(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_AGENT_LIST_IN_MESSAGES", "TRUE")
    monkeypatch.setenv("TDDAGENTS_MCP_INSTR_DELTA", "On")


def test_deltas_are_off_and_quiet_when_their_env_is_unset(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_AGENT_LIST_IN_MESSAGES", raising=False)
    monkeypatch.delenv("TDDAGENTS_MCP_INSTR_DELTA", raising=False)
    assert get_agent_listing_delta_attachment([], listing_ctx(agent("a"))) == []
    assert get_mcp_instructions_delta_attachment([], SimpleNamespace()) == []


def test_tools_description_joins_and_tolerates_bare_agents():
    assert get_tools_description(SimpleNamespace()) == "All tools"
    assert get_tools_description(SimpleNamespace(tools=("A", "B", "C"), disallowed_tools=("B",))) == "A, C"
    assert get_tools_description(SimpleNamespace(disallowed_tools=("A", "B"))) == "All tools except A, B"


def test_roster_and_clients_are_read_from_the_context():
    a = agent("a")
    client = {"name": "s"}
    assert agent_definitions_for(SimpleNamespace(agent_definitions=[a])) == (a,)
    assert agent_definitions_for(tool_context_for(AppStateStore())) == ()
    assert mcp_clients_for(SimpleNamespace(mcp_clients=[client])) == (client,)
    assert mcp_clients_for(tool_context_for(AppStateStore())) == ()


def test_agent_tool_by_alias_and_tolerating_bare_tools(deltas_on):
    a = agent("a")
    by_alias = SimpleNamespace(tools=(SimpleNamespace(aliases=("Agent",)),), agent_definitions=[a])
    assert [x.data["addedTypes"] for x in get_agent_listing_delta_attachment([], by_alias)] == [["a"]]
    other = SimpleNamespace(tools=(SimpleNamespace(name="Other"),), agent_definitions=[a])
    assert get_agent_listing_delta_attachment([], other) == []
    assert get_agent_listing_delta_attachment([], SimpleNamespace(agent_definitions=[a])) == []


def test_agent_listing_is_sorted_and_reads_the_whole_history(deltas_on):
    first = get_agent_listing_delta_attachment([], listing_ctx(agent("b"), agent("a")))
    assert [x.data["addedTypes"] for x in first] == [["a", "b"]]
    history = [HumanMessage(content="h"), att("todo_reminder", content=[]), *create_attachment_messages(first[0])]
    assert get_agent_listing_delta_attachment(history, listing_ctx(agent("b"), agent("a"))) == []


def test_a_removed_agent_that_returns_is_announced_again(deltas_on):
    history = [att("agent_listing_delta", addedTypes=["a"], addedLines=[], removedTypes=[]),
               att("agent_listing_delta", addedTypes=[], addedLines=[], removedTypes=["a"])]
    (again,) = get_agent_listing_delta_attachment(history, listing_ctx(agent("a")))
    assert (again.data["addedTypes"], again.data["isInitial"]) == (["a"], True)


def test_mcp_delta_reads_object_clients_and_the_whole_history(deltas_on):
    clients = [SimpleNamespace(name="s", type="connected", instructions="use s"),
               SimpleNamespace(name="q", type="connected")]
    ctx = SimpleNamespace(mcp_clients=clients)
    (first,) = get_mcp_instructions_delta_attachment([], ctx)
    assert first.data == {"addedNames": ["s"], "addedBlocks": ["## s\nuse s"], "removedNames": []}
    history = [HumanMessage(content="h"), att("todo_reminder", content=[]), *create_attachment_messages(first)]
    assert get_mcp_instructions_delta_attachment(history, ctx) == []
    gone = [*history, *create_attachment_messages(
        Attachment("mcp_instructions_delta", {"addedNames": [], "addedBlocks": [], "removedNames": ["s"]}))]
    (back,) = get_mcp_instructions_delta_attachment(gone, ctx)
    assert back.data["addedNames"] == ["s"]
