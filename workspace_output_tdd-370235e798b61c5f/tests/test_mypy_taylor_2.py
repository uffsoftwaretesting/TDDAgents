import sys
from mypy import api

def test_mypy_no_errors_for_taylor_2_module():
    # Run mypy on the taylor_2 module source
    result = api.run(["src/taylor_2/taylor_2.py"])
    stdout, stderr, exit_status = result
    # If mypy fails, print its output
    assert exit_status == 0, (
        f"mypy reported errors (exit status={exit_status})\n"
        f"stdout:\n{stdout}\n"
        f"stderr:\n{stderr}\n"
    )
