import subprocess
import pytest

def test_black_formatting():
    """
    Fail if any files are not formatted according to black.
    """
    # Run black in check mode across the project
    result = subprocess.run(
        ["black", "--check", "."],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    output = result.stdout
    assert result.returncode == 0, (
        "Black formatting issues detected. Please run 'black .':\n" + output
    )
