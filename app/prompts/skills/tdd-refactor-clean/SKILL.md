---
name: tdd-refactor-clean
description: "Guidelines for the mandatory REFACTOR phase: improving code and tests and raising coverage while every test stays green."
when_to_use: In the REFACTOR phase after all tests pass, to raise coverage and improve readability, typing, test strength and architectural hygiene.
argument-hint: "[target-source-file]"
---

# TDD Clean Refactoring Skill

You are performing the mandatory REFACTOR phase after achieving a GREEN test suite. It is done when a `RunTests` run passes. Raising line and branch coverage is part of the work: `RunTests` reports it with a good rule of thumb.

## Invariants

1. **Behavior Preservation**: Refactoring must not change public behavior or contract semantics.
2. **Improve the Tests Too**: In REFACTOR the tests may change: add tests for the uncovered lines and branches `RunTests` lists, sharpen weak assertions, and remove duplicated setup. A test may be rewritten, never weakened.
3. **Small Steps**: Make one structural transformation at a time (e.g. Extract Method, Rename Variable, Introduce Parameter Object).
4. **Immediate Verification**: Run `RunTests` after every transformation and read its coverage. If any test fails, fix or revert immediately.
5. **Clean Code Targets**:
   - Eliminate duplicated logic (DRY).
   - Add explicit Python type annotations.
   - Decompose complex conditionals.
   - Improve variable and function naming.

## Reference

For the clean-code catalogue, read `references/clean_code.md` in this skill's base directory.
