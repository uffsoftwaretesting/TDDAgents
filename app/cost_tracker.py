"""
The run's cost and usage totals (Phase 6).

Ported from `reference/claude-code/src/cost-tracker.ts` -> `addToTotalSessionCost`,
`addToTotalModelUsage`, `formatCost`, `formatTotalCost`, over the counters upstream keeps in
`bootstrap/state.ts` (`getTotalCostUSD`, `getModelUsage`, `getTotalAPIDuration`,
`hasUnknownModelCost`).

Upstream's totals are session-global state fed by `claude.ts` after each API response. Here
a session is a run, so the totals are a `CostTracker` per run, carried on
`ToolContext.cost_tracker` (a worker inherits it: its API calls count toward the run, as a
subagent's count toward upstream's session) and fed as a LangChain callback on the model
call: `on_llm_end` sees the whole response's `usage_metadata`, streamed or not. The framed
per-block messages the loop receives are not read — framing does not preserve usage.

LangChain's `input_tokens` *includes* cache reads and writes; Anthropic's excludes them.
`usage_from_langchain` converts to Anthropic's shape, which is the one prices are quoted in.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from app.utils.model_cost import Usage, calculate_usd_cost


@dataclass
class ModelUsage:
    """`ModelUsage`: one model's accumulated tokens and cost."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    web_search_requests: int = 0
    cost_usd: float = 0.0


def usage_from_langchain(usage: Mapping[str, Any] | None) -> Usage | None:
    """A LangChain `usage_metadata` in Anthropic's shape: uncached input split from the cache."""
    if not usage:
        return None
    details = usage.get("input_token_details") or {}
    read = int(details.get("cache_read") or 0)
    create = int(details.get("cache_creation") or 0)
    return Usage(
        input_tokens=int(usage.get("input_tokens") or 0) - read - create,
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_input_tokens=read,
        cache_creation_input_tokens=create,
    )


def _response_message(response: LLMResult) -> Any:
    generations = response.generations
    if not generations or not generations[0]:
        return None
    return getattr(generations[0][0], "message", None)


class CostTracker(BaseCallbackHandler):
    """One run's totals; `model` names who answered when a response does not say."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.total_cost_usd = 0.0
        self.total_api_duration_ms = 0.0
        self.has_unknown_model_cost = False
        self._model_usage: dict[str, ModelUsage] = {}
        self._started: dict[UUID, float] = {}

    def add_to_total_session_cost(self, usage: Usage, model: str) -> float:
        """`addToTotalSessionCost` + `addToTotalModelUsage`: returns the cost added."""
        cost, known = calculate_usd_cost(model, usage)
        if not known:
            self.has_unknown_model_cost = True
        entry = self._model_usage.setdefault(model, ModelUsage())
        entry.input_tokens += usage.input_tokens
        entry.output_tokens += usage.output_tokens
        entry.cache_read_input_tokens += usage.cache_read_input_tokens
        entry.cache_creation_input_tokens += usage.cache_creation_input_tokens
        entry.web_search_requests += usage.web_search_requests
        entry.cost_usd += cost
        self.total_cost_usd += cost
        return cost

    def model_usage(self) -> dict[str, ModelUsage]:
        """`getModelUsage`."""
        return dict(self._model_usage)

    def total_usage(self) -> dict[str, int]:
        """The run's tokens summed over every model (`QueryEngine.totalUsage`'s token fields)."""
        entries = self._model_usage.values()
        return {
            "input_tokens": sum(e.input_tokens for e in entries),
            "output_tokens": sum(e.output_tokens for e in entries),
            "cache_read_input_tokens": sum(e.cache_read_input_tokens for e in entries),
            "cache_creation_input_tokens": sum(e.cache_creation_input_tokens for e in entries),
        }

    def _start(self, run_id: UUID) -> None:
        self._started[run_id] = time.monotonic()

    def _finish(self, run_id: UUID) -> None:
        started = self._started.pop(run_id, None)
        if started is not None:
            self.total_api_duration_ms += (time.monotonic() - started) * 1000

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], *, run_id: UUID, **kwargs: Any) -> None:
        self._start(run_id)

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[Any]], *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._start(run_id)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id)
        message = _response_message(response)
        usage = usage_from_langchain(getattr(message, "usage_metadata", None))
        if usage is None:
            return
        metadata = getattr(message, "response_metadata", None) or {}
        model = metadata.get("model_name") or metadata.get("model") or self.model
        self.add_to_total_session_cost(usage, str(model))


def format_cost(cost: float, max_decimal_places: int = 4) -> str:
    """`formatCost`: two decimals above half a dollar, four below."""
    return f"${cost:.2f}" if cost > 0.5 else f"${cost:.{max_decimal_places}f}"


def format_total_cost(tracker: CostTracker) -> str:
    """`formatTotalCost`'s cost line, with upstream's unknown-model caveat."""
    caveat = " (costs may be inaccurate due to usage of unknown models)" if tracker.has_unknown_model_cost else ""
    return f"Total cost: {format_cost(tracker.total_cost_usd)}{caveat}"


__all__ = ["CostTracker", "ModelUsage", "format_cost", "format_total_cost", "usage_from_langchain"]
