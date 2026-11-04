"""
The product's command line and version, as the texts that name them show them.

Stands where `reference/claude-code/src/constants/product.ts` keeps the product's names: where an
upstream text says `claude …`, this build's says `CLI_COMMAND …` (the update-config skill's
`claude --debug`, the plugin hints' `claude plugin`). `VERSION` stands for upstream's build-time
`MACRO.VERSION` (user decision 2026-11-04): the one place TDDAgents' version is written.
"""

from __future__ import annotations

#: The command that starts TDDAgents (upstream's `claude`), run from the repository root.
CLI_COMMAND = "python -m app.main"

#: `MACRO.VERSION`: TDDAgents' version, wherever it is shown or sent.
VERSION = "0.1.0"

__all__ = ["CLI_COMMAND", "VERSION"]
