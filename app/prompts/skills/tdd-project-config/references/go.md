# Go: go test through gotestsum

`go test` writes no JUnit XML, and its coverage profile is neither Cobertura nor LCOV. Two tools close the gap: `gotestsum` (`gotest.tools/gotestsum`) runs `go test` and writes the JUnit report, and `gocover-cobertura` (`github.com/boumenot/gocover-cobertura`) converts the coverage profile. Since Go 1.24 both can be declared in `go.mod` as tools (`go get -tool gotest.tools/gotestsum@<version>`, the same for `gocover-cobertura`) and run with `go tool <name>`, so the manifest declares them.

## Example

```json
{
  "version": {{PROJECT_CONFIG_VERSION}},
  "setup": "go mod download",
  "test": "go tool gotestsum --junitfile \"${{JUNIT_REPORT_ENV}}\" -- -coverprofile=\"${{COVERAGE_REPORT_ENV}}.out\" -covermode=atomic ./...; status=$?; go tool gocover-cobertura < \"${{COVERAGE_REPORT_ENV}}.out\" > \"${{COVERAGE_REPORT_ENV}}\"; exit $status",
  "coverageReport": {"format": "cobertura"},
  "listTests": "find . -name '*_test.go' -not -path '*/vendor/*' -not -path '*/testdata/*' -not -path '*/.*' -not -path '*/_*'",
  "testFiles": ["*_test.go", "testdata/"],
  "testSupportFiles": ["testdata/"],
  "manifestFiles": ["go.mod", "go.sum"],
  "exclude": ["vendor/", "bin/"]
}
```

- `./...` runs every package's tests. Arguments after `--` go to `go test`.
- The command keeps `go test`'s exit status (`status=$?` … `exit $status`), so the conversion step never hides a failure.
- Go's coverage is statement coverage: the converted report carries line coverage, with no branch data.
- `gotestsum`'s JUnit report names each test's package (`classname`), not its file, so `listTests` declares the test files `./...` collects: every `_test.go` file outside `vendor/`, `testdata/` and the directories whose names start with `.` or `_`, as `go` skips them. A file behind a build tag the test command does not set is listed but never runs: avoid build tags on test files. Without it, no run passes in GREEN or REFACTOR. A package that fails to build is reported as a failed run.
- Before Go 1.24 (no `tool` directive), install pinned versions in `setup` (`go install gotest.tools/gotestsum@<version>`) and call them by path, or use `go-junit-report` (`go test -v ./... 2>&1 | go-junit-report -set-exit-code > report.xml`); check the tool's docs.

## Pitfalls

- Test helpers shared across packages belong in `_test.go` files or under `testdata/`; a helper in a non-test file is production code.
- Build tags or `-run` patterns in the test command leave tests out; the command must run the whole suite.
