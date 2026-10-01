---
name: tdd-mutation-defense
description: Strategies for designing high-mutation-score test suites that kill subtle mutant survivors.
when_to_use: When hardening test suites, addressing surviving mutants, or preparing for mutation testing gates.
argument_hint: "[mutant-id-or-file]"
paths:
  - "tests/**"
  - "app/**"
allowed_tools:
  - ReadFile
  - WriteFile
  - Edit
  - RunTests
---

# TDD Mutation Defense Skill

You are strengthening test assertions to kill surviving mutants produced by `mutmut`.

## Mutation Defense Tactics

1. **Boundary Value Testing**:
   - For `<=`, test exact equality, just below, and just above.
   - For `<`, test value, value - 1, and value + 1.
2. **Boolean Operator Mutation**:
   - `and` -> `or`: Write test cases where condition 1 is True and condition 2 is False.
   - `or` -> `and`: Write test cases where condition 1 is True and condition 2 is False.
3. **Return Value Inversion**:
   - Test both truthy and falsy branches explicitly.
   - Never assert only `assert result`, assert `assert result is True` or `assert result == expected`.
4. **Exception Mutation**:
   - Assert the exact exception message substring: `with pytest.raises(ValueError, match="..."):`.
5. **No Blind Tests**:
   - Verify every test fails when the production logic is altered.
