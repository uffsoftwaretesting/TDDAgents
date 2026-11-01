"""
A plan with one current sub-requirement, for tests that exercise the TDD phase tier.

The tier guards only the current sub-requirement's cycle (user decision 2026-10-31): with an
empty or finished plan the normal permission rules govern. A test about phase rules therefore
needs a current item, not just a ledger.
"""

from __future__ import annotations

from app.tdd.plan import TddPlan

#: One pending item: the phase tier applies.
CURRENT_ITEM_PLAN = TddPlan.from_texts(["the sub-requirement under test"])
