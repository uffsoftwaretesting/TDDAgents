"""
The once-per-run snapshot.

Two things are worth asserting: that the gates are resolved from where the docstring says
they are, and that no ceiling crept into the record. The second one is a test about an
absence, which is unusual, but the absence was a decision.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from app.config.config import Config
from app.loop.config import Gates, RunConfig, build_run_config


class TestBuildRunConfig:
    def test_the_run_id_is_the_callers(self):
        """Not minted here: K1 derives it from the spec so a run can be resumed."""
        assert build_run_config("tdd-abc123", postgres_checkpointing=True).run_id == "tdd-abc123"

    def test_web_tools_are_available_when_the_key_is_configured(self, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "tvly-configured")
        assert build_run_config("r", postgres_checkpointing=False).gates.web_tools_available is True

    def test_web_tools_are_unavailable_when_the_key_is_unset(self, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", None)
        assert build_run_config("r", postgres_checkpointing=False).gates.web_tools_available is False

    def test_an_empty_key_is_not_a_key(self, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "")
        assert build_run_config("r", postgres_checkpointing=False).gates.web_tools_available is False

    @pytest.mark.parametrize("live", [True, False])
    def test_checkpointing_is_reported_by_the_caller_that_attempted_it(self, live):
        config = build_run_config("r", postgres_checkpointing=live)
        assert config.gates.postgres_checkpointing is live

    def test_checkpointing_must_be_stated(self):
        with pytest.raises(TypeError):
            build_run_config("r")  # type: ignore[call-arg]


class TestTheSnapshotIsImmutable:
    def test_the_config_is_frozen(self):
        config = build_run_config("r", postgres_checkpointing=True)
        with pytest.raises(FrozenInstanceError):
            config.run_id = "other"  # type: ignore[misc]

    def test_the_gates_are_frozen(self):
        """A gate that could flip mid-run would defeat the reason for snapshotting it."""
        gates = Gates(web_tools_available=True, postgres_checkpointing=True)
        with pytest.raises(FrozenInstanceError):
            gates.web_tools_available = False  # type: ignore[misc]


class TestNoCeilings:
    def test_the_config_holds_identity_and_gates_and_nothing_else(self):
        assert [f.name for f in fields(RunConfig)] == ["run_id", "gates"]

    def test_no_gate_is_a_number(self):
        """
        Gates are facts about the environment. The moment one is an int it is a budget,
        and this record is where a ceiling would most plausibly be re-added by accident.
        """
        gates = build_run_config("r", postgres_checkpointing=True).gates
        for field_ in fields(Gates):
            assert isinstance(getattr(gates, field_.name), bool), field_.name
