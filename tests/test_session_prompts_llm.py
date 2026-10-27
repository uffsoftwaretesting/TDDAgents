"""Phase 9: the shell's Markdown prompts and its `with_retry` wrapper."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import app.session.llm as session_llm
from app.services.api.with_retry import DEFAULT_MAX_RETRIES, CannotRetryError
from app.session.prompts import (
    PROMPTS_DIR,
    analyst_prompts,
    engineer_prompts,
    planner_prompts,
)
from tests.retry_doubles import RetryableError


def _file(role: str, name: str) -> str:
    return (PROMPTS_DIR / role / name).read_text(encoding="utf-8")


class TestPrompts:
    def test_prompt_files_live_under_app_prompts_session(self) -> None:
        assert PROMPTS_DIR == Path(__file__).resolve().parents[1] / "app" / "prompts" / "session"
        for role in ("analyst", "engineer", "planner"):
            assert (PROMPTS_DIR / role / "system.md").is_file()
            assert (PROMPTS_DIR / role / "request.md").is_file()

    def test_no_jinja_left(self) -> None:
        for path in PROMPTS_DIR.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            assert "{%" not in text and "{{ " not in text, path

    def test_system_prompts_are_the_files(self) -> None:
        for role, build in (("analyst", lambda: analyst_prompts("u")), ("engineer", lambda: engineer_prompts("r")),
                            ("planner", lambda: planner_prompts("s"))):
            system, _ = build()
            assert system == _file(role, "system.md").strip()

    def test_analyst_without_history_has_no_history_section(self) -> None:
        _, human = analyst_prompts("Build X")
        assert human.startswith("📝 CURRENT USER REQUEST:\nBuild X\n\n🎯 TASK:")
        assert "CONVERSATION HISTORY" not in human
        assert "{{" not in human

    def test_analyst_with_history(self) -> None:
        _, human = analyst_prompts("EUR", "[User]: a")
        assert "📝 CURRENT USER REQUEST:\nEUR\n\n📚 CONVERSATION HISTORY:\n[User]: a\n\n🎯 TASK:" in human

    def test_engineer_prefers_the_history(self) -> None:
        _, human = engineer_prompts("REQ", "HIST")
        assert '"""\nHIST\n"""' in human
        assert "\"\"\"\nREQ\n\"\"\"" not in human
        _, human = engineer_prompts("REQ")
        assert '"""\nREQ\n"""' in human

    def test_planner_embeds_the_specification(self) -> None:
        _, human = planner_prompts("# Spec")
        assert '📋 PROJECT SPECIFICATION:\n"""\n# Spec\n"""' in human
        assert human == human.strip()

    def test_values_are_not_rescanned(self) -> None:
        """A placeholder inside user text survives verbatim: substitution is one pass."""
        _, human = analyst_prompts("{{CONVERSATION_HISTORY}}", "")
        assert "{{CONVERSATION_HISTORY}}" in human

    def test_planner_names_the_dependencies_section(self) -> None:
        system, _ = planner_prompts("s")
        assert '"Core Dependencies" section' in system
        assert "Section 6" not in system
        assert "## 5. Core Dependencies" in _file("engineer", "system.md")


class TestCallWithRetry:
    def test_returns_the_first_success(self, no_retry_sleep: list[float]) -> None:
        assert session_llm.call_with_retry(lambda a, b: a + b, 2, 3) == 5
        assert no_retry_sleep == []

    def test_retries_a_retryable_error(self, no_retry_sleep: list[float]) -> None:
        calls: list[int] = []

        def flaky() -> str:
            calls.append(1)
            if len(calls) < 3:
                raise RetryableError("503")
            return "ok"

        assert session_llm.call_with_retry(flaky) == "ok"
        assert len(calls) == 3
        assert len(no_retry_sleep) == 2

    def test_a_non_retryable_error_is_final(self, no_retry_sleep: list[float]) -> None:
        def broken() -> str:
            raise ValueError("bad request")

        with pytest.raises(CannotRetryError, match="bad request"):
            session_llm.call_with_retry(broken)
        assert no_retry_sleep == []

    def test_the_ceiling_is_with_retrys(self, no_retry_sleep: list[float]) -> None:
        calls: list[int] = []

        def always() -> str:
            calls.append(1)
            raise RetryableError("503")

        with pytest.raises(CannotRetryError):
            session_llm.call_with_retry(always)
        assert len(calls) == DEFAULT_MAX_RETRIES + 1

    def test_custom_sleep_and_model(self) -> None:
        slept: list[float] = []
        calls: list[int] = []

        async def sleep(ms: float) -> None:
            slept.append(ms)

        def flaky() -> str:
            calls.append(1)
            if len(calls) == 1:
                raise RetryableError("503")
            return "ok"

        assert session_llm.call_with_retry(flaky, model="m", sleep=sleep) == "ok"
        assert len(slept) == 1

    def test_run_sync_outside_a_loop(self) -> None:
        async def value() -> int:
            return 7

        assert session_llm.run_sync(value()) == 7

    def test_run_sync_inside_a_running_loop(self) -> None:
        async def value() -> int:
            return 8

        async def outer() -> int:
            return session_llm.run_sync(value())

        assert asyncio.run(outer()) == 8
