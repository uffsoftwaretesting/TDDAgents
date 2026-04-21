"""
Módulo de interpolação dos valores de u na malha.
"""
import numpy as np

def _evaluate_at_target(u: np.ndarray, x: np.ndarray, x_alvo: float) -> float:
    """
    Avalia o valor de u no ponto x_alvo usando interpolação linear.
    Retorna valor exato se x_alvo coincide com um nó.

    Parâmetros
    ----------
    u : numpy.ndarray
        Valores da solução em cada nó x.
    x : numpy.ndarray
        Nós da malha.
    x_alvo : float
        Ponto de avaliação.

    Retorna
    -------
    float
        Valor interpolado ou exato de u em x_alvo.
    """
    tol = 1e-12
    # Verifica correspondência exata dentro da tolerância
    idx_exact = np.where(np.abs(x - x_alvo) < tol)[0]
    if idx_exact.size > 0:
        return float(u[idx_exact[0]])
    # Localizar intervalo para interpolação
    idx = np.searchsorted(x, x_alvo)
    # Se estiver antes do primeiro nó
    if idx == 0:
        return float(u[0])
    # Se estiver além do último nó
    if idx >= x.shape[0]:
        return float(u[-1])
    # Pontos de interpolação
    x0 = x[idx - 1]
    x1 = x[idx]
    u0 = u[idx - 1]
    u1 = u[idx]
    # Interpolação linear
    return float(u0 + (u1 - u0) * (x_alvo - x0) / (x1 - x0))