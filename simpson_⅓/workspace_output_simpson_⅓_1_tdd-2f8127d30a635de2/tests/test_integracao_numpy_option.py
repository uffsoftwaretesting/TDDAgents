import pytest
import time
np = pytest.importorskip("numpy")
from src.integracao import integracao_simpson_1_3


def test_integracao_simpson_1_3_accepts_use_numpy_flag_and_returns_float():
    """Verifica que a função aceita parâmetro use_numpy e retorna float."""
    f = lambda x: x ** 2
    a, b, N = 0.0, 1.0, 100
    pure = integracao_simpson_1_3(f, a, b, N, use_numpy=False)
    numpy_res = integracao_simpson_1_3(f, a, b, N, use_numpy=True)
    assert isinstance(pure, float)
    assert isinstance(numpy_res, float)


def test_integracao_simpson_1_3_numpy_matches_pure_results():
    """Com use_numpy=True, resultado deve igualar à versão pura."""
    f = lambda x: np.sin(x)
    a, b, N = 0.0, np.pi, 1000
    pure = integracao_simpson_1_3(f, a, b, N, use_numpy=False)
    numpy_res = integracao_simpson_1_3(f, a, b, N, use_numpy=True)
    assert pure == pytest.approx(numpy_res, rel=1e-6)


def test_integracao_simpson_1_3_numpy_performance_gain():
    """Verifica que versão numpy é pelo menos 2x mais rápida para N grande."""
    f = lambda x: x ** 2
    a, b, N = 0.0, 1.0, 20000
    start = time.perf_counter()
    integracao_simpson_1_3(f, a, b, N, use_numpy=False)
    pure_time = time.perf_counter() - start
    start = time.perf_counter()
    integracao_simpson_1_3(f, a, b, N, use_numpy=True)
    numpy_time = time.perf_counter() - start
    assert numpy_time * 2 < pure_time, (
        f"Numpy version should be at least twice as fast, got pure_time={pure_time}, numpy_time={numpy_time}"
    )
