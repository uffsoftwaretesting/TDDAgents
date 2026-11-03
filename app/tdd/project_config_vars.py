"""
The values the `tdd-project-config` skill's Markdown names, each resolved here once.

No value appears in a prompt file (CLAUDE.md, "No values in prose"): the skill's prompt and
references name them as `{{PLACEHOLDER}}`s, rendered from `PROJECT_CONFIG_PROMPT_VARS`.

These are the declared project configuration's names: the environment variables RunTests and the
final run set to the report paths a project's test command writes, the plan section that carries
the configuration, and the file and schema version it is saved as.
"""

from __future__ import annotations

#: The environment variable holding the path the test command writes its JUnit XML report to.
JUNIT_REPORT_ENV = "TDDAGENTS_JUNIT_REPORT"
#: The environment variable holding the path the test command may write its coverage report to.
COVERAGE_REPORT_ENV = "TDDAGENTS_COVERAGE_REPORT"
#: The plan heading whose one fenced `json` block is the project configuration.
PROJECT_CONFIG_HEADING = "Project Configuration"
#: Where the approved configuration is saved, relative to the project.
PROJECT_CONFIG_FILE = ".tddagents/project.json"
#: The configuration schema's `version`.
PROJECT_CONFIG_VERSION = 1

PROJECT_CONFIG_PROMPT_VARS: dict[str, str] = {
    "JUNIT_REPORT_ENV": JUNIT_REPORT_ENV,
    "COVERAGE_REPORT_ENV": COVERAGE_REPORT_ENV,
    "PROJECT_CONFIG_HEADING": PROJECT_CONFIG_HEADING,
    "PROJECT_CONFIG_FILE": PROJECT_CONFIG_FILE,
    "PROJECT_CONFIG_VERSION": str(PROJECT_CONFIG_VERSION),
}

__all__ = [
    "COVERAGE_REPORT_ENV",
    "JUNIT_REPORT_ENV",
    "PROJECT_CONFIG_FILE",
    "PROJECT_CONFIG_HEADING",
    "PROJECT_CONFIG_PROMPT_VARS",
    "PROJECT_CONFIG_VERSION",
]
