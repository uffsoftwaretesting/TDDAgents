---
name: tdd-refactor-clean
description: Guidelines for safely restructuring production code in the REFACTOR phase while preserving green tests.
when_to_use: In the REFACTOR phase after all tests pass, to improve readability, typing, and architectural hygiene.
argument_hint: "[target-source-file]"
paths:
  - "app/**"
  - "src/**"
  - "*.py"
allowed_tools:
  - ReadFile
  - WriteFile
  - Edit
  - RunTests
---

# TDD Clean Refactoring Skill

You are performing safe code refactoring after achieving a GREEN test suite.

## Invariants

1. **Behavior Preservation**: Refactoring must not change public behavior or contract semantics.
2. **Never Edit Tests During Refactor**: In REFACTOR phase, test files are strictly immutable.
3. **Small Steps**: Make one structural transformation at a time (e.g. Extract Method, Rename Variable, Introduce Parameter Object).
4. **Immediate Verification**: Run `RunTests` after every transformation. If any test fails, revert immediately.
5. **Clean Code Targets**:
   - Eliminate duplicated logic (DRY).
   - Add explicit Python type annotations.
   - Decompose complex conditionals.
   - Improve variable and function naming.
