# Mutation Operators Quick Reference

1. **Number mutations**: +1, -1, sign flip. Guard with exact numeric boundary assertions.
2. **String mutations**: Capitalization, prefix mutation. Guard with full string matching or strict regex.
3. **Comparison mutations**: < to <=, > to >=, == to !=. Guard with edge values.
4. **Logical mutations**: AND to OR, a condition negated or replaced by `true` / `false`. Guard with cases where the conditions differ.
5. **Statement and return mutations**: a call removed, a return value replaced by a default (`None`, `null`, `0`, an empty collection). Guard by asserting the effect and the exact return value.

The tools differ by ecosystem (mutmut for Python, Stryker for JavaScript, TypeScript and C#, PIT for Java, cargo-mutants for Rust, Infection for PHP), but the operators and the way to kill them are the same.
