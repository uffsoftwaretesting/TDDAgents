"""
Debug logging: `--debug [filter]`, `--debug-file <path>`, `--debug-to-stderr`.

Ported from `reference/claude-code/src/utils/debug.ts` -> `DebugLogLevel`, `getMinDebugLogLevel`,
`isDebugMode`, `enableDebugLogging`, `getDebugFilter`, `isDebugToStdErr`, `getDebugFilePath`,
`logForDebugging`, `getDebugLogPath`, `updateLatestDebugLogSymlink`, `setHasFormattedOutput`; the
category filter is `app/utils/debug_filter.py`.

Nothing is written unless debug mode is on: `--debug` / `-d` (optionally with a category filter),
`--debug-to-stderr` / `-d2e`, `--debug-file <path>`, `enable_debug_logging()` (upstream's `/debug`),
or `DEBUG` / `DEBUG_SDK` truthy in the environment. Each line is
`<ISO timestamp> [LEVEL] <message>`, appended at once to the debug log
(`<config home>/debug/<session id>.txt`, `TDDAGENTS_DEBUG_LOGS_DIR` overriding it as upstream's
`CLAUDE_CODE_DEBUG_LOGS_DIR` does, `--debug-file` overriding both), with a `latest` symlink beside
it; with `--debug-to-stderr` it goes to stderr instead. `TDDAGENTS_DEBUG_LOG_LEVEL`
(`CLAUDE_CODE_DEBUG_LOG_LEVEL`) sets the lowest level written, `debug` by default, so the
high-volume `verbose` lines appear only when asked for.

*Divergences:* upstream reads its flags from `process.argv` (memoized); here `app/main.py` hands the
parsed flags to `configure_debug` once, and the filter is the `--debug` value however it was
written (`--debug=api,hooks` or `--debug api,hooks`; upstream filters only the `=` form). The
session id is TDDAgents' thread id, set by `app/main.py` once it is known (`set_debug_session_id`);
before that, a random id stands in, as upstream's session id before `--resume` switches it. Upstream's
ant-only buffered writer is not ported (non-ant builds write only in debug mode, synchronously).
`DebugLogHandler` routes what TDDAgents' own loggers emit (warnings and errors, at their configured
levels) into the same log, where upstream's code calls `logForDebugging` / `logError` directly.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from app.utils.debug_filter import DebugFilter, parse_debug_filter, should_show_debug_message

DebugLogLevel = Literal["verbose", "debug", "info", "warn", "error"]

#: `LEVEL_ORDER`.
LEVEL_ORDER: dict[str, int] = {"verbose": 0, "debug": 1, "info": 2, "warn": 3, "error": 4}
#: `CLAUDE_CODE_DEBUG_LOG_LEVEL`, renamed.
DEBUG_LOG_LEVEL_ENV = "TDDAGENTS_DEBUG_LOG_LEVEL"
#: `CLAUDE_CODE_DEBUG_LOGS_DIR`, renamed (a file path, as upstream uses it).
DEBUG_LOGS_DIR_ENV = "TDDAGENTS_DEBUG_LOGS_DIR"
#: The loggers `DebugLogHandler` forwards: TDDAgents' own, never a library's.
APP_LOGGER_PREFIXES: tuple[str, ...] = ("app", "TDDOrchestrator", "TDDMain", "loop_runner")


@dataclass(slots=True)
class _DebugState:
    """The process's debug flags (upstream reads them from `process.argv`)."""

    flag: bool = False
    filter_pattern: str | None = None
    file_path: str | None = None
    to_stderr: bool = False
    runtime_enabled: bool = False
    has_formatted_output: bool = False
    session_id: str = ""
    symlinked: str | None = None


_STATE = _DebugState(session_id=str(uuid.uuid4()))


def _is_env_truthy(value: str | None) -> bool:
    """`isEnvTruthy` (`app/utils/claudemd.py`'s, repeated so this module imports nothing of the app's)."""
    return value is not None and value.strip().lower() in ("1", "true", "yes", "on")


def _config_home() -> Path:
    """`getClaudeConfigHomeDir` (`app/utils/tool_result_storage.py`'s `config_home`)."""
    override = os.environ.get("TDDAGENTS_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".tddagents"


def configure_debug(
    debug: bool | str | None = None,
    *,
    debug_file: str | None = None,
    to_stderr: bool = False,
) -> None:
    """
    The `--debug [filter]`, `--debug-file` and `--debug-to-stderr` flags of this process. `debug` is
    `True` for a bare `--debug`, the filter string for `--debug <filter>`, falsy when absent.
    """
    _STATE.flag = bool(debug)
    _STATE.filter_pattern = debug if isinstance(debug, str) else None
    _STATE.file_path = debug_file
    _STATE.to_stderr = to_stderr
    _STATE.symlinked = None


def reset_debug_state() -> None:
    """Forget every flag (tests; upstream clears its memoized readers)."""
    global _STATE
    _STATE = _DebugState(session_id=str(uuid.uuid4()))


def set_debug_session_id(session_id: str) -> None:
    """The session the default log file is named after (`getSessionId`)."""
    _STATE.session_id = session_id
    _STATE.symlinked = None


def get_min_debug_log_level() -> str:
    """`getMinDebugLogLevel`."""
    raw = (os.environ.get(DEBUG_LOG_LEVEL_ENV) or "").lower().strip()
    return raw if raw in LEVEL_ORDER else "debug"


def is_debug_to_stderr() -> bool:
    """`isDebugToStdErr`."""
    return _STATE.to_stderr


def get_debug_file_path() -> str | None:
    """`getDebugFilePath`."""
    return _STATE.file_path


def is_debug_mode() -> bool:
    """`isDebugMode`."""
    return (
        _STATE.runtime_enabled
        or _is_env_truthy(os.environ.get("DEBUG"))
        or _is_env_truthy(os.environ.get("DEBUG_SDK"))
        or _STATE.flag
        or is_debug_to_stderr()
        or get_debug_file_path() is not None
    )


def enable_debug_logging() -> bool:
    """`enableDebugLogging`: debug mode from now on. Returns whether it was already on."""
    was_active = is_debug_mode()
    _STATE.runtime_enabled = True
    return was_active


def get_debug_filter() -> DebugFilter | None:
    """`getDebugFilter`."""
    return parse_debug_filter(_STATE.filter_pattern)


def set_has_formatted_output(value: bool) -> None:
    """`setHasFormattedOutput`: stdout carries JSON, so a multiline message is written as JSON."""
    _STATE.has_formatted_output = value


def get_has_formatted_output() -> bool:
    """`getHasFormattedOutput`."""
    return _STATE.has_formatted_output


def _should_log_debug_message(message: str) -> bool:
    """`shouldLogDebugMessage`."""
    if not is_debug_mode():
        return False
    return should_show_debug_message(message, get_debug_filter())


def get_debug_log_path() -> str:
    """`getDebugLogPath`."""
    return (
        get_debug_file_path()
        or os.environ.get(DEBUG_LOGS_DIR_ENV)
        or str(_config_home() / "debug" / f"{_STATE.session_id}.txt")
    )


def _update_latest_debug_log_symlink(path: str) -> None:
    """`updateLatestDebugLogSymlink`: `<log dir>/latest` points at this log; failures are silent."""
    if _STATE.symlinked == path:
        return
    _STATE.symlinked = path
    latest = Path(path).parent / "latest"
    try:
        latest.unlink(missing_ok=True)
        latest.symlink_to(path)
    except OSError:
        pass


def _timestamp() -> str:
    """`new Date().toISOString()`."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log_for_debugging(message: str, *, level: DebugLogLevel = "debug") -> None:
    """`logForDebugging`. Never raises: a debug log that cannot be written is skipped."""
    if LEVEL_ORDER[level] < LEVEL_ORDER[get_min_debug_log_level()]:
        return
    if not _should_log_debug_message(message):
        return
    if _STATE.has_formatted_output and "\n" in message:
        message = json.dumps(message)
    output = f"{_timestamp()} [{level.upper()}] {message.strip()}\n"
    if is_debug_to_stderr():
        sys.stderr.write(output)
        sys.stderr.flush()
        return
    path = get_debug_log_path()
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(output)
    except OSError:
        return
    _update_latest_debug_log_symlink(path)


#: Python logging levels as debug levels.
_PY_LEVELS: tuple[tuple[int, DebugLogLevel], ...] = (
    (logging.ERROR, "error"),
    (logging.WARNING, "warn"),
    (logging.INFO, "info"),
    (logging.DEBUG, "debug"),
)


class DebugLogHandler(logging.Handler):
    """
    TDDAgents' own log records into the debug log, at the levels their loggers already emit: what
    upstream's code writes with `logForDebugging(…, {level})` and `logError` directly.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return _is_app_record(record)

    def emit(self, record: logging.LogRecord) -> None:
        level: DebugLogLevel = "verbose"
        for threshold, name in _PY_LEVELS:
            if record.levelno >= threshold:
                level = name
                break
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a malformed record never breaks the caller's logging
            return
        log_for_debugging(message, level=level)


#: `extra=` for one of TDDAgents' own warnings that upstream shows the user on purpose (2026-11-10): a
#: `process.stderr.write` / `console.error` at startup or exit, a UI notification, `-p`'s error.
TERMINAL: dict[str, bool] = {"terminal": True}


def _is_app_record(record: logging.LogRecord) -> bool:
    return any(record.name == p or record.name.startswith(p + ".") for p in APP_LOGGER_PREFIXES)


class TerminalLogFilter(logging.Filter):
    """
    Keeps TDDAgents' own warnings and errors off the terminal's log handler (2026-11-10). Upstream writes
    its diagnostics with `logForDebugging(…, {level})` or `logError`, neither of which reaches the
    terminal; here they reach the debug log through `DebugLogHandler`. A live sandboxed run printed "MCP
    config errors for …/.mcp.json" over the REPL's permission prompts. Records below WARNING (the
    session's own report lines) and records marked `TERMINAL` still print.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.WARNING or not _is_app_record(record):
            return True
        return bool(getattr(record, "terminal", False))


def install_terminal_log_filter(root: logging.Logger | None = None) -> None:
    """
    Add `TerminalLogFilter`, once, to the root logger's terminal handlers: plain `StreamHandler`s (what
    `logging.basicConfig` installs) writing to the process's stdout or stderr. Subclasses (file handlers,
    a test runner's capture handlers) are left alone.
    """
    for handler in (root or logging.getLogger()).handlers:
        if type(handler) is logging.StreamHandler \
                and getattr(handler, "stream", None) in (sys.stderr, sys.stdout, sys.__stderr__, sys.__stdout__) \
                and not any(isinstance(f, TerminalLogFilter) for f in handler.filters):
            handler.addFilter(TerminalLogFilter())


def install_debug_log_handler() -> DebugLogHandler | None:
    """In debug mode, route TDDAgents' own log records into the debug log. Returns the handler to remove."""
    if not is_debug_mode():
        return None
    handler = DebugLogHandler()
    logging.getLogger().addHandler(handler)
    return handler


__all__ = [
    "APP_LOGGER_PREFIXES",
    "DEBUG_LOGS_DIR_ENV",
    "DEBUG_LOG_LEVEL_ENV",
    "DebugLogHandler",
    "TERMINAL",
    "TerminalLogFilter",
    "DebugLogLevel",
    "LEVEL_ORDER",
    "configure_debug",
    "enable_debug_logging",
    "get_debug_file_path",
    "get_debug_filter",
    "get_debug_log_path",
    "get_has_formatted_output",
    "get_min_debug_log_level",
    "install_terminal_log_filter",
    "install_debug_log_handler",
    "is_debug_mode",
    "is_debug_to_stderr",
    "log_for_debugging",
    "reset_debug_state",
    "set_debug_session_id",
    "set_has_formatted_output",
]
