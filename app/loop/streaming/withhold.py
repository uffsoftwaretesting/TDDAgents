"""
Withhold-then-decide recovery gating (Part F5).

Ported from `reference/claude-code/src/query.ts` -> lines 799-825 & 1070-1256.

Invariant F5:
Hoist recovery gate before the stream loop so the withholding decision and the recovery
decision read the SAME snapshot. A value flipping mid-run must never cause an error to
be withheld from emission and then silently dropped during post-stream recovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.loop.messages import Message

if TYPE_CHECKING:
    from app.loop.config import RunConfig
    from app.loop.state import LoopState


@dataclass(frozen=True, slots=True)
class WithholdGateSnapshot:
    """
    Immutable snapshot of recovery capabilities latched before the model stream begins.
    """

    reactive_compact_enabled: bool
    context_collapse_enabled: bool
    media_recovery_enabled: bool
    max_output_tokens_recovery_enabled: bool
    max_output_tokens_escalate_enabled: bool


def take_withhold_gate_snapshot(
    state: "LoopState", config: "RunConfig"
) -> WithholdGateSnapshot:
    """
    Latch the recovery gate values into an immutable record before streaming starts.
    """
    # Reactive compact is available if not already attempted in this turn
    reactive_compact_ok = not state.has_attempted_reactive_compact

    # Max output tokens recovery is enabled by default in the loop
    max_tokens_ok = True

    return WithholdGateSnapshot(
        reactive_compact_enabled=reactive_compact_ok,
        context_collapse_enabled=False,
        media_recovery_enabled=False,
        max_output_tokens_recovery_enabled=max_tokens_ok,
        max_output_tokens_escalate_enabled=False,
    )


def _get_api_error(message: Message) -> str | None:
    api_err = getattr(message, "api_error", None)
    if isinstance(api_err, str):
        return api_err
    additional = getattr(message, "additional_kwargs", None)
    if isinstance(additional, dict):
        val = additional.get("api_error")
        if isinstance(val, str):
            return val
    return None


def is_prompt_too_long(message: Message) -> bool:
    """
    Check if a message represents an API 413 / prompt-too-long error.
    """
    err = _get_api_error(message)
    if err in ("prompt_too_long", "context_length_exceeded"):
        return True

    status_code: Any = getattr(message, "status_code", None)
    if status_code == 413:
        return True

    additional = getattr(message, "additional_kwargs", None)
    if isinstance(additional, dict) and additional.get("status_code") == 413:
        return True

    content = getattr(message, "content", None)
    if isinstance(content, str) and (
        "prompt is too long" in content.lower()
        or "maximum context length" in content.lower()
        or "exceeds token limit" in content.lower()
    ):
        return True

    return False


def is_max_output_tokens(message: Message) -> bool:
    """
    Check if a message represents a model output token truncation.
    """
    err = _get_api_error(message)
    if err == "max_output_tokens":
        return True

    additional = getattr(message, "additional_kwargs", None)
    if isinstance(additional, dict):
        finish_reason = additional.get("finish_reason")
        if finish_reason in ("length", "max_tokens"):
            return True

    response_meta = getattr(message, "response_metadata", None)
    if isinstance(response_meta, dict):
        finish_reason = response_meta.get("finish_reason")
        if finish_reason in ("length", "max_tokens"):
            return True

    return False


def is_media_size_error(message: Message) -> bool:
    """
    Check if a message represents an oversized media error (images, PDFs).
    """
    err = _get_api_error(message)
    if err in ("image_error", "media_size_error", "media_too_large"):
        return True

    content = getattr(message, "content", None)
    if isinstance(content, str) and "image exceeds" in content.lower():
        return True

    return False


def is_withheld_error(message: Message, gate: WithholdGateSnapshot) -> bool:
    """
    Determine if an incoming stream message should be withheld from caller emission
    so that recovery systems can attempt a transparent retry.
    """
    if is_prompt_too_long(message):
        return gate.reactive_compact_enabled or gate.context_collapse_enabled

    if is_media_size_error(message):
        return gate.media_recovery_enabled

    if is_max_output_tokens(message):
        return (
            gate.max_output_tokens_recovery_enabled
            or gate.max_output_tokens_escalate_enabled
        )

    return False
