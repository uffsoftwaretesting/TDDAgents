# Refactoring Catalogue

## 1. Extract Function / Method
- Turn complex, nested code into small, descriptive helper functions.
- Ensure the extracted function has a single, clear responsibility.

## 2. Rename Variable / Symbol
- Replace cryptic or misleading names with domain-meaningful terms.
- Check that all callers are updated consistently.

## 3. Inline Temp
- If a temporary variable only holds an expression result once and adds no readability, inline it.

## 4. Replace Conditional with Polymorphism / Dictionary Dispatch
- Simplify repeated `if/elif/else` chains by mapping actions to functions or classes.

## 5. De-duplicate Test Setup
- Extract common fixture setup into reusable helpers or pytest fixtures.

## 6. Cover the Uncovered
- Read the coverage report `RunTests` prints (`Missing` lists the uncovered lines and branches).
- Add a focused test for each uncovered branch: the other side of an `if`, the exception path, the empty input.
- If code cannot be reached by any input the requirement allows, remove it rather than testing it.

## 7. Strengthen Weak Tests
- Replace assertions that only check "no error" or truthiness with checks of the exact result.
- Add boundary values (zero, one, the limit, one past it) and invalid inputs next to the typical case.
- A test that would still pass if the code under it were wrong is not yet a test.
