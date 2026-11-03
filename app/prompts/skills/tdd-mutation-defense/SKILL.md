---
name: tdd-mutation-defense
description: Strategies for designing high-mutation-score test suites that kill subtle mutant survivors.
when_to_use: When hardening test suites, addressing surviving mutants, or preparing for mutation testing gates.
argument-hint: "[mutant-id-or-file]"
---

# TDD Mutation Defense Skill

You are strengthening test assertions to kill the surviving mutants a mutation-testing tool reports (mutmut, Stryker, PIT, cargo-mutants, Infection and others).

## Mutation Defense Tactics

1. **Boundary Value Testing**:
   - For `<=`, test exact equality, just below, and just above.
   - For `<`, test value, value - 1, and value + 1.
2. **Boolean Operator Mutation**:
   - AND -> OR (`and` -> `or`, `&&` -> `||`): write a case where the first condition is true and the second is false.
   - OR -> AND: write a case where exactly one of the conditions is true.
3. **Return Value Inversion**:
   - Test both the true and the false branch explicitly.
   - Never assert only that a result is truthy; assert the exact value (`assert result is True`, `expect(result).toBe(true)`, `assertEquals(expected, result)`).
4. **Exception Mutation**:
   - Assert the error's type and message, not only that something failed: `pytest.raises(ValueError, match="...")`, `expect(() => f()).toThrow("...")`, JUnit's `assertThrows` followed by a check of `getMessage()`, the returned `error` in Go.
5. **No Blind Tests**:
   - Verify every test fails when the production logic is altered.

## Reference

For the mutation operators and how to kill each, read `references/mutation_operators.md` in this skill's base directory.
