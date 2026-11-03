"""
A fake POSIX-sh ecosystem (2026-11-03): a project TDDAgents knows nothing about, exercising the
language-generic core and the clean final run with no Python and no network.

* `deps.txt` is the manifest: one dependency name per line;
* `vendor/<name>/` is the local "registry" `setup.sh` installs from, into `env/` (excluded, like
  `node_modules/` or `.venv/`);
* `run-tests.sh` runs every `tests/test_*.sh` and writes a JUnit report naming each case's file to
  `$TDDAGENTS_JUNIT_REPORT`. A test script exiting 77 is a skip (automake's convention).
"""

from __future__ import annotations

from pathlib import Path

from app.tdd.project_config import ProjectConfig, save_project_config
from app.workspace.local import LocalWorkspace

SETUP_SH = """#!/bin/sh
mkdir -p env
for dep in $(cat deps.txt); do
  [ -d "vendor/$dep" ] || { echo "no such dependency: $dep"; exit 3; }
  cp -R "vendor/$dep" "env/$dep"
done
echo "installed: $(ls env | tr '\\n' ' ')"
"""

RUN_TESTS_SH = """#!/bin/sh
report="${TDDAGENTS_JUNIT_REPORT:-junit.xml}"
cases=""
tests=0
failures=0
skipped=0
for t in tests/test_*.sh; do
  [ -f "$t" ] || continue
  tests=$((tests + 1))
  out=$(sh "$t" 2>&1)
  code=$?
  file=" file=\\"$t\\""
  [ -n "$NO_FILES" ] && file=""
  if [ "$code" -eq 0 ]; then
    cases="$cases<testcase name=\\"$t\\"$file/>"
  elif [ "$code" -eq 77 ]; then
    skipped=$((skipped + 1))
    cases="$cases<testcase name=\\"$t\\"$file><skipped/></testcase>"
  else
    failures=$((failures + 1))
    cases="$cases<testcase name=\\"$t\\"$file><failure message=\\"exit $code\\"/></testcase>"
    echo "FAIL $t: $out"
  fi
done
mkdir -p "$(dirname "$report")"
printf '<testsuite tests="%s" failures="%s" skipped="%s">%s</testsuite>\\n' "$tests" "$failures" "$skipped" \\
  "$cases" > "$report"
echo "$tests tests, $failures failed, $skipped skipped"
[ "$failures" -eq 0 ]
"""

STRINGS_LIB = "upper() { printf '%s' \"$1\" | tr 'a-z' 'A-Z'; }\n"
GREET = "greet() { printf 'Hello, %s' \"$(upper \"$1\")\"; }\n"
GREET_TEST = ". ./env/strings/lib.sh\n. ./lib/greet.sh\n[ \"$(greet World)\" = \"Hello, WORLD\" ]\n"

SCRIPT_PROJECT_CONFIG = ProjectConfig(
    setup="sh setup.sh",
    test="sh run-tests.sh",
    test_files=("tests/test_*.sh",),
    test_support_files=("tests/fixtures/",),
    manifest_files=("deps.txt",),
    exclude=("env/",),
)


def make_script_project(root: Path, *, deps: str = "strings\n", config: ProjectConfig = SCRIPT_PROJECT_CONFIG,
                        install: bool = True) -> LocalWorkspace:
    """The project, its config approved, and (with `install`) its dependencies set up in the workspace."""
    ws = LocalWorkspace(root)
    for path, text in {"setup.sh": SETUP_SH, "run-tests.sh": RUN_TESTS_SH, "deps.txt": deps,
                       "vendor/strings/lib.sh": STRINGS_LIB, "lib/greet.sh": GREET,
                       "tests/test_greet.sh": GREET_TEST}.items():
        ws.write_file(path, text)
    save_project_config(ws, config)
    if install:
        ws.execute("mkdir -p env && cp -R vendor/strings env/strings")
    return ws


__all__ = ["SCRIPT_PROJECT_CONFIG", "make_script_project"]
