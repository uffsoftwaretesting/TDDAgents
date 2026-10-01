"""
Agent definition data model conforming to §4.5 and Part I1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.loop.ledger import TddPhase


class AgentFrontmatterError(ValueError):
    """Raised when an agent definition carries an invalid load-breaking field (such as phase)."""


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """
    Parsed and validated agent definition loaded from an AGENT.md file.

    Follows §4.5 rules:
    - Omission means unset (None).
    - Invalid fields are logged and ignored (except phase which raises AgentFrontmatterError).
    - Enumerations validate against explicit lists.
    - inherit is a sentinel.
    """

    name: str
    description: str
    prompt: str
    phase: TddPhase | None = None
    tools: tuple[str, ...] | None = None
    disallowed_tools: tuple[str, ...] | None = None
    permission_mode: str | None = None
    memory: str | None = None
    fork_from: str | None = None
    revert_on_red: bool | None = None
    hooks: dict[str, Any] | None = None
    model: str | None = None
    background: bool | None = None
    source: str = "built-in"  # "built-in", "project", "user", "synthetic"
    base_dir: str = ""
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)
