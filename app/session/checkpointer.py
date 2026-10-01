"""
Checkpointer factory conforming to §3.4 and Part K1.

Supports:
- In-memory checkpointing via MemorySaver (for unit tests, dry-runs, and local dev)
- Persistent checkpointing via PostgresSaver (for production cross-restart resume)
- Automatic fallback on connection error
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
            from psycopg.rows import dict_row
            from langgraph.checkpoint.postgres import PostgresSaver

            conn = psycopg.connect(uri, autocommit=True, row_factory=dict_row)
            saver = PostgresSaver(conn)
            # Setup tables if needed
            if hasattr(saver, "setup"):
                saver.setup()
            logger.info("Configured PostgresSaver checkpointer for URI: %s", uri.split("@")[-1])
            return saver
        except Exception as exc:
            logger.warning(
                "Failed to initialize PostgresSaver (%s). Falling back to MemorySaver.",
                exc,
            )
            return MemorySaver()

    logger.warning("Unrecognized checkpointer URI '%s'. Falling back to MemorySaver.", checkpoint_uri)
    return MemorySaver()
