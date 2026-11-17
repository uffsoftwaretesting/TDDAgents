"""
The REPL's colours, in one place.

Ported from `reference/claude-code/src/utils/theme.ts` (the dark theme): `success`, `warning`, `error`,
`inactive` (what `dimColor` renders), `subtle`, `permission` and `planMode`, as rich colour strings. The
brand colour is TDDAgents' own (user decision 2026-11-17): a light green, TDD's Green, where upstream
uses `claude` orange `rgb(215,119,87)`; the dialogs' `permission` blue and the plan-mode sage take it too,
so every box and highlight shares one colour.
"""

from __future__ import annotations

#: TDDAgents' brand colour: light green.
BRAND = "rgb(134,239,172)"
#: `theme.success`.
SUCCESS = "rgb(78,186,101)"
#: `theme.warning`.
WARNING = "rgb(255,193,7)"
#: `theme.error`.
ERROR = "rgb(255,107,128)"
#: `theme.inactive`, what `dimColor` text renders in upstream. Not used for text here: TDDAgents' text is
#: `TEXT`, crisp on any background (user decision 2026-11-17).
INACTIVE = "rgb(153,153,153)"
#: The terminal's own foreground: crisp white on a dark background, crisp black on a light one (rich emits
#: no colour for it). Every text the REPL styles uses it; colour is kept for the marks.
TEXT = "default"
#: `theme.subtle`, the plan's dashed rules.
SUBTLE = "rgb(80,80,80)"
#: `theme.permission` and `theme.planMode`, both the brand green here (see the module docstring).
PERMISSION = BRAND
PLAN_MODE = BRAND

__all__ = ["BRAND", "ERROR", "INACTIVE", "PERMISSION", "PLAN_MODE", "SUBTLE", "SUCCESS", "TEXT", "WARNING"]
