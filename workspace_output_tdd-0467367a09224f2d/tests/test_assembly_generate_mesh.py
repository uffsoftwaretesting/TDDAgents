import pytest
import numpy as np
from diferencas_finitas_bvp.assembly import _generate_mesh


def test_generate_mesh_N1_unit_domain():
    """
    For a=0.0, b=1.0 and N=1, h should be 0.5 and x should be [0.0, 0.5, 1.0].
    """
    a, b, N = 0.0, 1.0, 1
    h_expected = 0.5
    x_expected = np.array([0.0, 0.5, 1.0])
    h, x = _generate_mesh(a, b, N)
    # Check spacing
    assert isinstance(h, float), "h should be a float"
    assert h == pytest.approx(h_expected)
    # Check mesh nodes
    assert isinstance(x, np.ndarray), "x should be a numpy array"
    assert x.shape == (N + 2,)
    assert np.allclose(x, x_expected)


def test_generate_mesh_N3_unit_domain():
    """
    For a=0.0, b=1.0 and N=3, h should be 0.25 and x should be linspace from 0 to 1 with 5 points.
    """
    a, b, N = 0.0, 1.0, 3
    h_expected = (b - a) / (N + 1)
    x_expected = np.linspace(a, b, N + 2)
    h, x = _generate_mesh(a, b, N)
    assert h == pytest.approx(h_expected)
    assert x.shape == (N + 2,)
    assert np.allclose(x, x_expected)


def test_generate_mesh_custom_domain():
    """
    For a=-2.0, b=2.0 and N=3, h should be (2-(-2))/4 = 1.0 and x should be linspace(-2,2,5).
    """
    a, b, N = -2.0, 2.0, 3
    h_expected = (b - a) / (N + 1)
    x_expected = np.linspace(a, b, N + 2)
    h, x = _generate_mesh(a, b, N)
    assert h == pytest.approx(h_expected)
    assert x.shape == (N + 2,)
    assert np.allclose(x, x_expected)
