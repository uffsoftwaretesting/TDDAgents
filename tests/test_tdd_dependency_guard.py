"""
A dependency is installed, never faked (2026-10-28).

The CPF live run's agents wrote `fastapi.py` and `pydantic.py` to stand in for packages they never
installed, after RunTests called the import error "the code under test does not exist yet". The
fix follows claude-code's path safety check (`checkPathSafetyForAutoEdit`): a write that would
stand in for a dependency asks, bypass- and hook-immune. RunTests, the runner seam, tells a
missing dependency from code not written yet by the specification's Core Dependencies.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from app.state.app_state_store import AppState, AppStateStore
from app.tdd.dependency_guard import (
    declared_dependencies,
    is_declared,
    missing_dependency,
    missing_dependency_message,
    record_missing,
    stand_in_message,
    stand_ins,
    unimportable_module,
)
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.run_tests_tool.run_tests_tool import (
    SuiteExecutionResult,
    build_run_tests_tool,
)
from app.tool import tool_context_for
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    ToolPermissionContext,
)
from app.utils.permissions.filesystem import (
    DEPENDENCY_DIRECTORIES,
    check_path_safety_for_auto_edit,
    check_write_permission_for_tool,
    dependency_directory_in,
    dependency_stood_in_for,
    is_path_allowed,
    normalize_dependency_name,
)
from tests.conftest import FakeWorkspace

SPEC = """# CPF API

## 4. Technical Constraints
- FastAPI in the routes only.

## 5. Core Dependencies
- **FastAPI** (>=0.110): web framework
- `pydantic` — request validation
* validate-docbr: CPF check digits
1. pytest, pytest-cov and flake8
- uvicorn[standard]
Python 3.11 / Docker

## 6. Next
- requests
"""

BANNER = "______ ERROR collecting tests/test_api.py ______\n"


def collection_error(module: str) -> SuiteExecutionResult:
    return SuiteExecutionResult(
        exit_code=2,
        stdout=f"{BANNER}E   ModuleNotFoundError: No module named '{module}'\n!!! Interrupted: 1 error !!!")


# ── the specification's Core Dependencies ────────────────────────────────────

def test_declared_dependencies_are_the_names_of_the_core_dependencies_section():
    assert declared_dependencies(SPEC) == frozenset({
        "FastAPI", "pydantic", "validate-docbr", "pytest", "pytest-cov", "flake8", "uvicorn", "Python", "Docker"})


@pytest.mark.parametrize("spec", ["", "# Spec\n\n## 4. Constraints\n- fastapi\n", "Core Dependencies: fastapi"])
def test_no_core_dependencies_section_declares_nothing(spec: str):
    assert declared_dependencies(spec) == frozenset()


def test_a_section_without_a_number_or_at_the_end_still_counts():
    assert declared_dependencies("### Core dependencies\n\n- Express\n- jest\n") == frozenset({"Express", "jest"})


def test_names_compare_case_folded_with_dashes_underscores_and_dots_alike():
    assert normalize_dependency_name("Validate-Docbr") == "validate_docbr"
    assert normalize_dependency_name("validate_docbr") == "validate_docbr"
    assert normalize_dependency_name("zope.interface") == "zope_interface"
    assert is_declared("validate_docbr", frozenset({"validate-docbr"}))
    assert is_declared("FASTAPI", frozenset({"fastapi"}))
    assert not is_declared("calculator", frozenset({"fastapi"}))


# ── RunTests' reading of pytest ──────────────────────────────────────────────

DECLARED = frozenset({"fastapi", "validate-docbr"})


def test_a_declared_module_pytest_cannot_import_is_a_missing_dependency():
    ws = FakeWorkspace(files={"tests/test_api.py": "import fastapi\n"})
    result = collection_error("fastapi.testclient")
    assert missing_dependency(result.stdout, "", 2, ws, DECLARED) == "fastapi"
    assert missing_dependency("", collection_error("validate_docbr").stdout, 2, ws, DECLARED) == "validate_docbr"


@pytest.mark.parametrize("why, module, files, code, declared", [
    ("code not written yet", "calculator", {}, 2, DECLARED),
    ("the project defines it", "fastapi", {"fastapi/__init__.py": ""}, 2, DECLARED),
    ("not a collection error", "fastapi", {}, 1, DECLARED),
    ("nothing declared", "fastapi", {}, 2, frozenset()),
])
def test_anything_else_is_not_a_missing_dependency(
        why: str, module: str, files: dict[str, str], code: int, declared: frozenset[str]):
    ws = FakeWorkspace(files=files)
    assert missing_dependency(collection_error(module).stdout, "", code, ws, declared) is None, why


def test_the_banner_is_required_and_an_unlistable_workspace_classifies_nothing():
    text = "E   ModuleNotFoundError: No module named 'fastapi'"
    assert missing_dependency(text, "", 2, FakeWorkspace(), DECLARED) is None
    assert missing_dependency(collection_error("fastapi").stdout, "", 2, SimpleNamespace(), DECLARED) is None


def test_a_project_file_inside_a_dependency_directory_does_not_define_the_module():
    ws = FakeWorkspace(files={".venv/lib/site-packages/fastapi/__init__.py": "", "node_modules/fastapi/x.js": ""})
    assert missing_dependency(collection_error("fastapi").stdout, "", 2, ws, DECLARED) == "fastapi"


def test_unimportable_module_is_the_first_dotted_name():
    assert unimportable_module(collection_error("src.main").stdout, "") == "src.main"
    assert unimportable_module("E   ImportError: cannot import name 'x'", "") is None


def test_stand_ins_are_project_files_named_like_a_missing_dependency():
    ws = FakeWorkspace(files={"fastapi.py": "", "pydantic/__init__.py": "", "pydantic/main.py": "",
                              "src/api.py": "", "src/fastapi_app.py": "", ".venv/lib/pydantic/x.py": ""})
    assert stand_ins(ws, frozenset({"fastapi", "pydantic"})) == (("fastapi.py", "fastapi"), ("pydantic", "pydantic"))
    assert stand_ins(ws, frozenset()) == ()
    assert stand_ins(SimpleNamespace(), frozenset({"fastapi"})) == ()


def test_record_missing_adds_the_name_once():
    state = record_missing(AppState(), "fastapi")
    assert state.tool_permission_context.missing_dependencies == frozenset({"fastapi"})
    assert record_missing(state, "FastAPI") is state
    assert record_missing(state, "pydantic").tool_permission_context.missing_dependencies == frozenset(
        {"fastapi", "pydantic"})


def test_the_messages():
    assert missing_dependency_message("fastapi") == (
        "pytest could not import 'fastapi', which is not part of this project: it is not installed in the project's "
        "environment. The TDD ledger was not changed.")
    assert stand_in_message((("fastapi.py", "fastapi"), ("pydantic", "pydantic"))) == (
        "This run does not count: fastapi.py stands in for 'fastapi', pydantic stands in for 'pydantic', a "
        "dependency the tests could not import. The TDD ledger was not changed.")


# ── RunTests ─────────────────────────────────────────────────────────────────

def run(result: SuiteExecutionResult, files: dict[str, str], *, ledger: PhaseLedger | None = None,
        missing: frozenset[str] = frozenset(), shares: bool = True) -> tuple[Any, AppStateStore]:
    state = AppState(phase_ledger=ledger or PhaseLedger(), declared_dependencies=DECLARED,
                     tool_permission_context=ToolPermissionContext(missing_dependencies=missing))
    store = AppStateStore(state)
    ctx = replace(tool_context_for(store, workspace=FakeWorkspace(files=dict(files))), shares_app_state=shares)
    return asyncio.run(build_run_tests_tool(lambda path: result).call({}, ctx)), store


def test_a_missing_dependency_is_an_environment_error_and_is_recorded():
    res, store = run(collection_error("fastapi"), {"tests/test_api.py": "import fastapi\n"})
    assert (res.is_error, res.exit_code) == (True, 2)
    assert res.content.startswith(missing_dependency_message("fastapi") + "\n\n--- STDOUT ---\n")
    after = store.get()
    assert after.phase_ledger == PhaseLedger() and after.last_test_run is None
    assert after.tool_permission_context.missing_dependencies == frozenset({"fastapi"})


def test_a_background_run_reports_a_missing_dependency_and_records_nothing():
    res, store = run(collection_error("fastapi"), {"tests/test_api.py": ""}, shares=False)
    assert res.is_error is True and res.content.startswith(missing_dependency_message("fastapi"))
    assert store.get().tool_permission_context.missing_dependencies == frozenset()


def test_an_undeclared_missing_module_is_still_red_evidence_said_neutrally():
    res, store = run(collection_error("calculator"), {"tests/test_calc.py": "from calculator import add\n"})
    assert res.is_error is False
    assert res.content.startswith("Tests failed (exit 2: collection error — pytest could not import 'calculator').")
    assert store.get().phase_ledger.phase == TddPhase.GREEN


@pytest.mark.parametrize("shares", [True, False])
def test_a_stand_in_for_a_missing_dependency_refuses_the_run(shares: bool):
    files = {"tests/test_api.py": "import fastapi\n", "fastapi.py": "class FastAPI: ...\n"}
    green = PhaseLedger(phase=TddPhase.GREEN, tests_written=True)
    res, store = run(SuiteExecutionResult(exit_code=0, stdout="1 passed"), files, ledger=green,
                     missing=frozenset({"fastapi"}), shares=shares)
    assert res.is_error is True
    assert res.content.startswith(stand_in_message((("fastapi.py", "fastapi"),)))
    assert store.get().phase_ledger == green


def test_with_no_stand_in_the_run_counts():
    green = PhaseLedger(phase=TddPhase.GREEN, tests_written=True)
    res, store = run(SuiteExecutionResult(exit_code=0, stdout="1 passed"), {"tests/test_api.py": ""}, ledger=green,
                     missing=frozenset({"fastapi"}))
    assert res.is_error is False and store.get().phase_ledger.green_passed is True


# ── the path safety check (`checkPathSafetyForAutoEdit`) ─────────────────────

@pytest.mark.parametrize("directory", DEPENDENCY_DIRECTORIES)
def test_every_dependency_directory_is_found_case_folded_and_nested(directory: str):
    assert dependency_directory_in(f"/w/{directory}/pkg/x.js", "/w") == directory
    assert dependency_directory_in(f"/w/a/{directory.upper()}/x", "/w") == directory.upper()


def test_only_the_segments_below_the_working_directory_count():
    assert dependency_directory_in("/home/vendor/w/src/a.py", "/home/vendor/w") is None
    assert dependency_directory_in("/home/vendor/x/a.py", "/w") == "vendor"
    assert dependency_directory_in("/w/src/a.py") is None


@pytest.mark.parametrize("path, expected", [
    ("/w/fastapi.py", "fastapi"),
    ("/w/fastapi.pyi", "fastapi"),
    ("/w/pydantic/__init__.py", "pydantic"),
    ("/w/express/index.js", "express"),
    ("/w/validate_docbr.py", "validate-docbr"),
    ("/w/fastapi_app.py", None),
    ("/w/src/api.py", None),
    ("/w", None),
])
def test_a_stand_in_is_named_like_a_missing_dependency(path: str, expected: str | None):
    missing = ("fastapi", "pydantic", "express", "validate-docbr")
    assert dependency_stood_in_for(path, missing, "/w") == expected
    assert dependency_stood_in_for(path, (), "/w") is None


def test_the_project_folders_own_name_is_not_a_stand_in():
    assert dependency_stood_in_for("/home/fastapi/project/src/a.py", ("fastapi",), "/home/fastapi/project") is None


def test_the_safety_check_messages_and_approvability():
    assert check_path_safety_for_auto_edit("/w/node_modules/x.js", ("/w/node_modules/x.js",), (), "/w") == (
        False, ("The agent requested permissions to write to /w/node_modules/x.js, inside the dependency directory "
                "node_modules. Dependencies are installed with the project's package manager."), True)
    assert check_path_safety_for_auto_edit("/w/fastapi.py", ("/w/fastapi.py",), ("fastapi",), "/w") == (
        False, ("The agent requested permissions to write to /w/fastapi.py, which would stand in for the dependency "
                "'fastapi' the tests could not import. Install it with the project's package manager instead."), False)
    assert check_path_safety_for_auto_edit("/w/src/a.py", ("/w/src/a.py",), ("fastapi",), "/w") == (True, "", False)
    # Upstream's own predicates still come first.
    assert check_path_safety_for_auto_edit("/w/.git/x", ("/w/.git/x",), ("git",), "/w")[1].endswith("sensitive file.")


@pytest.mark.parametrize("mode", [PermissionMode.ACCEPT_EDITS, PermissionMode.BYPASS_PERMISSIONS])
def test_a_stand_in_write_asks_before_modes_and_allow_rules(mode: PermissionMode):
    from app.types.permissions import PermissionRule, PermissionRuleSource

    allow = PermissionRule("Edit", PermissionBehavior.ALLOW, None, PermissionRuleSource.PROJECT_SETTINGS)
    context = ToolPermissionContext(mode=mode, always_allow_rules=(allow,), missing_dependencies=frozenset({"fastapi"}))
    result = check_write_permission_for_tool("fastapi.py", {"file_path": "fastapi.py"}, context, "/w")
    assert result.behavior == PermissionBehavior.ASK
    assert result.decision_reason is not None and result.decision_reason["type"] == "safetyCheck"
    assert result.decision_reason["classifierApprovable"] is False


def test_a_bash_redirect_target_meets_the_same_check():
    context = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, missing_dependencies=frozenset({"fastapi"}))
    verdict = is_path_allowed("/w/fastapi.py", context, "/w", "create")
    assert verdict.allowed is False and verdict.decision_reason is not None
    assert verdict.decision_reason["type"] == "safetyCheck"
    assert is_path_allowed("/w/fastapi.py", context, "/w", "read").allowed is True
    assert is_path_allowed("/w/src/app.py", context, "/w", "create").allowed is True


def test_the_gate_keeps_the_ask_in_bypass_mode_and_a_headless_run_denies_it():
    from app.tools.file_write_tool.file_write_tool import build_write_file_tool
    from app.utils.permissions.permissions import has_permissions_to_use_tool

    tool = build_write_file_tool()
    for avoid, behavior in ((False, PermissionBehavior.ASK), (True, PermissionBehavior.DENY)):
        perm = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS, is_bypass_permissions_mode_available=True,
                                     missing_dependencies=frozenset({"fastapi"}),
                                     should_avoid_permission_prompts=avoid)
        store = AppStateStore(AppState(tool_permission_context=perm,
                                       phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
        ctx = tool_context_for(store, workspace=FakeWorkspace())
        result = asyncio.run(has_permissions_to_use_tool(tool, {"file_path": "fastapi.py", "content": "x"}, ctx, "c1"))
        assert result.behavior == behavior
