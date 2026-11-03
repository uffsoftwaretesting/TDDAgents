"""
The values TDD prompts name as placeholders (no values in prose, §4 of the plan).

Each value has one resolution point in code: the coverage rule of thumb (`app/tdd/coverage_gate.py`),
the project configuration's file, version, report variables, plan heading and skill
(`app/tdd/project_config.py`, `app/tdd/plan.py`). A prompt names them as `{{PLACEHOLDER}}`
(`app/utils/frontmatter_parser.py::render_prompt`).
"""

from __future__ import annotations

from app.tdd.coverage_gate import COVERAGE_RULE_OF_THUMB
from app.tdd.plan import IMPLEMENTATION_PLAN_HEADING, PROJECT_CONFIG_HEADING
from app.tdd.project_config import (
    COVERAGE_REPORT_ENV,
    JUNIT_REPORT_ENV,
    PROJECT_CONFIG_FILE,
    PROJECT_CONFIG_SKILL,
    PROJECT_CONFIG_VERSION,
)

#: The project configuration's values (2026-11-03), under the names the `tdd-project-config` skill uses.
PROJECT_CONFIG_PROMPT_VARS: dict[str, str] = {
    "JUNIT_REPORT_ENV": JUNIT_REPORT_ENV,
    "COVERAGE_REPORT_ENV": COVERAGE_REPORT_ENV,
    "PROJECT_CONFIG_HEADING": PROJECT_CONFIG_HEADING,
    "PROJECT_CONFIG_FILE": PROJECT_CONFIG_FILE,
    "PROJECT_CONFIG_VERSION": str(PROJECT_CONFIG_VERSION),
    "PROJECT_CONFIG_SKILL": PROJECT_CONFIG_SKILL,
}

TDD_PROMPT_VARS: dict[str, str] = {
    "COVERAGE_RULE_OF_THUMB": f"{COVERAGE_RULE_OF_THUMB:.0f}",
    "IMPLEMENTATION_PLAN_HEADING": IMPLEMENTATION_PLAN_HEADING,
    **PROJECT_CONFIG_PROMPT_VARS,
}

__all__ = ["PROJECT_CONFIG_PROMPT_VARS", "TDD_PROMPT_VARS"]
