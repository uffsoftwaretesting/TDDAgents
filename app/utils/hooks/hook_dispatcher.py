"""
Building a run's hook dispatcher from its settings.

The production assembly of `HookDispatcher`: the config snapshot is captured from the same
settings files as the permission rules (`captureHooksConfigSnapshot` at startup), and the
three executors a non-command hook needs are bound to the run — the prompt hook and the
agent hook to the run's model as their fallback (`getSmallFastModel` first, see
`utils/hooks/exec_prompt_hook.py`), the HTTP hook to the settings' URL and env-var allowlists.

Every production piece is injectable, so a test builds a dispatcher with fakes and no model,
network or subprocess it did not ask for.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from app.types.permissions import PermissionRuleSource
from app.utils.hooks.async_hook_registry import AsyncHookRegistry
from app.utils.hooks.exec_agent_hook import (
    AgentHookRunner,
    build_agent_hook_runner,
    exec_agent_hook,
)
from app.utils.hooks.exec_http_hook import (
    exec_http_hook,
    http_hook_policy_from_settings,
)
from app.utils.hooks.exec_prompt_hook import (
    PromptModelCaller,
    build_prompt_model_caller,
    exec_prompt_hook,
)
from app.utils.hooks.hooks import HookDispatcher
from app.utils.hooks.hooks_config_snapshot import capture_hooks_config_snapshot
from app.utils.settings.settings import (
    SettingsLocations,
    get_settings_for_source,
    load_settings,
)


def build_hook_dispatcher(
    locations: SettingsLocations,
    *,
    session_id: str,
    cwd: str,
    project_dir: str,
    model: str,
    transcript_path: str = "",
    prompt_model_caller: PromptModelCaller | None = None,
    agent_runner: AgentHookRunner | None = None,
    http_transport: Any = None,
) -> HookDispatcher:
    """The run's dispatcher, with production executors unless fakes are injected."""
    snapshot = capture_hooks_config_snapshot(locations)
    policy_settings = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations)
    http_policy = http_hook_policy_from_settings(load_settings(locations), policy_settings)
    caller = prompt_model_caller if prompt_model_caller is not None else build_prompt_model_caller()
    runner = agent_runner if agent_runner is not None else build_agent_hook_runner()
    return HookDispatcher(
        snapshot=snapshot,
        session_id=session_id,
        cwd=cwd,
        project_dir=project_dir,
        transcript_path=transcript_path,
        registry=AsyncHookRegistry(),
        prompt_executor=partial(exec_prompt_hook, model_caller=caller, default_model=model),
        agent_executor=partial(
            exec_agent_hook, agent_runner=runner, transcript_path=transcript_path, default_model=model
        ),
        http_executor=partial(exec_http_hook, policy=http_policy, transport=http_transport),
    )


__all__ = ["build_hook_dispatcher"]
