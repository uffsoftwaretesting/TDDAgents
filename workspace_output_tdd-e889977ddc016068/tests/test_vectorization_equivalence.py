import pytest
import numpy as np
import math
import integrador_trapezio as mod

# 1. Existência da função vetorizada
def test_solve_vectorized_exists():
    assert hasattr(mod, 'solve_vectorized'), "Módulo deve expor 'solve_vectorized'"
    assert callable(mod.solve_vectorized), "solve_vectorized deve ser chamável"

# 2. Equivalência entre implementações sequencial e vetorizada
@pytest.mark.parametrize("f, a, b, n", [
    (lambda x: x**2, 0.0, 1.0, 100),
    (math.sin, 0.0, math.pi, 100),
    (lambda x: 3*x + 2, -1.0, 1.0, 50),
])
def test_vectorized_matches_sequential(f, a, b, n):
    seq = mod.solve(f, a, b, n)
    vec = mod.solve_vectorized(f, a, b, n)
    assert isinstance(vec, float), "Resultado da versão vetorizada deve ser float"
    assert vec == pytest.approx(seq), (
        f"solve_vectorized devolveu {vec}, mas solve devolveu {seq} para f={f}, a={a}, b={b}, n={n}"
    )

# 3. Aceitar funções que processam arrays numpy de uma vez
def test_vectorized_handles_numpy_vector_function():
    # Função f que espera um numpy.ndarray e retorna numpy.ndarray
    def f_array(x_arr):
        # Retorna x^3
        return x_arr**3
    a, b, n = -2.0, 2.0, 200
    seq = mod.solve(lambda x: x**3, a, b, n)
    vec = mod.solve_vectorized(f_array, a, b, n)
    assert vec == pytest.approx(seq)

# 4. Documentação sobre vetorização
def test_module_docstring_mentions_numpy_or_vectorization():
    doc = (mod.__doc__ or '').lower()
    assert 'numpy' in doc or 'vetoriz' in doc, \
        "Docstring do módulo deve mencionar 'numpy' ou 'vetorização' para indicar uso de vetor"