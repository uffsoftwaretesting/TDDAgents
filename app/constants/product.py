"""
The product's command line, as the texts that tell the user to run it name it.

Stands where `reference/claude-code/src/constants/product.ts` keeps the product's names: where an
upstream text says `claude …`, this build's says `CLI_COMMAND …` (the update-config skill's
`claude --debug`, the plugin hints' `claude plugin`).
"""

from __future__ import annotations

#: The command that starts TDDAgents (upstream's `claude`), run from the repository root.
CLI_COMMAND = "python -m app.main"

__all__ = ["CLI_COMMAND"]
