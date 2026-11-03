"""
Applying a settings change to a running session.

Ported from `reference/claude-code/src/utils/settings/applySettingsChange.ts` ->
`applySettingsChange`.

The settings change detector (`app/utils/settings/change_detector.py`) calls this for every
change it lets through, in the REPL and in `-p` / stream-json runs alike (upstream's
`useSettingsChange` in `AppState.tsx` and `print.ts`' direct subscription): the permission rules
are re-read from disk (`loadAllPermissionRulesFromDisk`), the hooks snapshot is recaptured
(`updateHooksConfigSnapshot`), and the run's permission context gets the new rules
(`syncPermissionRulesFromDisk`); when settings now disable bypass mode, a context in it leaves it
(`createDisabledBypassPermissionsContext`). The permission mode itself, the CLI and session rules
and the additional directories are kept, as upstream keeps them.

**TDD managed tier:** where upstream re-strips the overly broad Bash allows for its internal
users only, here `strip_dangerous_permissions` runs on every reloaded context, so an interpreter or
Agent allow written into an editable settings file mid-session is stripped exactly as at startup.
The phase permission tier lives in code, keyed on the ledger, and no reload reaches it.

*Divergences:* `transitionPlanAutoMode` is not applied (the `TRANSCRIPT_CLASSIFIER` plan-mode
branches are not ported, `app/utils/permissions/permission_setup.py`); `AppState` has no
`settings` copy or `effortValue` (every reader loads the settings files when it needs them), so
there is nothing else to refresh, and `onChangeAppState`'s side effects (auth caches, `env`) have
no counterpart.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from app.state.app_state_store import AppState
from app.types.permissions import PermissionRuleSource
from app.utils.debug import log_for_debugging
from app.utils.hooks.hooks_config_snapshot import update_hooks_config_snapshot
from app.utils.permissions.dangerous_patterns import strip_dangerous_permissions
from app.utils.permissions.permission_setup import create_disabled_bypass_permissions_context
from app.utils.permissions.permission_update import sync_permission_rules_from_disk
from app.utils.settings.settings import (
    SettingsLocations,
    get_initial_settings,
    is_bypass_permissions_mode_disabled,
    load_all_permission_rules_from_disk,
)


def apply_settings_change(
    source: PermissionRuleSource,
    set_app_state: Callable[[Callable[[AppState], AppState]], Any],
    locations: SettingsLocations,
    hook_dispatcher: Any = None,
) -> None:
    """`applySettingsChange`: the settings re-read, then the run's rules and hooks replaced."""
    new_settings = get_initial_settings(locations)
    log_for_debugging(f"Settings changed from {source}, updating app state")
    updated_rules = load_all_permission_rules_from_disk(locations)
    if hook_dispatcher is not None:
        update_hooks_config_snapshot(hook_dispatcher)

    def update(prev: AppState) -> AppState:
        context = sync_permission_rules_from_disk(prev.tool_permission_context, updated_rules, locations)
        context = strip_dangerous_permissions(context)
        if context.is_bypass_permissions_mode_available and is_bypass_permissions_mode_disabled(new_settings):
            context = create_disabled_bypass_permissions_context(context)
        return replace(prev, tool_permission_context=context)

    set_app_state(update)


__all__ = ["apply_settings_change"]
