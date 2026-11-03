# Test Isolation and Mocking Principles

1. Prefer direct instantiation and state verification over heavy mock hierarchies.
2. Give each test its own temporary files and directories, created by the framework and removed after the test: `tmp_path` (pytest), `t.TempDir()` (Go), `@TempDir` (JUnit 5), a directory from `fs.mkdtempSync` removed in `afterEach` (Jest, Vitest).
3. Replace only the boundaries the unit does not own (network, clock, randomness, the filesystem when the test is not about it) with fakes or the framework's mocks, and reset them after each test.
4. Keep assertions strict: test exact values and errors rather than checking only truthiness.
