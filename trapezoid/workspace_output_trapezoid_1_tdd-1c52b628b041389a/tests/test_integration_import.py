import sys
import subprocess
import textwrap
import pytest

def test_standalone_script_execution(tmp_path):
    # Write a minimal standalone script that imports and uses solve
    script_path = tmp_path / "standalone_script.py"
    script_content = textwrap.dedent("""
        from src.solve import solve

        def f(x):
            return x * x

        # Approximate integral of x^2 from 0 to 3 (exact 9.0)
        # Use a large n for convergence
        result = solve(f, 0, 3, 1000)
        print(result)
    """)
    script_path.write_text(script_content)

    # Execute the script using the same Python interpreter, ensuring no path hacks
    completed = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True
    )
    # The script should exit cleanly without import or runtime errors
    assert completed.returncode == 0, f"Script failed with stderr: {completed.stderr}"

    # Capture and validate the printed result
    output = completed.stdout.strip()
    try:
        value = float(output)
    except ValueError:
        pytest.fail(f"Output is not a float: {output}")
    # Expect 9.0 with a small tolerance since n is large
    assert value == pytest.approx(9.0, rel=1e-6), f"Expected approx 9.0, got {value}"