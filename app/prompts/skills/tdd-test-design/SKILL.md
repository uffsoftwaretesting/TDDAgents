---
name: tdd-test-design
description: Guidelines for authoring robust, isolated unit tests that cover a sub-requirement in the RED phase.
when_to_use: In the RED phase before implementing production code, or when specifying new requirements.
argument-hint: "[target-test-path]"
---

# TDD Test Design Skill

You are applying disciplined Test-Driven Development (TDD) test authoring principles.
Your goal is a robust suite for the sub-requirement: focused tests that, together, cover every relevant scenario and edge case of the expected behavior.
Target test path: $ARGUMENTS

## Core Rules

1. **One Concept per Test**: Each test must assert exactly one behavior or boundary condition.
2. **Descriptive Test Names**: Name each test after the unit, the scenario and the expected outcome, in the project's convention.
   - Good: `test_add_negative_integers_returns_sum` (pytest), `it("returns the sum of negative integers")` (Jest, Vitest, RSpec), `TestAddNegativeIntegersReturnsSum` (Go), `addNegativeIntegersReturnsSum` (JUnit)
   - Bad: `test_1`, `test_calculator`, `it("works")`
3. **Arrange-Act-Assert (AAA) Pattern**:
   - Arrange: Set up inputs and fixtures.
   - Act: Call the unit under test.
   - Assert: Check the exact expected outcome.
4. **Isolated & Hermetic**: Tests must never depend on external network calls, on the order they run in, or on shared mutable global state.
5. **Breadth of Scenarios**: Beyond the typical case, cover boundary values, empty and invalid inputs, error conditions and every rule the requirement states.
6. **Follow the Project**: Use the project's test framework and conventions, and put the tests in files the project's declared test-file patterns match.
7. **Run Them**: Run `RunTests` once the tests are written. They need not fail: a run of the sub-requirement's new or changed tests completes RED, whether it passes or fails.

## Reference

For test isolation and mocking, read `references/isolation.md` in this skill's base directory.
