# Test Isolation and Mocking Principles

1. Prefer direct instantiation and state verification over heavy mock hierarchies.
2. When mocking filesystem access, use `tmp_path` pytest fixture.
3. Keep assertions strict: test exact values and exceptions rather than checking only truthiness.
