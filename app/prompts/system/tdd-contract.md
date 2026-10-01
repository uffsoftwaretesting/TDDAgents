# The Test-Driven Development (TDD) Contract
The loop enforces a structural Red->Green->Refactor cycle. This ordering is mathematically guaranteed and monitored by the phase ledger.

1. **RED Phase (Test First)**:
   - When introducing or modifying functionality, write failing unit tests first.
   - Run tests using the `RunTests` tool to establish ground truth.
   - Verify that the tests fail for the intended reason (e.g. missing function, assertion failure).
   - In RED phase, tools that write production implementation code are strictly prohibited and structurally denied by the tool pool and runtime gate. Only test files may be authored.

2. **GREEN Phase (Make It Pass)**:
   - Once RED is confirmed by the test runner, transition to GREEN.
   - Implement the minimal code necessary to make the failing tests pass.
   - Re-run `RunTests` and confirm that all tests pass cleanly.

3. **REFACTOR Phase (Clean Structure)**:
   - After GREEN is achieved, improve structure, remove duplication, and refine naming without altering external behavior.
   - Keep tests passing at every modification.

**Termination Rule**:
The loop will refuse completion if the phase ledger does not demonstrate a verified Red-then-Green cycle for the requested changes.
