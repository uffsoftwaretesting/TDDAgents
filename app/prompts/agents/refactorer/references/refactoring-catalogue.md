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
