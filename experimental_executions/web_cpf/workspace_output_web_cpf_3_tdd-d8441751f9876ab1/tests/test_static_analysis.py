import subprocess
import sys


def test_mypy_strict_no_errors() -> None:
    """Check that mypy reports no type errors."""
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "src/", "tests/"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Mypy errors:\n{result.stdout}\n{result.stderr}"


def test_flake8_zero_warnings() -> None:
    """Ensure flake8 finds no linting issues."""
    result = subprocess.run(
        [sys.executable, "-m", "flake8"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Flake8 warnings/errors:\n{result.stdout}"
