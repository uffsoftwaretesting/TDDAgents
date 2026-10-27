"""
Phase 11: the session's TDD plan (`app/tdd/plan.py`) and the `UpdatePlan` tool.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.plan import (
    PLAN_FILE_NAME,
    ItemStatus,
    PlanError,
    PlanItem,
    TddPlan,
    load_plan,
    plan_path,
    render_plan,
    save_plan,
    update_tdd_plan,
)
from app.tdd.tdd_plan_tool.tdd_plan_tool import (
    UPDATE_PLAN_INPUT_SCHEMA,
    UPDATE_PLAN_TOOL_NAME,
    apply_operations,
    build_update_plan_tool,
    describe_change,
)
from app.tool import tool_context_for
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    ToolPermissionContext,
)
from app.utils.permissions.permissions import has_permissions_to_use_tool

CYCLE = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)


def plan(*texts: str) -> TddPlan:
    return TddPlan.from_texts(list(texts))


def done_first(p: TddPlan) -> TddPlan:
    return p.complete_current(CYCLE)


# ── the plan ─────────────────────────────────────────────────────────────────

def test_from_texts_numbers_items_from_one_and_starts_pending():
    p = plan("A", "B")
    assert p.items == (PlanItem("1", "A"), PlanItem("2", "B"))
    assert p.current == PlanItem("1", "A")
    assert (p.is_complete, p.done_count) == (False, 0)


def test_an_empty_plan_has_nothing_left_to_do():
    assert TddPlan().current is None
    assert TddPlan().is_complete is True


def test_only_an_observed_cycle_completes_the_current_item():
    p = plan("A", "B")
    for ledger in (PhaseLedger(), PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True),
                   PhaseLedger(green_passed=True)):
        with pytest.raises(PlanError, match="Sub-requirement 1 has no observed Red -> Green cycle."):
            p.complete_current(ledger)
    after = p.complete_current(CYCLE)
    assert after.items[0].status == ItemStatus.DONE
    assert after.current == PlanItem("2", "B")
    assert after.done_count == 1


def test_completing_a_finished_plan_is_refused():
    p = done_first(plan("A"))
    assert p.is_complete is True
    with pytest.raises(PlanError, match="Every sub-requirement is already done."):
        p.complete_current(CYCLE)


def test_add_appends_or_inserts_at_a_one_based_position_with_a_fresh_id():
    p = plan("A", "B").add("C")
    assert [i.id for i in p.items] == ["1", "2", "3"]
    p = p.add("first", position=1).add("second", position=2).add("far", position=99).add("low", position=0)
    assert [i.text for i in p.items] == ["low", "first", "second", "A", "B", "C", "far"]
    assert [i.id for i in p.items] == ["7", "4", "5", "1", "2", "3", "6"]


def test_ids_never_reuse_a_removed_item_s_number_while_a_higher_one_exists():
    p = plan("A", "B", "C").remove("2").add("D")
    assert [i.id for i in p.items] == ["1", "3", "4"]


def test_non_numeric_ids_are_ignored_when_numbering():
    p = TddPlan(items=(PlanItem("x", "X"),)).add("Y")
    assert p.items[1].id == "1"


def test_text_is_trimmed_and_must_not_be_blank():
    assert plan("  A  ").items[0].text == "A"
    for bad in ("", "   "):
        with pytest.raises(PlanError, match="A sub-requirement needs text."):
            plan("A").add(bad)
        with pytest.raises(PlanError, match="A sub-requirement needs text."):
            plan("A").edit("1", bad)


def test_editing_a_done_item_reopens_it_and_keeps_its_place():
    p = done_first(plan("A", "B")).edit("1", "A, reworded")
    assert p.items == (PlanItem("1", "A, reworded"), PlanItem("2", "B"))
    assert p.current == PlanItem("1", "A, reworded")


def test_editing_a_pending_item_keeps_it_pending():
    assert plan("A", "B").edit("2", "B2").items[1] == PlanItem("2", "B2")


def test_removing_a_done_item_drops_it():
    p = done_first(plan("A", "B")).remove("1")
    assert p.items == (PlanItem("2", "B"),)


def test_move_reorders_to_a_one_based_position():
    p = plan("A", "B", "C")
    assert [i.text for i in p.move("3", 1).items] == ["C", "A", "B"]
    assert [i.text for i in p.move("1", 2).items] == ["B", "A", "C"]
    assert [i.text for i in p.move("1", 99).items] == ["B", "C", "A"]
    assert [i.text for i in p.move("2", -4).items] == ["B", "A", "C"]


def test_an_unknown_id_is_refused_by_every_operation():
    p = plan("A")
    for op in (lambda: p.edit("9", "x"), lambda: p.remove("9"), lambda: p.move("9", 1), lambda: p.get("9")):
        with pytest.raises(PlanError, match="No sub-requirement with id '9'."):
            op()


def test_operations_never_mutate_the_original():
    p = plan("A")
    p.add("B"), p.edit("1", "x"), p.remove("1"), p.complete_current(CYCLE)
    assert p.items == (PlanItem("1", "A"),)


def test_render_marks_done_current_and_pending():
    p = done_first(plan("A", "B", "C"))
    assert render_plan(p) == "✔ 1. A\n▶ 2. B\n· 3. C"
    assert render_plan(TddPlan()) == "(no sub-requirements)"
    assert render_plan(done_first(plan("A"))) == "✔ 1. A"


# ── persistence ──────────────────────────────────────────────────────────────

def test_the_plan_round_trips_through_its_file(tmp_path: Path):
    path = tmp_path / "deep" / "dir" / PLAN_FILE_NAME
    p = done_first(plan("A", "café"))
    save_plan(path, p)
    assert json.loads(path.read_text(encoding="utf-8")) == {"items": [
        {"id": "1", "text": "A", "status": "done"}, {"id": "2", "text": "café", "status": "pending"}]}
    assert "café" in path.read_text(encoding="utf-8")
    assert load_plan(path) == p


def test_a_missing_file_is_no_plan(tmp_path: Path):
    assert load_plan(tmp_path / "absent.json") is None


@pytest.mark.parametrize("content", [
    "not json", "[]", '{"items": 3}', '{"items": [3]}', '{"items": [{"id": "1"}]}',
    '{"items": [{"id": "1", "text": "A", "status": "finished"}]}', '{"items": [{"id": "1", "text": " "}]}',
])
def test_an_unreadable_file_is_logged_and_ignored(tmp_path: Path, content: str, caplog: pytest.LogCaptureFixture):
    path = tmp_path / PLAN_FILE_NAME
    path.write_text(content, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Plan"):
        assert load_plan(path) is None
    assert caplog.messages and caplog.messages[0].startswith(f"Ignoring unreadable plan file {path}: ")


def test_a_missing_status_reads_as_pending():
    assert TddPlan.from_dict({"items": [{"id": 1, "text": "A"}]}).items == (PlanItem("1", "A"),)


def test_plan_path_is_inside_the_session_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    path = plan_path("run-1", "/work/space")
    assert path.name == PLAN_FILE_NAME
    assert path.parent.name == "run-1"
    assert path.parent.parent.parent == tmp_path / "projects"


def test_update_tdd_plan_writes_state_and_file(tmp_path: Path):
    store = AppStateStore(state=AppState(tdd_plan=plan("A")))
    ctx = tool_context_for(store)
    ctx.tdd_plan_path = str(tmp_path / PLAN_FILE_NAME)
    updated = update_tdd_plan(ctx, lambda p: p.add("B"))
    assert store.get().tdd_plan == updated == plan("A", "B")
    assert load_plan(tmp_path / PLAN_FILE_NAME) == updated


def test_update_tdd_plan_without_a_path_keeps_it_in_memory(tmp_path: Path):
    store = AppStateStore(state=AppState(tdd_plan=plan("A")))
    update_tdd_plan(tool_context_for(store), lambda p: p.add("B"))
    assert store.get().tdd_plan == plan("A", "B")
    assert list(tmp_path.iterdir()) == []


def test_a_refused_change_leaves_state_and_file_untouched(tmp_path: Path):
    store = AppStateStore(state=AppState(tdd_plan=plan("A")))
    ctx = tool_context_for(store)
    ctx.tdd_plan_path = str(tmp_path / PLAN_FILE_NAME)
    with pytest.raises(PlanError):
        update_tdd_plan(ctx, lambda p: p.remove("9"))
    assert store.get().tdd_plan == plan("A")
    assert not (tmp_path / PLAN_FILE_NAME).exists()


# ── the UpdatePlan tool ──────────────────────────────────────────────────────

def test_apply_operations_in_order():
    p = apply_operations(plan("A", "B"), [
        {"op": "add", "text": "C", "position": 1},
        {"op": "edit", "id": "1", "text": "A2"},
        {"op": "move", "id": "2", "position": 1},
        {"op": "remove", "id": "3"},
    ])
    assert p.items == (PlanItem("2", "B"), PlanItem("1", "A2"))


@pytest.mark.parametrize("operation, message", [
    ({"op": "add"}, "'add' needs 'text'."),
    ({"op": "edit", "id": "1"}, "'edit' needs 'text'."),
    ({"op": "edit", "text": "x"}, "'edit' needs 'id'."),
    ({"op": "remove"}, "'remove' needs 'id'."),
    ({"op": "move", "id": "1"}, "'move' needs 'position'."),
    ({"op": "move", "position": 1}, "'move' needs 'id'."),
    ({"op": "done", "id": "1"}, "Unknown plan operation 'done'."),
])
def test_apply_operations_refuses_incomplete_or_unknown_operations(operation: dict[str, Any], message: str):
    with pytest.raises(PlanError) as exc:
        apply_operations(plan("A"), [operation])
    assert str(exc.value) == message


def test_the_schema_offers_no_way_to_mark_an_item_done():
    item = UPDATE_PLAN_INPUT_SCHEMA["properties"]["operations"]["items"]
    assert item["properties"]["op"]["enum"] == ["add", "edit", "remove", "move"]
    assert item["additionalProperties"] is False
    assert UPDATE_PLAN_INPUT_SCHEMA["additionalProperties"] is False
    assert UPDATE_PLAN_INPUT_SCHEMA["required"] == ["operations"]


def test_describe_change_shows_before_after_and_the_reason():
    before, after = plan("A"), plan("A", "B")
    assert describe_change(before, after, "needed") == (
        "Update the TDD plan?\n\nReason: needed\n\nBefore:\n\n▶ 1. A\n\nAfter:\n\n▶ 1. A\n· 2. B")
    assert describe_change(before, after) == "Update the TDD plan?\n\nBefore:\n\n▶ 1. A\n\nAfter:\n\n▶ 1. A\n· 2. B"


def _ctx(p: TddPlan, mode: PermissionMode = PermissionMode.ACCEPT_EDITS, avoid: bool = True) -> Any:
    perm = ToolPermissionContext(mode=mode, should_avoid_permission_prompts=avoid)
    store = AppStateStore(state=AppState(tdd_plan=p, tool_permission_context=perm))
    return tool_context_for(store, workspace=SimpleNamespace(root="/w"))


def test_the_tool_surface():
    tool = build_update_plan_tool()
    assert tool.name == UPDATE_PLAN_TOOL_NAME == "UpdatePlan"
    assert tool.prompt.startswith("Changes the session's TDD plan")
    assert tool.requires_user_interaction({}) is True
    assert tool.is_read_only({}) is False
    assert tool.is_concurrency_safe({}) is False
    assert (tool.is_implementation_writer(), tool.is_test_writer()) == (False, False)
    assert tool.description({"operations": [{}, {}]}) == "Update the TDD plan (2 changes)"
    assert tool.description({}) == "Update the TDD plan (0 changes)"


def test_validate_input_rejects_an_operation_that_cannot_apply():
    tool = build_update_plan_tool()
    ctx = _ctx(plan("A"))
    bad = tool.validate_input({"operations": [{"op": "remove", "id": "9"}]}, ctx)
    assert (bad.valid, bad.message, bad.error_code) == (False, "No sub-requirement with id '9'.", 1)
    assert tool.validate_input({"operations": [{"op": "add", "text": "B"}]}, ctx).valid is True


def test_check_permissions_asks_with_the_diff():
    tool = build_update_plan_tool()
    result = asyncio.run(tool.check_permissions(
        {"operations": [{"op": "add", "text": "B"}], "reason": "r"}, _ctx(plan("A"))))
    assert result.behavior == PermissionBehavior.ASK
    assert result.message == describe_change(plan("A"), plan("A", "B"), "r")
    assert result.decision_reason == {"type": "other", "reason": "A plan change needs the user's approval."}


@pytest.mark.parametrize(
    "mode", [PermissionMode.ACCEPT_EDITS, PermissionMode.BYPASS_PERMISSIONS, PermissionMode.DEFAULT]
)
def test_a_headless_run_cannot_change_its_own_plan(mode: PermissionMode):
    tool = build_update_plan_tool()
    result = asyncio.run(has_permissions_to_use_tool(
        tool, {"operations": [{"op": "add", "text": "B"}]}, _ctx(plan("A"), mode=mode), "tu"))
    assert result.behavior == PermissionBehavior.DENY


def test_an_interactive_run_gets_the_ask_even_under_bypass():
    tool = build_update_plan_tool()
    result = asyncio.run(has_permissions_to_use_tool(
        tool, {"operations": [{"op": "add", "text": "B"}]},
        _ctx(plan("A"), mode=PermissionMode.BYPASS_PERMISSIONS, avoid=False), "tu"))
    assert result.behavior == PermissionBehavior.ASK
    assert result.message.startswith("Update the TDD plan?")


def test_call_applies_and_reports_the_new_plan(tmp_path: Path):
    tool = build_update_plan_tool()
    ctx = _ctx(plan("A"))
    ctx.tdd_plan_path = str(tmp_path / PLAN_FILE_NAME)
    result = asyncio.run(tool.call({"operations": [{"op": "add", "text": "B"}]}, ctx))
    assert result.content == "The TDD plan is now:\n▶ 1. A\n· 2. B"
    assert ctx.get_app_state().tdd_plan == plan("A", "B")
    assert load_plan(tmp_path / PLAN_FILE_NAME) == plan("A", "B")


def test_replace_pending_keeps_done_items_and_replaces_the_rest():
    p = done_first(plan("A", "B", "C")).replace_pending(["X", "Y"])
    assert p.items == (PlanItem("1", "A", ItemStatus.DONE), PlanItem("2", "X"), PlanItem("3", "Y"))
    assert plan("A").replace_pending([]) == TddPlan()


def test_moving_the_work_to_another_item_restarts_the_ledger(tmp_path: Path):
    from app.tdd.ledger import TestRunRecord

    record = TestRunRecord(test_path="t", exit_code=1)
    red = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)
    for change in (lambda p: p.remove("1"), lambda p: p.move("2", 1), lambda p: p.edit("1", "A2"),
                   lambda p: p.replace_pending(["X"])):
        store = AppStateStore(state=AppState(tdd_plan=plan("A", "B"), phase_ledger=red, last_test_run=record))
        update_tdd_plan(tool_context_for(store), change)
        assert store.get().phase_ledger == PhaseLedger()
        assert store.get().last_test_run is None


def test_a_change_that_keeps_the_current_item_keeps_the_ledger():
    red = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)
    for change in (lambda p: p.add("C"), lambda p: p.edit("2", "B2"), lambda p: p.move("2", 3)):
        store = AppStateStore(state=AppState(tdd_plan=plan("A", "B"), phase_ledger=red))
        update_tdd_plan(tool_context_for(store), change)
        assert store.get().phase_ledger == red
