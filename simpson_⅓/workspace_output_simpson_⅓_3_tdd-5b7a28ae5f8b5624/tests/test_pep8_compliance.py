import subprocess
import sys


def test_pep8_compliance():
    """Verifica conformidade PEP8 em src/ e tests/ usando flake8."""
    result = subprocess.run(
        [sys.executable, '-m', 'flake8', 'src', 'tests'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert result.returncode == 0, (
        f"PEP8 violations found:\n{result.stdout}{result.stderr}"
    )
