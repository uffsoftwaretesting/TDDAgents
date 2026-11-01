"""
Accept the camelCase `requestId` some SDK hosts send.

Ported from `reference/claude-code/src/utils/controlMessageCompat.ts` ->
`normalizeControlMessageKeys`: `requestId` becomes `request_id`, on the message and on its
`response`, unless the snake_case key is already there. Mutates in place and returns the input.
"""

from __future__ import annotations

from typing import Any


def normalize_control_message_keys(obj: Any) -> Any:
    """`normalizeControlMessageKeys`."""
    if not isinstance(obj, dict):
        return obj
    if "requestId" in obj and "request_id" not in obj:
        obj["request_id"] = obj.pop("requestId")
    response = obj.get("response")
    if isinstance(response, dict) and "requestId" in response and "request_id" not in response:
        response["request_id"] = response.pop("requestId")
    return obj


__all__ = ["normalize_control_message_keys"]
