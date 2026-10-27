"""Phase 5: the runner attaches the user-input attachments before the first model call
(upstream `processUserInput` -> `getAttachmentMessages` with the input), persisted in state."""

from __future__ import annotations

import asyncio
import dataclasses

from langchain_core.messages import AIMessage

from app.query.deps import get_production_deps
from tests.session_run_helpers import run_item


def test_the_first_turn_carries_the_initial_tdd_state(tmp_path, make_fake_workspace):
    seen: list = []

    async def model(state, config):
        seen.append(state.messages)
        yield AIMessage(content="done")

    deps = dataclasses.replace(get_production_deps(), call_model=model)
    asyncio.run(run_item("Add two numbers", 0, {"session_id": "s"}, workspace=make_fake_workspace(),
                         deps=deps, project_root=tmp_path, home=tmp_path / "home",
                         managed_dir=tmp_path / "etc"))
    first = seen[0]
    prompt_index = next(i for i, m in enumerate(first) if m.content == "Add two numbers")
    kinds = [((m.additional_kwargs or {}).get("attachment") or {}).get("type") for m in first[prompt_index + 1:]]
    assert "tdd_state" in kinds
