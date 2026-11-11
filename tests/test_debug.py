"""
`--debug [filter]` (`reference/claude-code/src/utils/debug.ts`, `debugFilter.ts`, `main.tsx`'s
options): the category filter, the debug log file, the flags, and what the hooks write to it.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import sys
from pathlib import Path

import pytest

from app.main import apply_debug_flags, build_parser
from app.schemas.hooks import parse_hooks_settings
from app.utils import debug
from app.utils.debug import (
    DebugLogHandler,
    configure_debug,
    enable_debug_logging,
    get_debug_filter,
    get_debug_log_path,
    get_has_formatted_output,
    get_min_debug_log_level,
    install_debug_log_handler,
    is_debug_mode,
    log_for_debugging,
    set_debug_session_id,
)
from app.utils.debug_filter import (
    DebugFilter,
    extract_debug_categories,
    parse_debug_filter,
    should_show_debug_categories,
    should_show_debug_message,
)
from app.utils.hooks.hooks import HookDispatcher, get_matching_hooks
from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot

LINE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z \[(VERBOSE|DEBUG|INFO|WARN|ERROR)\] (.*)$")


def _lines(path: Path) -> list[tuple[str, str]]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = LINE.match(raw)
        assert match, raw
        out.append((match.group(1), match.group(2)))
    return out


# ── debugFilter.ts ───────────────────────────────────────────────────────────

class TestFilter:
    @pytest.mark.parametrize("text", [None, "", "   ", ",", " , ,"])
    def test_no_filter(self, text):
        assert parse_debug_filter(text) is None

    def test_inclusive(self):
        assert parse_debug_filter(" API, Hooks ") == DebugFilter(include=("api", "hooks"), exclude=(),
                                                                 is_exclusive=False)

    def test_exclusive(self):
        assert parse_debug_filter("!1p,!FILE") == DebugFilter(include=(), exclude=("1p", "file"), is_exclusive=True)

    def test_mixed_is_no_filter(self):
        assert parse_debug_filter("api,!file") is None

    @pytest.mark.parametrize("message, categories", [
        ("Hooks: Checking first line for async: {}", ["hooks"]),
        ('MCP server "GitHub": connected', ["mcp", "github"]),
        ("MCP server 'x': failed: timeout", ["mcp", "x", "failed"]),
        ("[ANT-ONLY] 1P event: tengu_timer", ["ant-only", "1p"]),
        ("[HOOKS] matched", ["hooks"]),
        ("AutoUpdaterWrapper: Installation type: development", ["autoupdaterwrapper", "installation"]),
        ("no categories here", []),
        ("Settings: a very long secondary category that has spaces: x", ["settings"]),
    ])
    def test_categories(self, message, categories):
        assert extract_debug_categories(message) == categories

    def test_long_secondary_category_is_dropped(self):
        assert extract_debug_categories("a: " + "x" * 30 + ": b") == ["a"]
        assert extract_debug_categories("a: " + "x" * 29 + ": b") == ["a", "x" * 29]

    def test_should_show(self):
        include = parse_debug_filter("hooks")
        exclude = parse_debug_filter("!hooks")
        assert should_show_debug_message("anything", None)
        assert should_show_debug_message("Hooks: x", include) and not should_show_debug_message("API: x", include)
        assert not should_show_debug_message("Hooks: x", exclude) and should_show_debug_message("API: x", exclude)
        # An uncategorized message passes no filter, inclusive or exclusive.
        assert not should_show_debug_message("plain", include) and not should_show_debug_message("plain", exclude)
        assert should_show_debug_categories([], None) and not should_show_debug_categories([], include)


# ── debug.ts ─────────────────────────────────────────────────────────────────

class TestDebugMode:
    def test_off_by_default_and_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
        assert not is_debug_mode()
        log_for_debugging("Hooks: x")
        assert not (tmp_path / "debug").exists()

    @pytest.mark.parametrize("env", ["DEBUG", "DEBUG_SDK"])
    def test_env_turns_it_on(self, env, monkeypatch):
        monkeypatch.setenv(env, "1")
        assert is_debug_mode()
        monkeypatch.setenv(env, "0")
        assert not is_debug_mode()

    def test_each_flag_turns_it_on(self, tmp_path):
        configure_debug(True)
        assert is_debug_mode() and get_debug_filter() is None
        configure_debug("hooks")
        assert is_debug_mode() and get_debug_filter() == parse_debug_filter("hooks")
        configure_debug(None, to_stderr=True)
        assert is_debug_mode()
        configure_debug(None, debug_file=str(tmp_path / "d.txt"))
        assert is_debug_mode()
        configure_debug(None)
        assert not is_debug_mode()

    def test_enable_debug_logging_mid_session(self):
        assert enable_debug_logging() is False
        assert is_debug_mode()
        assert enable_debug_logging() is True

    def test_default_log_path_is_the_session_under_the_config_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
        set_debug_session_id("tdd-abc")
        configure_debug(True)
        assert get_debug_log_path() == str(tmp_path / "debug" / "tdd-abc.txt")
        log_for_debugging("Hooks: one")
        log_for_debugging("  two  \n")
        assert _lines(tmp_path / "debug" / "tdd-abc.txt") == [("DEBUG", "Hooks: one"), ("DEBUG", "two")]
        latest = tmp_path / "debug" / "latest"
        assert latest.is_symlink() and os.readlink(latest) == str(tmp_path / "debug" / "tdd-abc.txt")

    def test_logs_dir_env_then_debug_file_win(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TDDAGENTS_DEBUG_LOGS_DIR", str(tmp_path / "env.txt"))
        configure_debug(True)
        assert get_debug_log_path() == str(tmp_path / "env.txt")
        configure_debug(True, debug_file=str(tmp_path / "flag.txt"))
        assert get_debug_log_path() == str(tmp_path / "flag.txt")

    def test_category_filter(self, tmp_path):
        target = tmp_path / "d.txt"
        configure_debug("hooks", debug_file=str(target))
        log_for_debugging("Hooks: kept")
        log_for_debugging("API: dropped")
        log_for_debugging("uncategorized, dropped")
        assert _lines(target) == [("DEBUG", "Hooks: kept")]

    def test_levels(self, tmp_path, monkeypatch):
        target = tmp_path / "d.txt"
        configure_debug(True, debug_file=str(target))
        assert get_min_debug_log_level() == "debug"
        log_for_debugging("a: verbose", level="verbose")
        log_for_debugging("a: error", level="error")
        monkeypatch.setenv("TDDAGENTS_DEBUG_LOG_LEVEL", " Verbose ")
        log_for_debugging("a: verbose now", level="verbose")
        monkeypatch.setenv("TDDAGENTS_DEBUG_LOG_LEVEL", "warn")
        log_for_debugging("a: info", level="info")
        log_for_debugging("a: warn", level="warn")
        monkeypatch.setenv("TDDAGENTS_DEBUG_LOG_LEVEL", "nonsense")
        assert get_min_debug_log_level() == "debug"
        assert _lines(target) == [("ERROR", "a: error"), ("VERBOSE", "a: verbose now"), ("WARN", "a: warn")]

    def test_to_stderr_writes_no_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
        configure_debug(None, to_stderr=True)
        log_for_debugging("Hooks: to stderr", level="warn")
        err = capsys.readouterr().err
        assert LINE.match(err.rstrip("\n")) and err.endswith("[WARN] Hooks: to stderr\n")
        assert not (tmp_path / "debug").exists()

    def test_formatted_output_writes_a_multiline_message_as_json(self, tmp_path):
        target = tmp_path / "d.txt"
        configure_debug(True, debug_file=str(target))
        debug.set_has_formatted_output(True)
        log_for_debugging("a\nb")
        log_for_debugging("one line")
        assert _lines(target) == [("DEBUG", json.dumps("a\nb")), ("DEBUG", "one line")]

    def test_unwritable_log_is_skipped(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        configure_debug(True, debug_file=str(blocker / "sub" / "d.txt"))
        log_for_debugging("Hooks: nowhere")  # never raises


class TestHandler:
    def test_forwards_only_tddagents_loggers(self, tmp_path):
        target = tmp_path / "d.txt"
        configure_debug(True, debug_file=str(target))
        handler = install_debug_log_handler()
        assert isinstance(handler, DebugLogHandler)
        try:
            logging.getLogger("TDDOrchestrator.Hooks").warning("Hooks: from %s", "a warning")
            logging.getLogger("app.utils.x").error("boom")
            logging.getLogger("TDDMain").info("main info")
            logging.getLogger("httpx").error("a library's error")
            logging.getLogger("TDDOrchestratorX").error("a lookalike name")
        finally:
            logging.getLogger().removeHandler(handler)
        assert _lines(target) == [("WARN", "Hooks: from a warning"), ("ERROR", "boom"), ("INFO", "main info")]

    def test_levels_below_info_are_debug_and_verbose(self, tmp_path, monkeypatch):
        target = tmp_path / "d.txt"
        monkeypatch.setenv("TDDAGENTS_DEBUG_LOG_LEVEL", "verbose")
        configure_debug(True, debug_file=str(target))
        handler = DebugLogHandler()
        for level in (logging.DEBUG, 5):
            handler.handle(logging.LogRecord("app.x", level, __file__, 1, "lvl %s", (level,), None))
        handler.handle(logging.LogRecord("app.x", logging.ERROR, __file__, 1, "bad %s %s", ("args",), None))
        assert _lines(target) == [("DEBUG", "lvl 10"), ("VERBOSE", "lvl 5")]

    def test_not_installed_outside_debug_mode(self):
        assert install_debug_log_handler() is None


# ── main.tsx's options ───────────────────────────────────────────────────────

class TestFlags:
    @pytest.mark.parametrize("argv, value", [
        ([], None), (["--debug"], True), (["-d"], True), (["--debug=api,hooks"], "api,hooks"),
        (["--debug", "hooks"], "hooks"), (["-dhooks"], "hooks"),
    ])
    def test_debug(self, argv, value):
        args = build_parser().parse_args(argv)
        assert args.debug == value

    def test_hidden_and_file_flags(self):
        args = build_parser().parse_args(["-d2e", "--debug-file", "/x/d.txt"])
        assert args.debug_to_stderr and args.debug_file == "/x/d.txt" and args.debug is None
        assert build_parser().parse_args(["--debug-to-stderr"]).debug_to_stderr
        help_text = " ".join(build_parser().format_help().split())
        assert "--debug-to-stderr" not in help_text and "-d2e" not in help_text
        assert '"api,hooks" or "!1p,!file"' in help_text and "implicitly enables debug mode" in help_text

    def test_apply_debug_flags(self, tmp_path):
        args = build_parser().parse_args(["-p", "--output-format", "json", "--debug=hooks",
                                          "--debug-file", str(tmp_path / "d.txt")])
        handler = apply_debug_flags(args)
        try:
            assert isinstance(handler, DebugLogHandler) and handler in logging.getLogger().handlers
            assert get_debug_filter() == parse_debug_filter("hooks") and get_has_formatted_output()
            assert get_debug_log_path() == str(tmp_path / "d.txt")
        finally:
            logging.getLogger().removeHandler(handler)

    def test_apply_debug_flags_without_debug(self):
        args = build_parser().parse_args(["--output-format", "stream-json"])
        assert apply_debug_flags(args) is None
        assert not is_debug_mode() and not get_has_formatted_output()

    def test_main_names_the_log_after_the_thread_and_removes_its_handler(self, tmp_path, monkeypatch):
        import app.main as main_module

        monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
        seen: dict[str, object] = {}

        def fake_entry(args):
            set_debug_session_id("tdd-thread")
            log_for_debugging("Hooks: inside the session")
            seen["handlers"] = [h for h in logging.getLogger().handlers if isinstance(h, DebugLogHandler)]
            return 7

        monkeypatch.setattr(main_module, "run_entry", fake_entry)
        assert main_module.main(["--debug"]) == 7
        assert len(seen["handlers"]) == 1  # type: ignore[arg-type]
        assert not [h for h in logging.getLogger().handlers if isinstance(h, DebugLogHandler)]
        assert _lines(tmp_path / "debug" / "tdd-thread.txt") == [("DEBUG", "Hooks: inside the session")]


# ── what the hooks write ─────────────────────────────────────────────────────

def _snapshot(hooks: dict) -> HooksConfigSnapshot:
    return HooksConfigSnapshot(hooks=parse_hooks_settings(hooks, origin="settings"))


def test_matching_hooks_are_logged_at_verbose(tmp_path, monkeypatch):
    target = tmp_path / "d.txt"
    monkeypatch.setenv("TDDAGENTS_DEBUG_LOG_LEVEL", "verbose")
    configure_debug(True, debug_file=str(target))
    snapshot = _snapshot({"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "a"},
                                                                       {"type": "command", "command": "a"}]},
                                         {"matcher": "Edit", "hooks": [{"type": "command", "command": "b"}]}]})
    found = get_matching_hooks(snapshot, "PreToolUse", {"hook_event_name": "PreToolUse", "tool_name": "Bash"})
    assert len(found) == 1
    assert [m for _, m in _lines(target)] == [
        "Getting matching hook commands for PreToolUse with query: Bash",
        "Found 2 hook matchers in settings",
        'Matched 1 unique hooks for query "Bash" (2 before deduplication)',
    ]


def test_if_conditions_and_http_skips_are_logged(tmp_path):
    target = tmp_path / "d.txt"
    configure_debug(True, debug_file=str(target))
    snapshot = _snapshot({"SessionStart": [{"hooks": [{"type": "command", "command": "c", "if": "Bash(git *)"},
                                                      {"type": "http", "url": "https://h.example/x"}]}]})
    assert get_matching_hooks(snapshot, "SessionStart", {"hook_event_name": "SessionStart", "source": "startup"}) == []
    messages = [m for _, m in _lines(target)]
    assert messages == ['Hook if condition "Bash(git *)" cannot be evaluated for non-tool event SessionStart',
                        "Skipping HTTP hook https://h.example/x — HTTP hooks are not supported for SessionStart"]


def test_an_invalid_matcher_regex_is_logged(tmp_path):
    from app.utils.hooks.hooks import matches_pattern

    target = tmp_path / "d.txt"
    configure_debug(True, debug_file=str(target))
    assert not matches_pattern("Bash", "(unclosed")
    assert _lines(target) == [("DEBUG", "Invalid regex pattern in hook matcher: (unclosed")]


def test_a_hook_run_is_logged(tmp_path):
    target = tmp_path / "d.txt"
    configure_debug("hooks,sessionend", debug_file=str(target))
    snapshot = _snapshot({"SessionEnd": [{"hooks": [{"type": "command", "command": "echo '{\"systemMessage\": 1}'"},
                                                    {"type": "command", "command": "exit 3"}]}]})
    dispatcher = HookDispatcher(snapshot=snapshot, session_id="s", cwd=str(tmp_path / "gone"),
                                project_dir=str(tmp_path))
    results = asyncio.run(dispatcher.execute_hooks_outside_repl(
        {"hook_event_name": "SessionEnd", "reason": "other"}, match_query="other"))
    assert len(results) == 2
    text = target.read_text(encoding="utf-8")
    assert f"[WARN] Hooks: cwd {tmp_path / 'gone'} not found, falling back to original cwd\n" in text
    assert "[DEBUG] SessionEnd:other [exit 3] completed with status 3\n" in text
    assert "[DEBUG] SessionEnd:other [echo '{\"systemMessage\": 1}'] completed with status 0\n" in text
    # A JSON output failing the schema: the hook failed to run (multiline, as upstream writes it).
    assert "[ERROR] SessionEnd:other [echo '{\"systemMessage\": 1}'] failed to run: " in text


def test_a_hook_decision_is_logged(tmp_path):
    from app.types.hooks import HookResult
    from app.utils.hooks.hooks import _log_hook_result

    target = tmp_path / "d.txt"
    configure_debug(True, debug_file=str(target))
    hook = parse_hooks_settings({"PreToolUse": [{"hooks": [{"type": "command", "command": "cmd"}]}]},
                                origin="settings")["PreToolUse"][0].hooks[0]
    _log_hook_result("PreToolUse", HookResult(
        hook=hook, outcome="success", prevent_continuation=True, additional_context="abc",
        initial_user_message="hello", watch_paths=("a", "b"), updated_mcp_tool_output={"x": 1},
        permission_behavior="deny", hook_permission_decision_reason="no", updated_input={"command": "x", "y": 1}))
    _log_hook_result("PreToolUse", HookResult(hook=hook, outcome="success", permission_behavior="allow"))
    _log_hook_result("PreToolUse", HookResult(hook=hook, outcome="success"))
    assert [m for _, m in _lines(target)] == [
        "Hook PreToolUse (cmd) requested preventContinuation",
        "Hook PreToolUse (cmd) provided additionalContext (3 chars)",
        "Hook PreToolUse (cmd) provided initialUserMessage (5 chars)",
        "Hook PreToolUse (cmd) provided 2 watchPaths",
        "Hook PreToolUse (cmd) replaced MCP tool output",
        "Hook PreToolUse (cmd) returned permissionDecision: deny (reason: no)",
        "Hook PreToolUse (cmd) modified tool input keys: [command, y]",
        "Hook PreToolUse (cmd) returned permissionDecision: allow",
    ]


def test_disable_all_hooks_is_logged(tmp_path):
    target = tmp_path / "d.txt"
    configure_debug(True, debug_file=str(target))
    dispatcher = HookDispatcher(snapshot=HooksConfigSnapshot(disable_all=True), session_id="s", cwd=str(tmp_path),
                                project_dir=str(tmp_path))
    assert asyncio.run(dispatcher.execute_hooks_outside_repl({"hook_event_name": "SessionEnd"}, match_query="x")) == []

    async def drain() -> list:
        return [item async for item in dispatcher.execute_hooks({"hook_event_name": "Stop"}, "t")]

    assert asyncio.run(drain()) == []
    assert [m for _, m in _lines(target)] == [
        "Skipping hooks for SessionEnd:x due to 'disableAllHooks' managed setting",
        "Skipping hooks for Stop due to 'disableAllHooks' managed setting",
    ]


# ── TDDAgents' own warnings stay off the terminal (2026-11-10) ───────────────

def _terminal(root: logging.Logger) -> tuple[logging.StreamHandler, io.StringIO]:
    from app.utils.debug import install_terminal_log_filter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root.addHandler(handler)
    # The terminal handler is the one writing to the process's stderr; stand the test's stream in for it.
    real = sys.stderr
    sys.stderr = stream
    try:
        install_terminal_log_filter(root)
        install_terminal_log_filter(root)  # once only
    finally:
        sys.stderr = real
    # A handler writing elsewhere (a file, a capture) is left alone.
    elsewhere = logging.StreamHandler(io.StringIO())
    root.addHandler(elsewhere)
    install_terminal_log_filter(root)
    assert elsewhere.filters == []
    root.removeHandler(elsewhere)
    return handler, stream


def test_the_terminal_filter_keeps_app_warnings_off_and_lets_the_rest_through():
    from app.utils.debug import TERMINAL, TerminalLogFilter

    root = logging.getLogger("tdd-filter-test-root")
    root.propagate = False
    handler, stream = _terminal(root)
    try:
        assert [type(f) for f in handler.filters] == [TerminalLogFilter]
        mine = logging.makeLogRecord
        for record, shown in [
            (mine({"name": "app.services.mcp.config", "levelno": logging.ERROR, "msg": "diag"}), False),
            (mine({"name": "TDDOrchestrator.Loop", "levelno": logging.WARNING, "msg": "diag"}), False),
            (mine({"name": "app.x", "levelno": logging.WARNING, "msg": "shown", **TERMINAL}), True),
            (mine({"name": "TDDMain", "levelno": logging.INFO, "msg": "report"}), True),
            (mine({"name": "httpx", "levelno": logging.ERROR, "msg": "library"}), True),
            (mine({"name": "application", "levelno": logging.ERROR, "msg": "not ours"}), True),
        ]:
            assert bool(handler.filter(record)) is shown, record.name
    finally:
        root.removeHandler(handler)


def test_an_empty_mcp_json_reaches_the_debug_log_and_not_the_terminal(tmp_path):
    """The live sandboxed run: bwrap's empty `.mcp.json` mount point printed an error over the prompt."""
    from app.services.mcp.config import _file_scope
    from app.utils.debug import DebugLogHandler

    target = tmp_path / "debug.txt"
    configure_debug(True, debug_file=str(target))
    root = logging.getLogger()
    handler, stream = _terminal(root)
    debug_handler = DebugLogHandler()
    root.addHandler(debug_handler)
    try:
        (tmp_path / ".mcp.json").write_text("", encoding="utf-8")
        servers, errors = _file_scope(tmp_path / ".mcp.json", "project")
        assert servers == {} and [e.message for e in errors] == ["MCP config is not a valid JSON"]
        assert stream.getvalue() == ""
        assert f'[ERROR] MCP config errors for {tmp_path / ".mcp.json"}: ["MCP config is not a valid JSON"]' in (
            target.read_text(encoding="utf-8"))
    finally:
        root.removeHandler(handler)
        root.removeHandler(debug_handler)
