---
name: tdd-test-design
description: Guidelines for authoring clean, isolated unit tests that establish ground truth in the RED phase.
when_to_use: In the RED phase before implementing production code, or when specifying new requirements.
argument_hint: "[target-test-path]"
paths:
  - "tests/**"
  - "test_*.py"
  - "*_test.py"
allowed_tools:
  - ReadFile
  - WriteFile
  - Edit
  - RunTests
---

# TDD Test Design Skill

You are applying disciplined Test-Driven Development (TDD) test authoring principles.
Your goal is to write a single, focused, failing test that pins down the expected behavior.
Target test path: $ARGUMENTS

## Core Rules

1. **One Concept per Test**: Each test function must assert exactly one behavior or boundary condition.
2. **Descriptive Test Names**: Name tests using `test_<unit>_<scenario>_<expected_outcome>()`.
   - Good: `test_add_negative_integers_returns_sum()`
   - Bad: `test_1()`, `test_calculator()`
3. **Arrange-Act-Assert (AAA) Pattern**:
   - Arrange: Set up inputs and test fixtures.
   - Act: Call the unit under test.
   - Assert: Check the exact expected outcome.
4. **Isolated & Hermetic**: Tests must never depend on external network calls or shared mutable global state.
5. **Verify Failure**: After writing the test, immediately run `RunTests` to ensure it fails with the expected failure mode (ImportError, AttributeError, or AssertionError).
