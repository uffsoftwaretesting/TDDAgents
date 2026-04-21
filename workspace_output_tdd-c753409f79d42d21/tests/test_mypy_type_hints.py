import mypy.api

def test_mypy_type_hints_on_solver():
    """
    Ensure src/solver_euler.py has valid type hints as declared.
    """
    # Run mypy on the module under test, ignoring missing imports
    stdout, stderr, exit_status = mypy.api.run([
        "--ignore-missing-imports",
        "src/solver_euler.py"
    ])
    assert exit_status == 0, f"Mypy found type errors:\n{stdout}{stderr}"