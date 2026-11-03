"""
A plan with one current sub-requirement, for tests that exercise the TDD phase tier.

The tier guards only the current sub-requirement's cycle (user decision 2026-10-31): with an
empty or finished plan the normal permission rules govern. A test about phase rules therefore
needs a current item, not just a ledger.
"""

from __future__ import annotations

import json

from app.tdd.plan import TddPlan
from app.tdd.project_config import ProjectConfig

#: One pending item: the phase tier applies.
CURRENT_ITEM_PLAN = TddPlan.from_texts(["the sub-requirement under test"])

#: A declared configuration whose test-file patterns are the ones TDDAgents used to hard-code
#: (`tests/` and `test/` directories, `test_*.py`, `*_test.py`): the phase tier, the fingerprint and
#: RunTests read which files are tests from the project's configuration (2026-11-03).
TEST_PROJECT_CONFIG = ProjectConfig(
    test="sh run-tests.sh",
    test_files=("tests/", "test/", "test_*.py", "*_test.py"),
    exclude=("__pycache__/", ".venv/", ".pytest_cache/"),
)

#: The plan-file section declaring `TEST_PROJECT_CONFIG`: ExitPlanMode refuses a plan without one.
PROJECT_CONFIG_SECTION = (
    "\n## Project Configuration\n\n```json\n" + json.dumps(TEST_PROJECT_CONFIG.to_dict(), indent=2) + "\n```\n"
)
