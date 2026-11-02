"""`start_plugin_checks`: the REPL's background `performStartupChecks`, behind the folder's trust."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.session.driver import start_plugin_checks
from app.utils.settings.settings import SettingsLocations


def test_startup_checks_run_with_the_runs_state_trust_and_refresh(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.utils.config as config_mod
    import app.utils.plugins.perform_startup_checks as checks_mod

    locations = SettingsLocations(project_root=tmp_path / "project", home=tmp_path / "home")
    calls: list[tuple[Any, ...]] = []

    async def checks(set_app_state: Any, locs: Any, *, trust_accepted: bool, refresh: Any) -> None:
        calls.append((set_app_state, locs, trust_accepted, refresh))

    monkeypatch.setattr(checks_mod, "perform_startup_checks", checks)
    monkeypatch.setattr(config_mod, "check_has_trust_dialog_accepted", lambda project: project == tmp_path / "project")

    async def refresh_plugins() -> None:
        pass

    def update(_: Any) -> None:
        pass

    run = SimpleNamespace(mcp=SimpleNamespace(settings_locations=lambda: locations),
                          store=SimpleNamespace(update=update), refresh_plugins=refresh_plugins)

    async def scenario() -> None:
        start_plugin_checks(run)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert calls == [(update, locations, True, refresh_plugins)]
