import inspect
from src.solver_euler import euler_explicito


def test_return_annotation():
    sig = inspect.signature(euler_explicito)
    assert sig.return_annotation is float, \
        f"Expected return annotation float, got {sig.return_annotation}"