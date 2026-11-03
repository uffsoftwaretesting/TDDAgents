<!-- The prompt of the code-built `tdd-project-config` skill (app/tdd/project_config_skill.py).
     The code keeps the text before `## Reading Guide`, then either the reading guide (a language
     is known) or `## No Language Detected`, the inlined references, and `## Common Pitfalls`. -->
# TDD Project Configuration

TDDAgents runs every TDD phase through the project's own test command and judges the cycle from the JUnit XML report that command writes. The project tells TDDAgents how to build its environment and run its suite in a configuration you propose in the plan, under a `## {{PROJECT_CONFIG_HEADING}}` heading holding one fenced `json` block. When the user approves the plan, TDDAgents saves that block as `{{PROJECT_CONFIG_FILE}}`; you cannot edit that file afterwards, so the plan is the place to get it right.

## What to Produce

One `json` block under `## {{PROJECT_CONFIG_HEADING}}`, next to the plan's `## Implementation Plan`:

- `setup`: the command that builds the environment from the project's manifests alone.
- `test`: the command that runs the **whole** suite from the project root and writes a JUnit XML report to `${{JUNIT_REPORT_ENV}}` (and, when it can, a coverage report to `${{COVERAGE_REPORT_ENV}}`).
- The globs that tell TDDAgents which files are tests, test support, manifests, and environment directories.

Choose the ecosystem's usual test runner and coverage tool, and declare both in the project's own manifest as (development) dependencies. Prefer a runner option that records each test's source file in the report. Declare a Cobertura or LCOV coverage report (`coverageReport`): RunTests then reports the coverage on every run, which shows the untested code and helps raise quality ({{COVERAGE_RULE_OF_THUMB}}% is a good rule of thumb, not a gate).

## Reading Guide

The documentation for the `{{LANGUAGE}}` ecosystem is included below in `<doc>` tags. Each tag has a `path` attribute showing its original file path:

- **The schema, TDDAgents' rules, and the plan block's form:** `references/shared.md`
- **A complete example configuration, runner and coverage options, globs, and pitfalls:** `references/{{LANGUAGE}}.md`

Start from the example, adapt the paths and package names to this project, and keep every rule in `shared.md`.

## No Language Detected

No project language was given or detected (the project may still be empty). Use the language the user's request names; if it names none, ask the user which one to use. Then invoke this skill again with that ecosystem as its argument. The available ecosystems:

{{ECOSYSTEMS}}

The schema and TDDAgents' rules, which hold for every ecosystem, are included below.

## Common Pitfalls

- A test command that runs only part of the suite: RunTests always runs the declared command, so it must collect every test file.
- A dependency installed by hand during the session but missing from the manifest: the final run builds a clean copy from the manifests alone, so the suite fails there.
- A JUnit report written somewhere other than `${{JUNIT_REPORT_ENV}}` without a `junitReport` override: TDDAgents finds no report, and a run without one is not evidence.
- A reporter that only prints to the terminal: TDDAgents reads the XML report, never the console output, to decide a pass.
- A runner that writes one JUnit file per test class (Maven Surefire, Gradle, Minitest's reporter) with no join step: TDDAgents reads a single report file, so the test command must join them into one.
- Tests inside production files (Rust `#[cfg(test)]` modules, doctests): they are not supported. Put tests in separate files that `testFiles` matches.
