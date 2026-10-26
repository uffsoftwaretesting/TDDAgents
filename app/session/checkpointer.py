"""
Checkpointer factory conforming to §3.4 and Part K1.

Supports:
- In-memory checkpointing via MemorySaver (for unit tests, dry-runs, and local dev)
- Persistent checkpointing via PostgresSaver (for production cross-restart resume)
- Automatic fallback on connection error

The entry point checkpoints to `Config.POSTGRES_URL` by default (`--checkpoint memory` opts
out) and records whether the connection actually held in `Gates.postgres_checkpointing`
(`is_persistent_checkpointer`): a resumable run and an unresumable one are not the same
experiment, so the fallback is a recorded fact, not only a log line.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)


def build_checkpointer(checkpoint_uri: str | None = None) -> BaseCheckpointSaver[Any]:
    """
    Build a LangGraph checkpointer for session state persistence.

    - Returns `MemorySaver()` if `checkpoint_uri` is None, empty, or ':memory:'.
    - Attempts `PostgresSaver(conn)` if a postgres URI is given.
    - Falls back to `MemorySaver()` gracefully if Postgres connection fails.
    """
    if not checkpoint_uri or checkpoint_uri.strip().lower() in (":memory:", "memory", ""):
        return MemorySaver()

    uri = checkpoint_uri.strip()
    if uri.startswith(("postgresql://", "postgres://")):
        try:
            import psycopg
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg.rows import dict_row

            conn = psycopg.connect(uri, autocommit=True, row_factory=dict_row)
            saver = PostgresSaver(conn)
            # Setup tables if needed
            if hasattr(saver, "setup"):
                saver.setup()
            logger.info("Configured PostgresSaver checkpointer for URI: %s", uri.split("@")[-1])
            return saver
        except Exception as exc:  # noqa: BLE001 - any connection failure falls back to memory, logged
            logger.warning(
                "Failed to initialize PostgresSaver (%s). Falling back to MemorySaver.",
                exc,
            )
            return MemorySaver()

    logger.warning("Unrecognized checkpointer URI '%s'. Falling back to MemorySaver.", checkpoint_uri)
    return MemorySaver()


def is_persistent_checkpointer(checkpointer: BaseCheckpointSaver[Any]) -> bool:
    """Whether `checkpointer` survives the process: a Postgres saver, not the in-memory fallback."""
    return not isinstance(checkpointer, MemorySaver)
