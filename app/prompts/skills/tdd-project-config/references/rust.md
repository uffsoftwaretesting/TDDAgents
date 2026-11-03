# Rust: cargo-nextest and cargo-llvm-cov

`cargo test` writes no JUnit XML. `cargo-nextest` runs the same tests and writes JUnit, and `cargo-llvm-cov` measures coverage and writes LCOV. Both are cargo subcommands, which `Cargo.toml` cannot declare, so `setup` installs pinned versions, together with the `llvm-tools-preview` component `cargo-llvm-cov` needs.

## Tests live in `tests/`

Rust's usual unit tests, `#[cfg(test)] mod tests` inside a source file, are not supported: TDDAgents cannot tell them from production code. Write integration tests in `tests/*.rs` against the crate's public API, which needs a library target (`src/lib.rs`; a binary keeps its `main.rs` thin and calls the library). Shared helpers go in `tests/common/mod.rs`.

## Example

`.config/nextest.toml` turns on the JUnit report for a profile:

```toml
[profile.ci.junit]
path = "junit.xml"
```

nextest writes it under the profile's directory, `target/nextest/ci/junit.xml`, hence the `junitReport` override.

```json
{
  "version": {{PROJECT_CONFIG_VERSION}},
  "setup": "rustup component add llvm-tools-preview && cargo install --locked cargo-nextest@<version> cargo-llvm-cov@<version> && cargo fetch --locked",
  "test": "NEXTEST_PROFILE=ci cargo llvm-cov nextest --lcov --output-path \"${{COVERAGE_REPORT_ENV}}\"",
  "junitReport": "target/nextest/ci/junit.xml",
  "coverageReport": {"format": "lcov"},
  "testFiles": ["tests/", ".config/nextest.toml"],
  "testSupportFiles": ["tests/common/", ".config/nextest.toml"],
  "manifestFiles": ["Cargo.toml", "Cargo.lock"],
  "exclude": ["target/"]
}
```

- `cargo llvm-cov nextest` runs the suite through nextest with coverage instrumentation; `--lcov --output-path <file>` writes LCOV. `NEXTEST_PROFILE` selects the nextest profile with the JUnit report.
- Replace `<version>` with the versions you choose, so the final run installs the same tools.
- nextest's report names each test's binary (`classname`), not its file, so the uncollected-file check is unknown for Rust. A compile error fails the run.
- Branch coverage needs a nightly toolchain; the LCOV report carries line coverage.

## Pitfalls

- `cargo install` compiles the tools, which is slow; check the tools' docs for prebuilt binaries (`cargo binstall`) if `setup` must be faster.
- `#[ignore]` tests are skipped, and a skipped test is not a pass.
