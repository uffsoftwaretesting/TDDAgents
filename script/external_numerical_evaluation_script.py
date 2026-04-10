# =============================================================================
# convergence_tester.py
#
# Validador externo de convergência para o projeto TDDAgents.
# Testa se o código gerado pelos agentes converge na ordem teórica esperada.
#
# Uso:
#   python convergence_tester.py
#
# Para trocar de cenário, edite APENAS o bloco "CONFIGURAÇÃO DO USUÁRIO".
# O resto do script não precisa ser modificado.
# =============================================================================

import importlib.util
import sys
import json
import numpy as np
from scipy.integrate import quad
from pathlib import Path


# =============================================================================
#  CONFIGURAÇÃO DO USUÁRIO — EDITE APENAS ESTE BLOCO
# =============================================================================

# --- Arquivo gerado pelos agentes -----------------------------------------
AGENT_MODULE_PATH = "workspace_output/src/trapezio_solver.py"
FUNCTION_NAME     = "solve"      # nome da função exposta pelo agente
CHALLENGE_ID      = 1            # ID correspondente no ground_truth.json

# --- Gabarito ---------------------------------------------------------------
GROUND_TRUTH_FILE = "ground_truth.json"

# --- Método sendo testado ---------------------------------------------------
# Escolha uma chave do dicionário METHODS definido mais abaixo.
# Cada chave corresponde a um método com sua ordem teórica e problem_type.
#
# Chaves disponíveis:
#
#   EDO — Valor Inicial (IVP):
#     "euler_explicito"        ordem 1.0
#     "euler_implicito"        ordem 1.0
#     "rk2_heun"               ordem 2.0
#     "rk2_midpoint"           ordem 2.0
#     "rk4"                    ordem 4.0
#     "adams_bashforth_2"      ordem 2.0
#     "adams_bashforth_3"      ordem 3.0
#     "taylor_2"               ordem 2.0
#
#   Integração Numérica:
#     "trapezio"               ordem 2.0   ← DEFAULT
#     "simpson_1_3"            ordem 4.0
#
#   Derivação Numérica:
#     "diferenca_central"      ordem 2.0
#
#   Interpolação:
#     "lagrange"               ordem variável (grau + 1)
#
#   EDO — Condições de Contorno (BVP):
#     "diferencas_finitas_bvp" ordem 2.0
#
METHOD_KEY = "trapezio"

# --- Tolerância para aprovação da ordem ------------------------------------
ORDER_TOLERANCE = 0.20    # aceita ±20% da ordem teórica esperada

# --- Níveis de refinamento (h decrescente, razão 0.5) ----------------------
# Para integração/derivação: h = (b-a)/n, com n dobrando a cada nível.
# Para EDOs: h é o passo de tempo.
# Mínimo recomendado: 5 níveis para a regressão log-log ser confiável.
H_LEVELS = [0.1, 0.05, 0.025, 0.0125, 0.00625, 0.003125]

# =============================================================================
#  DEFINIÇÃO DO PROBLEMA — troque aqui ao mudar de desafio
# =============================================================================
# Cada problem_type usa um subconjunto dessas variáveis.
# Comente/descomente o bloco correspondente ao método escolhido.

# --- INTEGRAÇÃO (trapezio, simpson_1_3) ------------------------------------
#
# Problema default: ∫₀^π sin(x) dx = 2.0
#
# Como trocar:
#   1. Redefina INTEGRAND(x) com a função a integrar.
#   2. Redefina A, B com os limites de integração.
#   3. Redefina INTEGRAL_EXACT com o valor exato (use scipy.integrate.quad
#      se não houver solução analítica simples).
#   4. A assinatura esperada do agente: solve(f, a, b, n) -> float
#      onde n é o número de subintervalos (h = (b-a)/n).

A, B = 0.0, np.pi

def INTEGRAND(x):
    return np.sin(x)

INTEGRAL_EXACT, _ = quad(INTEGRAND, A, B)   # 2.0 para sin(x) em [0, π]

# --- DERIVAÇÃO (diferenca_central) -----------------------------------------
#
# Como trocar:
#   1. Redefina FUNC_TO_DIFF(x).
#   2. Redefina X0 com o ponto onde calcular a derivada.
#   3. Redefina DERIV_EXACT com o valor exato de f'(X0).
#   4. A assinatura esperada do agente: solve(f, x0, h) -> float

# X0          = np.pi / 4
# DERIV_EXACT = np.cos(X0)          # f'(x) = cos(x) para f(x) = sin(x)
#
# def FUNC_TO_DIFF(x):
#     return np.sin(x)

# --- EDO — VALOR INICIAL (euler_*, rk*, adams_*, taylor_2) -----------------
#
# Como trocar:
#   1. Redefina ODE_FUNC(t, y) com dy/dt = f(t, y).
#   2. Redefina EXACT_SOLUTION(t) com a solução analítica.
#   3. Redefina Y0, T0, T_FINAL.
#   4. A assinatura esperada do agente:
#      solve(f, y0, t0, t_final, h) -> (t_array, y_array)

# Y0, T0, T_FINAL = 1.0, 0.0, 2.0
#
# def ODE_FUNC(t, y):
#     return -2.0 * y               # dy/dt = -2y
#
# def EXACT_SOLUTION(t):
#     return np.exp(-2.0 * t)       # y(t) = e^(-2t)

# --- INTERPOLAÇÃO (lagrange) ------------------------------------------------
#
# Como trocar:
#   1. Redefina FUNC_TO_INTERP(x).
#   2. Redefina INTERP_A, INTERP_B com o intervalo dos nós.
#   3. Redefina X_TEST com o ponto de avaliação.
#   4. Redefina INTERP_EXACT com o valor exato f(X_TEST).
#   5. A assinatura esperada do agente:
#      solve(f, a, b, n, x_test) -> float
#      onde n é o número de nós igualmente espaçados.

# INTERP_A, INTERP_B = 0.0, 1.0
# X_TEST             = 0.5
# INTERP_EXACT       = np.exp(X_TEST)
#
# def FUNC_TO_INTERP(x):
#     return np.exp(x)

# --- EDO — CONDIÇÕES DE CONTORNO (diferencas_finitas_bvp) ------------------
#
# Como trocar:
#   1. Redefina BVP_SOURCE(x) com o termo fonte (lado direito de u'' = g(x)).
#   2. Redefina BVP_EXACT(x_arr) com a solução analítica vetorizada.
#   3. Redefina BVP_A, BVP_B com o intervalo espacial.
#   4. A assinatura esperada do agente:
#      solve(g, a, b, n, ua, ub) -> (x_array, u_array)
#      onde n é o número de pontos internos, ua e ub são condições de contorno.

# BVP_A, BVP_B = 0.0, 1.0
# BVP_UA, BVP_UB = 0.0, 0.0       # condições de contorno
#
# def BVP_SOURCE(x):
#     return -(np.pi**2) * np.sin(np.pi * x)   # u'' = -π²sin(πx)
#
# def BVP_EXACT(x_arr):
#     return np.sin(np.pi * np.asarray(x_arr)) # u(x) = sin(πx)


# =============================================================================
#  CATÁLOGO DE MÉTODOS — adicione novos métodos aqui se necessário
# =============================================================================

METHODS = {
    # --- EDO — Valor Inicial (IVP) ------------------------------------------
    "euler_explicito": {
        "label":          "Euler Explícito",
        "expected_order": 1.0,
        "problem_type":   "ode_ivp",
        "description":    "y_{n+1} = y_n + h·f(t_n, y_n)",
    },
    "euler_implicito": {
        "label":          "Euler Implícito (Backward Euler)",
        "expected_order": 1.0,
        "problem_type":   "ode_ivp",
        "description":    "y_{n+1} = y_n + h·f(t_{n+1}, y_{n+1}) — resolvido iterativamente",
    },
    "rk2_heun": {
        "label":          "Runge-Kutta 2ª ordem — Heun",
        "expected_order": 2.0,
        "problem_type":   "ode_ivp",
        "description":    "k1=f(t,y), k2=f(t+h, y+h·k1), média ponderada",
    },
    "rk2_midpoint": {
        "label":          "Runge-Kutta 2ª ordem — Ponto Médio",
        "expected_order": 2.0,
        "problem_type":   "ode_ivp",
        "description":    "k1=f(t,y), k2=f(t+h/2, y+h/2·k1)",
    },
    "rk4": {
        "label":          "Runge-Kutta 4ª ordem Clássico",
        "expected_order": 4.0,
        "problem_type":   "ode_ivp",
        "description":    "4 estágios com pesos 1/6, 1/3, 1/3, 1/6",
    },
    "adams_bashforth_2": {
        "label":          "Adams-Bashforth 2 Passos",
        "expected_order": 2.0,
        "problem_type":   "ode_ivp",
        "description":    "Método explícito de múltiplos passos de ordem 2",
    },
    "adams_bashforth_3": {
        "label":          "Adams-Bashforth 3 Passos",
        "expected_order": 3.0,
        "problem_type":   "ode_ivp",
        "description":    "Método explícito de múltiplos passos de ordem 3",
    },
    "taylor_2": {
        "label":          "Taylor de 2ª Ordem",
        "expected_order": 2.0,
        "problem_type":   "ode_ivp",
        "description":    "y_{n+1} = y_n + h·f + (h²/2)·f' — requer derivada de f",
    },
    # --- Integração Numérica ------------------------------------------------
    "trapezio": {
        "label":          "Regra do Trapézio Composta",
        "expected_order": 2.0,
        "problem_type":   "integracao",
        "description":    "∫f dx ≈ (h/2)·[f(a) + 2Σf(xi) + f(b)], h = (b-a)/n",
    },
    "simpson_1_3": {
        "label":          "Regra de Simpson 1/3 Composta",
        "expected_order": 4.0,
        "problem_type":   "integracao",
        "description":    "∫f dx ≈ (h/3)·[f(a) + 4Σímpar + 2Σpar + f(b)], n par",
    },
    # --- Derivação Numérica -------------------------------------------------
    "diferenca_central": {
        "label":          "Diferença Finita Central (1ª Derivada)",
        "expected_order": 2.0,
        "problem_type":   "derivacao",
        "description":    "f'(x) ≈ [f(x+h) - f(x-h)] / (2h) — erro O(h²)",
    },
    # --- Interpolação -------------------------------------------------------
    "lagrange": {
        "label":          "Interpolação de Lagrange",
        "expected_order": None,   # depende do grau: ordem = grau + 1
        "problem_type":   "interpolacao",
        "description":    "P(x) = Σ f(xi)·Li(x); erro ∝ h^(n+1) para n nós",
    },
    # --- EDO — Condições de Contorno (BVP) ----------------------------------
    "diferencas_finitas_bvp": {
        "label":          "Diferenças Finitas para BVP",
        "expected_order": 2.0,
        "problem_type":   "bvp",
        "description":    "u'' ≈ [u(x-h)-2u(x)+u(x+h)]/h² — sistema tridiagonal Au=b",
    },
}


# =============================================================================
#  FUNÇÕES DE ERRO POR PROBLEM_TYPE
#  Cada função recebe o resultado do agente e retorna o erro escalar.
#  Adicione uma nova entrada aqui ao criar um novo problem_type.
# =============================================================================

def _error_ode_ivp(result, h):
    """Para EDOs IVP: compara y(t_final) com a solução exata."""
    t_arr, y_arr = _unpack_ode_result(result)
    return abs(float(y_arr[-1]) - EXACT_SOLUTION(T_FINAL))

def _error_integracao(result, h):
    """Para integração: compara o escalar retornado com o valor exato."""
    return abs(float(result) - INTEGRAL_EXACT)

def _error_derivacao(result, h):
    """Para derivação: compara o escalar retornado com a derivada exata."""
    return abs(float(result) - DERIV_EXACT)

def _error_interpolacao(result, h):
    """Para interpolação: compara P(x_test) com f(x_test) exato."""
    return abs(float(result) - INTERP_EXACT)

def _error_bvp(result, h):
    """Para BVP: erro máximo sobre todos os pontos da malha."""
    x_arr, u_arr = result
    x_arr = np.asarray(x_arr)
    u_arr = np.asarray(u_arr)
    return float(np.max(np.abs(u_arr - BVP_EXACT(x_arr))))

ERROR_FN = {
    "ode_ivp":      _error_ode_ivp,
    "integracao":   _error_integracao,
    "derivacao":    _error_derivacao,
    "interpolacao": _error_interpolacao,
    "bvp":          _error_bvp,
}


# =============================================================================
#  FUNÇÕES DE CHAMADA AO SOLVER POR PROBLEM_TYPE
#  Cada função chama o solver do agente com os argumentos corretos para o tipo.
#  Adicione uma nova entrada aqui ao criar um novo problem_type.
# =============================================================================

def _call_ode_ivp(solver_fn, h):
    return solver_fn(ODE_FUNC, Y0, T0, T_FINAL, h)

def _call_integracao(solver_fn, h):
    n = max(2, round((B - A) / h))     # n subintervalos inteiros
    return solver_fn(INTEGRAND, A, B, n)

def _call_derivacao(solver_fn, h):
    return solver_fn(FUNC_TO_DIFF, X0, h)

def _call_interpolacao(solver_fn, h):
    n = max(2, round((INTERP_B - INTERP_A) / h) + 1)   # n nós
    return solver_fn(FUNC_TO_INTERP, INTERP_A, INTERP_B, n, X_TEST)

def _call_bvp(solver_fn, h):
    n = max(2, round((BVP_B - BVP_A) / h) - 1)         # n pontos internos
    return solver_fn(BVP_SOURCE, BVP_A, BVP_B, n, BVP_UA, BVP_UB)

CALL_FN = {
    "ode_ivp":      _call_ode_ivp,
    "integracao":   _call_integracao,
    "derivacao":    _call_derivacao,
    "interpolacao": _call_interpolacao,
    "bvp":          _call_bvp,
}


# =============================================================================
#  UTILITÁRIOS
# =============================================================================

def _unpack_ode_result(result):
    """Normaliza o retorno de solvers de EDO para (t_array, y_array)."""
    if isinstance(result, (tuple, list)) and len(result) == 2:
        t_arr = np.asarray(result[0])
        y_arr = np.asarray(result[1])
        if y_arr.ndim == 2:
            y_arr = y_arr[0]    # shape (1, N) → (N,)
        return t_arr, y_arr
    if isinstance(result, np.ndarray):
        n_steps = round((T_FINAL - T0) / H_LEVELS[-1])
        t_arr   = np.linspace(T0, T_FINAL, n_steps + 1)
        return t_arr, result
    raise ValueError(
        f"Formato de retorno não reconhecido para EDO: {type(result)}. "
        "Esperado: (t_array, y_array) ou y_array."
    )

def load_ground_truth(filepath, challenge_id):
    with open(filepath) as f:
        entries = json.load(f)
    if isinstance(entries, list):
        match = [e for e in entries if e["id"] == challenge_id]
        if not match:
            raise ValueError(f"ID {challenge_id} não encontrado em {filepath}")
        return match[0]
    return entries

def load_agent_module(module_path):
    path = Path(module_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Módulo não encontrado: {path}")
    spec   = importlib.util.spec_from_file_location("agent_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =============================================================================
#  VERIFICAÇÃO PONTUAL
# =============================================================================

def run_pointwise_check(solver_fn, gt, problem_type):
    """
    Roda o solver com o menor h disponível (mais preciso) e compara
    o resultado com o gabarito usando numpy.testing.assert_allclose.
    """
    h_fine = H_LEVELS[-1]
    try:
        result     = CALL_FN[problem_type](solver_fn, h_fine)
        error_fn   = ERROR_FN[problem_type]
        error      = error_fn(result, h_fine)

        # Extrai o valor escalar obtido para o relatório
        if problem_type == "ode_ivp":
            _, y_arr   = _unpack_ode_result(result)
            obtained   = float(y_arr[-1])
        elif problem_type == "bvp":
            obtained   = None    # erro máximo já capturado acima
        else:
            obtained   = float(result)

        np.testing.assert_allclose(
            actual  = error,
            desired = 0.0,
            atol    = gt["tolerancia_absoluta"],
        )
        return {
            "status":         "PASS",
            "h_used":         h_fine,
            "value_obtained": round(obtained, 10) if obtained is not None else "—",
            "value_expected": gt["valor_esperado"],
            "abs_error":      round(error, 12),
        }
    except AssertionError as e:
        return {"status": "FAIL",  "reason": str(e)}
    except Exception as e:
        return {"status": "ERROR", "reason": str(e)}


# =============================================================================
#  TESTE DE CONVERGÊNCIA
# =============================================================================

def run_convergence_test(solver_fn, expected_order, problem_type):
    """
    Para cada h em H_LEVELS:
      1. Chama o solver do agente.
      2. Calcula o erro em relação ao valor exato.
      3. Acumula o par (h, erro).

    Estima a ordem de convergência pela inclinação da regressão
    log10(erro) vs log10(h) via numpy.polyfit.

    Critérios de aprovação:
      - Ordem estimada dentro de ±ORDER_TOLERANCE da ordem teórica.
      - Fator de redução médio do erro próximo de 2^ordem_esperada
        (ao dividir h por 2, o erro deve cair por esse fator).
    """
    if expected_order is None:
        # Lagrange: ordem depende do grau — apenas registra sem critério fixo
        return _run_convergence_no_fixed_order(solver_fn, problem_type)

    h_vals, errors = [], []
    prev_error     = None

    print(f"\n  {'h':>10}  {'erro':>14}  {'fator redução':>16}")
    print(f"  {'-'*45}")

    for h in H_LEVELS:
        try:
            result = CALL_FN[problem_type](solver_fn, h)
            error  = ERROR_FN[problem_type](result, h)
            error  = max(error, 1e-15)    # evita log(0)

            fator  = f"{prev_error / error:.3f}" if prev_error else "—"
            print(f"  {h:>10.6f}  {error:>14.2e}  {fator:>16}")

            h_vals.append(h)
            errors.append(error)
            prev_error = error

        except Exception as e:
            print(f"  [WARN] h={h}: {e}")

    if len(h_vals) < 3:
        return {
            "status": "INCONCLUSIVE",
            "reason": f"Apenas {len(h_vals)} níveis válidos (mínimo: 3)",
        }

    # --- Regressão log-log --------------------------------------------------
    log_h  = np.log10(h_vals)
    log_e  = np.log10(errors)
    coeffs = np.polyfit(log_h, log_e, deg=1)
    estimated_order = float(coeffs[0])

    residuals = log_e - np.polyval(coeffs, log_h)
    ss_res    = np.sum(residuals**2)
    ss_tot    = np.sum((log_e - np.mean(log_e))**2)
    r_squared = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    # --- Fator de redução médio ---------------------------------------------
    # Ao dividir h por 2, esperamos que o erro caia por 2^ordem_esperada.
    # Ex: trapézio (ordem 2) → fator ≈ 4; RK4 (ordem 4) → fator ≈ 16.
    reduction_factors = [errors[i] / errors[i+1] for i in range(len(errors)-1)]
    avg_reduction     = float(np.mean(reduction_factors))
    expected_factor   = 2 ** expected_order
    factor_ok         = (expected_factor * 0.6) <= avg_reduction <= (expected_factor * 1.4)

    lower    = expected_order * (1 - ORDER_TOLERANCE)
    upper    = expected_order * (1 + ORDER_TOLERANCE)
    order_ok = lower <= estimated_order <= upper

    passed = order_ok and factor_ok

    return {
        "status":                     "PASS" if passed else "FAIL",
        "estimated_order":            round(estimated_order, 4),
        "expected_order":             expected_order,
        "acceptable_range":           [round(lower, 4), round(upper, 4)],
        "r_squared":                  round(r_squared, 4),
        "avg_error_reduction_factor": round(avg_reduction, 4),
        "expected_reduction_factor":  round(expected_factor, 4),
        "h_vals":                     h_vals,
        "errors":                     [round(e, 12) for e in errors],
        "reason": "OK" if passed else (
            f"Ordem estimada {estimated_order:.3f} "
            f"(esperado {expected_order:.1f} ± {ORDER_TOLERANCE*100:.0f}%)  |  "
            f"Fator de redução médio {avg_reduction:.2f} "
            f"(esperado ≈ {expected_factor:.1f})"
        ),
    }


def _run_convergence_no_fixed_order(solver_fn, problem_type):
    """
    Versão sem ordem fixa — usada para Lagrange.
    Apenas estima a ordem observada e reporta, sem critério de PASS/FAIL.
    """
    h_vals, errors = [], []
    prev_error     = None

    print(f"\n  {'h':>10}  {'erro':>14}  {'fator redução':>16}")
    print(f"  {'-'*45}")

    for h in H_LEVELS:
        try:
            result = CALL_FN[problem_type](solver_fn, h)
            error  = ERROR_FN[problem_type](result, h)
            error  = max(error, 1e-15)

            fator  = f"{prev_error / error:.3f}" if prev_error else "—"
            print(f"  {h:>10.6f}  {error:>14.2e}  {fator:>16}")

            h_vals.append(h)
            errors.append(error)
            prev_error = error
        except Exception as e:
            print(f"  [WARN] h={h}: {e}")

    if len(h_vals) < 3:
        return {"status": "INCONCLUSIVE", "reason": "Poucos níveis válidos"}

    log_h  = np.log10(h_vals)
    log_e  = np.log10(errors)
    coeffs = np.polyfit(log_h, log_e, deg=1)

    return {
        "status":           "INFO",
        "estimated_order":  round(float(coeffs[0]), 4),
        "expected_order":   "variável (grau + 1)",
        "note":             "Lagrange: sem critério fixo — inspecione a ordem estimada",
        "h_vals":           h_vals,
        "errors":           [round(e, 12) for e in errors],
    }


# =============================================================================
#  MAIN
# =============================================================================

def main():
    # --- Validar METHOD_KEY -------------------------------------------------
    if METHOD_KEY not in METHODS:
        print(f"[ERRO] METHOD_KEY '{METHOD_KEY}' não encontrado.")
        print(f"       Opções disponíveis: {list(METHODS.keys())}")
        sys.exit(1)

    method         = METHODS[METHOD_KEY]
    problem_type   = method["problem_type"]
    expected_order = method["expected_order"]

    print(f"\n{'='*60}")
    print(f" TDDAgents — Validador Externo de Convergência")
    print(f" Método       : {method['label']}")
    print(f" Tipo         : {problem_type}")
    print(f" Ordem esperada: {expected_order}")
    print(f" Módulo       : {AGENT_MODULE_PATH}")
    print(f"{'='*60}")

    # --- Carregar gabarito e módulo -----------------------------------------
    gt        = load_ground_truth(GROUND_TRUTH_FILE, CHALLENGE_ID)
    module    = load_agent_module(AGENT_MODULE_PATH)

    if not hasattr(module, FUNCTION_NAME):
        print(f"\n[ERRO] Função '{FUNCTION_NAME}' não encontrada no módulo.")
        sys.exit(1)

    solver_fn = getattr(module, FUNCTION_NAME)
    print(f"\n[OK] Função '{FUNCTION_NAME}' carregada.\n")

    # --- Verificação pontual ------------------------------------------------
    print("── Verificação Pontual (assert_allclose) ──────────────")
    pointwise = run_pointwise_check(solver_fn, gt, problem_type)
    print(f"  Status  : {pointwise['status']}")
    if pointwise["status"] == "PASS":
        print(f"  Obtido  : {pointwise['value_obtained']}")
        print(f"  Esperado: {pointwise['value_expected']}")
        print(f"  Erro    : {pointwise['abs_error']}")
    else:
        print(f"  Motivo  : {pointwise.get('reason')}")

    # --- Teste de convergência ----------------------------------------------
    print("\n── Teste de Convergência (regressão log-log) ──────────")
    convergence = run_convergence_test(solver_fn, expected_order, problem_type)

    print(f"\n  Status            : {convergence['status']}")
    print(f"  Ordem estimada    : {convergence.get('estimated_order')}")
    print(f"  Intervalo aceito  : {convergence.get('acceptable_range')}")
    print(f"  R²                : {convergence.get('r_squared')}")
    if "avg_error_reduction_factor" in convergence:
        print(f"  Fator de redução  : {convergence['avg_error_reduction_factor']} "
              f"(esperado ≈ {convergence['expected_reduction_factor']})")
    if convergence["status"] not in ("PASS", "INFO"):
        print(f"  Motivo            : {convergence.get('reason')}")

    # --- Resultado final ----------------------------------------------------
    if convergence["status"] == "INFO":
        # Lagrange: sem critério fixo, resultado depende só da verificação pontual
        final_status = pointwise["status"]
    else:
        final_status = (
            "PASS"
            if pointwise["status"] == "PASS" and convergence["status"] == "PASS"
            else "FAIL"
        )

    result = {
        "challenge_id":     CHALLENGE_ID,
        "module":           AGENT_MODULE_PATH,
        "method":           method["label"],
        "method_key":       METHOD_KEY,
        "problem_type":     problem_type,
        "final_status":     final_status,
        "pointwise_check":  pointwise,
        "convergence_test": convergence,
    }

    output_path = f"evaluation_results_challenge_{CHALLENGE_ID}.json"
    with open(output_path, "w") as f_out:
        json.dump(result, f_out, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f" RESULTADO FINAL : {final_status}")
    print(f" Salvo em        : {output_path}")
    print(f"{'='*60}\n")

    sys.exit(0 if final_status == "PASS" else 1)


if __name__ == "__main__":
    main()