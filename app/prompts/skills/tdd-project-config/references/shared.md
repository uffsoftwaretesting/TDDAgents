# The Project Configuration (every ecosystem)

## The plan block

The plan carries the configuration as one fenced `json` block under its own heading, next to `## Implementation Plan`:

````markdown
## {{PROJECT_CONFIG_HEADING}}

```json
{
  "version": {{PROJECT_CONFIG_VERSION}},
  "setup": "<command that builds the environment from the manifests>",
  "test": "<command that runs the whole suite and writes the JUnit report>",
  "testFiles": ["<glob>"],
  "testSupportFiles": ["<glob>"],
  "manifestFiles": ["<file>"],
  "exclude": ["<dir>/"]
}
```
````

On approval TDDAgents saves it as `{{PROJECT_CONFIG_FILE}}`. ExitPlanMode refuses a plan whose block is missing or invalid and lists every issue.

## Schema (version {{PROJECT_CONFIG_VERSION}})

Keys are camelCase. Unknown keys are invalid.

| Key | Required | Meaning |
|---|---|---|
| `version` | yes | The schema version, `{{PROJECT_CONFIG_VERSION}}`. |
| `setup` | no | A shell command, run from the project root, that builds the environment from the project's manifests alone: create a virtual environment and install the requirements, `npm ci`, `bundle install`, `composer install`, `dotnet restore`. Omit it when the test command needs nothing installed first. |
| `test` | yes | A shell command, run from the project root, that runs the **whole** suite and writes a JUnit XML report to the path in `${{JUNIT_REPORT_ENV}}` and, optionally, a coverage report to the path in `${{COVERAGE_REPORT_ENV}}`. |
| `junitReport` | no | The report's path, relative to the project root, when the runner cannot write to `${{JUNIT_REPORT_ENV}}` (it always writes to a fixed place). TDDAgents then reads that file. |
| `coverageReport` | no | `{"path": "<file>", "format": "cobertura"}` or `"lcov"`: the coverage report's format, and its path when the tool cannot write to `${{COVERAGE_REPORT_ENV}}`. Without it, coverage is not reported. |
| `testFiles` | yes | Gitignore-style globs matching **every** file of test code, helpers and fixtures included. |
| `testSupportFiles` | no | The subset of `testFiles` that never appears in a report as a test file: fixtures, shared helpers, the runner's own test configuration. |
| `manifestFiles` | no | Dependency and build manifests and lockfiles. They are writable in every TDD phase. |
| `exclude` | no | Environment, build and dependency directories (a virtual environment, `node_modules/`, `target/`): left out of TDDAgents' file walks and of the final run's clean copy. |

Globs follow `.gitignore` rules: a pattern without a slash matches at any depth (`*_test.go`), a trailing slash matches a directory and everything in it (`tests/`), a leading slash anchors to the project root (`/conftest.py`), and `**` crosses directories (`tests/**/fixtures/`).

## How TDDAgents judges a run

RunTests runs `test` as declared, with `${{JUNIT_REPORT_ENV}}` and `${{COVERAGE_REPORT_ENV}}` set, and reads the reports afterwards. It reads the exit code as a shell does:

- **Exit 0** is a pass only if the JUnit report agrees: the report exists, records at least one test, and records no failures, errors or skips. Exit 0 with no report, or with zero tests, is not evidence.
- **Exit 126 or 127** (not executable, command not found), **a signal (128 and above)** and **a timeout** are errors. They are not evidence, and the ledger does not change.
- **Any other non-zero exit** is a failed run. A load or compile error counts as a failure, which RED accepts: a test of code that does not exist yet may fail to compile.
- **A skipped test is not a pass.** Every test must run.
- **A test file that did not run is not a pass.** A file `testFiles` matches (and `testSupportFiles` does not) that the report does not name did not run. TDDAgents can check this only when the report names files (a `file` attribute on each `<testcase>`), so prefer a reporter option that writes it. When the report names no files, the check is unknown and RunTests says so.

## The final run

When the session ends, TDDAgents copies the project, leaving out `exclude`, version control and what `.gitignore` ignores, then runs `setup` and `test` in that clean copy, in an environment built from the manifests alone. A dependency installed during the session but missing from a manifest fails there, so declare every dependency, the test runner and the coverage tool included, in the project's own manifest. A tool that no manifest can declare (a system package, a compiler toolchain component) belongs in `setup`, with a pinned version.

## Rules for the globs

- `testFiles` must match every test file the runner collects, and nothing the production code needs: in RED only these files are writable, in GREEN they are locked.
- The runner's own configuration decides which tests run, so when it lives in a file of its own (`pytest.ini`, `vitest.config.ts`, `jest.config.js`, `.rspec`, `phpunit.xml`, `.config/nextest.toml`), declare it in `testFiles` and `testSupportFiles`. Configuration inside a manifest (`pyproject.toml`, the `"jest"` key of `package.json`) is a manifest, writable in every phase.
- `manifestFiles` lists files, not directories: the manifests and their lockfiles.
- `exclude` lists generated directories only. Never exclude source or test code.

## Not supported

Tests inside production files, such as Rust `#[cfg(test)]` modules or doctests: they cannot be told apart from production code, so the phase rules cannot apply to them. Put every test in a separate file.
