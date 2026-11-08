"""
A plan with one current sub-requirement, for tests that exercise the TDD phase tier.

The tier guards only the current sub-requirement's cycle (user decision 2026-10-31): with an
empty or finished plan the normal permission rules govern. A test about phase rules therefore
needs a current item, not just a ledger.
"""

from __future__ import annotations

import json

from app.tdd.plan import TddPlan
from app.tdd.project_config import CoverageReportConfig, ProjectConfig

#: One pending item: the phase tier applies.
CURRENT_ITEM_PLAN = TddPlan.from_texts(["the sub-requirement under test"])

#: The environment variable naming the interpreter of the shared test venv (`tests/conftest.py`,
#: `python_workspace` sets it): the declared test command runs pytest with it.
TEST_PYTHON_ENV = "TDDAGENTS_TEST_PYTHON"

#: A pytest project's declared test command (2026-11-03): the whole suite, a JUnit report naming each
#: case's file (`xunit1`) and a cobertura coverage report, at the paths RunTests passes in the environment.
PYTEST_COMMAND = (
    f'"${TEST_PYTHON_ENV}" -m pytest -p no:cacheprovider -o junit_family=xunit1 '
    '--junitxml="$TDDAGENTS_JUNIT_REPORT" --cov=. --cov-branch --cov-report=term-missing '
    '--cov-report=xml:"$TDDAGENTS_COVERAGE_REPORT"'
)

#: A declared configuration whose test patterns are the ones TDDAgents used to hard-code
#: (`tests/` and `test/` directories, `test_*.py`, `*_test.py`): the phase tier, the fingerprint and
#: RunTests read which files are tests from the project's configuration (2026-11-03). As the
#: `tdd-project-config` skill's contract says (`references/shared.md`), `testFiles` matches every file
#: of test code and `testSupportFiles` is the subset that never appears in a report (2026-11-08).
TEST_PROJECT_CONFIG = ProjectConfig(
    test=PYTEST_COMMAND,
    test_files=("tests/", "test/", "test_*.py", "*_test.py", "conftest.py"),
    test_support_files=("conftest.py", "__init__.py", "fixtures/", "helpers/"),
    manifest_files=("requirements.txt",),
    exclude=("__pycache__/", ".venv/", ".pytest_cache/"),
    coverage_report=CoverageReportConfig(format="cobertura"),
)

#: The plan-file section declaring `TEST_PROJECT_CONFIG`: ExitPlanMode refuses a plan without one.
PROJECT_CONFIG_SECTION = (
    "\n## Project Configuration\n\n```json\n" + json.dumps(TEST_PROJECT_CONFIG.to_dict(), indent=2) + "\n```\n"
)
