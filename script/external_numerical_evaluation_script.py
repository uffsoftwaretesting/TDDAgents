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
# =============================================================================

import importlib.util
import sys
import json
import numpy as np
from pathlib import Path


# =============================================================================
#  CONFIGURAÇÃO DO USUÁRIO — EDITE APENAS ESTE BLOCO
# =============================================================================

AGENT_MODULE_PATH = "workspace_output_tdd-c79c2ec75162b2f1/src/solve.py"
FUNCTION_NAME     = "solve"
CHALLENGE_ID      = 3

GROUND_TRUTH_FILE = Path(__file__).parent / "ground_truth.json"

METHOD_KEY      = "trapezio"
ORDER_TOLERANCE = 0.20
H_LEVELS        = [0.1, 0.05, 0.025, 0.0125, 0.00625, 0.003125]


# =============================================================================
#  DEFINIÇÃO DO PROBLEMA
# =============================================================================

A, B = 0.0, np.pi

T0, TF = 0.0, 1.0
Y0 = 1.0

def INTEGRAND(x):
    """sin(x)/x via np.sinc para estabilidade numérica em x=0."""
    return np.sinc(x / np.pi)


def ODE_FUNCTION(t, y):
    """ODE de referência para cenários Euler/RK no gabarito: y' = -y."""
    return -y

INTEGRAL_EXACT = None  # preenchido após carregar o gabarito
ODE_EXACT = None


# =============================================================================
#  CATÁLOGO DE MÉTODOS
# =============================================================================

METHODS = {
    "euler_explicito":       {"label": "Euler Explícito",              "expected_order": 1.0,  "problem_type": "ode_ivp"},
    "euler_implicito":       {"label": "Euler Implícito",              "expected_order": 1.0,  "problem_type": "ode_ivp"},
    "rk2_heun":              {"label": "Runge-Kutta 2 — Heun",         "expected_order": 2.0,  "problem_type": "ode_ivp"},
    "rk2_midpoint":          {"label": "Runge-Kutta 2 — Midpoint",     "expected_order": 2.0,  "problem_type": "ode_ivp"},
    "rk4":                   {"label": "Runge-Kutta 4",                "expected_order": 4.0,  "problem_type": "ode_ivp"},
    "adams_bashforth_2":     {"label": "Adams Bashforth 2",            "expected_order": 2.0,  "problem_type": "ode_ivp"},
    "adams_bashforth_3":     {"label": "Adams Bashforth 3",            "expected_order": 3.0,  "problem_type": "ode_ivp"},
    "taylor_2":              {"label": "Taylor ordem 2",               "expected_order": 2.0,  "problem_type": "ode_ivp"},
    "trapezio":              {"label": "Regra do Trapézio Composta",   "expected_order": 2.0,  "problem_type": "integracao"},
    "simpson_1_3":           {"label": "Simpson 1/3",                  "expected_order": 4.0,  "problem_type": "integracao"},
    "diferenca_central":     {"label": "Diferença Central",            "expected_order": 2.0,  "problem_type": "derivacao"},
    "lagrange":              {"label": "Interpolação de Lagrange",     "expected_order": None, "problem_type": "interpolacao"},
    "diferencas_finitas_bvp":{"label": "Diferenças Finitas BVP",      "expected_order": 2.0,  "problem_type": "bvp"},
}


# =============================================================================
#  FUNÇÕES DE ERRO
# =============================================================================

def _error_integracao(result, h):
    return abs(float(result) - INTEGRAL_EXACT)


def _error_ode_ivp(result, h):
    if isinstance(result, (list, tuple, np.ndarray)):
        result = result[-1]
    return abs(float(result) - ODE_EXACT)

ERROR_FN = {
    "integracao": _error_integracao,
    "ode_ivp": _error_ode_ivp,
}


# =============================================================================
#  CHAMADAS AO SOLVER
# =============================================================================

def _call_integracao(solver_fn, h):
    n = max(2, round((B - A) / h))  # round garante que n dobra quando h é dividido por 2
    return solver_fn(INTEGRAND, A, B, n)


def _call_ode_ivp(solver_fn, h):
    n = max(2, round((TF - T0) / h))
    return solver_fn(ODE_FUNCTION, T0, TF, Y0, n)

CALL_FN = {
    "integracao": _call_integracao,
    "ode_ivp": _call_ode_ivp,
}


# =============================================================================
#  UTILITÁRIOS
# =============================================================================

def load_ground_truth(filepath, challenge_id):
    filepath = Path(filepath)
    with open(filepath) as f:
        entries = json.load(f)
    match = [e for e in entries if e["id"] == challenge_id]
    if not match:
        raise ValueError(f"Challenge ID {challenge_id} não encontrado em {filepath}")
    return match[0]


def load_agent_module(module_path):
    path = Path(module_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Módulo não encontrado: {path}")

    # Resolve imports like "from src..." independent of workspace folder name.
    project_root = next(
        (parent for parent in path.parents if (parent / "src").is_dir()),
        path.parent.parent,
    )
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

    spec   = importlib.util.spec_from_file_location("agent_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =============================================================================
#  VERIFICAÇÃO PONTUAL
# =============================================================================

def run_pointwise_check(solver_fn, gt, problem_type):
    h_fine = H_LEVELS[-1]
    try:
        result = CALL_FN[problem_type](solver_fn, h_fine)
        error  = ERROR_FN[problem_type](result, h_fine)
        np.testing.assert_allclose(actual=error, desired=0.0, atol=gt["tolerancia_absoluta"])
        return {"status": "PASS", "abs_error": error}
    except AssertionError as e:
        return {"status": "FAIL",  "reason": str(e)}
    except Exception as e:
        return {"status": "ERROR", "reason": str(e)}


# =============================================================================
#  TESTE DE CONVERGÊNCIA
# =============================================================================

def run_convergence_test(solver_fn, expected_order, problem_type):
    h_vals, errors = [], []
    prev_error = None

    print(f"\n  {'h':>10}  {'erro':>14}  {'fator redução':>16}")
    print(f"  {'-'*45}")

    for h in H_LEVELS:
        try:
            result = CALL_FN[problem_type](solver_fn, h)
            error  = max(ERROR_FN[problem_type](result, h), 1e-15)
            fator  = f"{prev_error / error:.3f}" if prev_error else "—"
            print(f"  {h:>10.6f}  {error:>14.2e}  {fator:>16}")
            h_vals.append(h)
            errors.append(error)
            prev_error = error
        except Exception as e:
            print(f"  [WARN] h={h}: {e}")

    if len(h_vals) < 3:
        return {"status": "INCONCLUSIVE", "reason": f"Apenas {len(h_vals)} níveis válidos (mínimo: 3)"}

    log_h = np.log10(h_vals)
    log_e = np.log10(errors)
    coeffs = np.polyfit(log_h, log_e, 1)
    estimated_order = float(coeffs[0])

    residuals = log_e - np.polyval(coeffs, log_h)
    ss_res    = np.sum(residuals ** 2)
    ss_tot    = np.sum((log_e - np.mean(log_e)) ** 2)
    r_squared = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    # Regressão fraca: não dá para concluir sobre a ordem
    if r_squared < 0.9:
        return {
            "status":           "INCONCLUSIVE",
            "reason":           f"Regressão fraca R²={r_squared:.3f} — erro não segue lei de potência",
            "estimated_order":  round(estimated_order, 4),
            "r_squared":        round(r_squared, 4),
            "h_vals":           h_vals,
            "errors":           errors,
        }

    reduction_factors = [errors[i] / errors[i + 1] for i in range(len(errors) - 1)]
    avg_reduction     = float(np.mean(reduction_factors))
    expected_factor   = 2 ** expected_order

    lower    = expected_order * (1 - ORDER_TOLERANCE)
    upper    = expected_order * (1 + ORDER_TOLERANCE)
    order_ok = lower <= estimated_order <= upper
    factor_ok = (expected_factor * 0.6) <= avg_reduction <= (expected_factor * 1.4)
    passed   = order_ok and factor_ok

    return {
        "status":           "PASS" if passed else "FAIL",
        "estimated_order":  round(estimated_order, 4),
        "expected_order":   expected_order,
        "acceptable_range": [round(lower, 4), round(upper, 4)],
        "r_squared":        round(r_squared, 4),
        "avg_reduction":    round(avg_reduction, 4),
        "expected_reduction": round(expected_factor, 4),
        "h_vals":           h_vals,
        "errors":           errors,
        "reason": "OK" if passed else (
            f"Ordem estimada {estimated_order:.3f} (esperado {expected_order:.1f} ±{ORDER_TOLERANCE*100:.0f}%)  |  "
            f"Fator de redução médio {avg_reduction:.2f} (esperado ≈ {expected_factor:.1f})"
        ),
    }


# =============================================================================
#  MAIN
# =============================================================================

def main():
    global INTEGRAL_EXACT, ODE_EXACT

    method         = METHODS[METHOD_KEY]
    problem_type   = method["problem_type"]
    expected_order = method["expected_order"]

    print(f"\n{'='*60}")
    print(f" TDDAgents — Validador de Convergência")
    print(f" Método : {method['label']}")
    print(f" Módulo : {AGENT_MODULE_PATH}")
    print(f"{'='*60}")

    gt = load_ground_truth(GROUND_TRUTH_FILE, CHALLENGE_ID)

    if problem_type == "integracao":
        INTEGRAL_EXACT = gt["valor_esperado"]
        print(f"\n[INFO] INTEGRAL_EXACT = {INTEGRAL_EXACT}")

    if problem_type == "ode_ivp":
        ODE_EXACT = gt["valor_esperado"]
        print(f"\n[INFO] ODE_EXACT = {ODE_EXACT}")

    module    = load_agent_module(AGENT_MODULE_PATH)
    solver_fn = getattr(module, FUNCTION_NAME)
    print(f"[OK]  Função '{FUNCTION_NAME}' carregada.\n")

    print("── Verificação Pontual ────────────────────────────────")
    pointwise = run_pointwise_check(solver_fn, gt, problem_type)
    print(f"  Status : {pointwise['status']}")
    if pointwise["status"] != "PASS":
        print(f"  Motivo : {pointwise.get('reason')}")

    print("\n── Teste de Convergência ──────────────────────────────")
    convergence = run_convergence_test(solver_fn, expected_order, problem_type)
    print(f"\n  Status           : {convergence['status']}")
    print(f"  Ordem estimada   : {convergence.get('estimated_order')}")
    print(f"  R²               : {convergence.get('r_squared')}")
    if convergence["status"] == "FAIL":
        print(f"  Motivo           : {convergence.get('reason')}")

    final_status = (
        "PASS"
        if pointwise["status"] == "PASS" and convergence["status"] == "PASS"
        else "FAIL"
    )

    result = {
        "challenge_id": CHALLENGE_ID,
        "module":       AGENT_MODULE_PATH,
        "method":       method["label"],
        "method_key":   METHOD_KEY,
        "problem_type": problem_type,
        "final_status": final_status,
        "pointwise":    pointwise,
        "convergence":  convergence,
    }

    output_path = f"evaluation_results_challenge_{CHALLENGE_ID}.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f" RESULTADO FINAL : {final_status}")
    print(f" Salvo em        : {output_path}")
    print(f"{'='*60}\n")

    sys.exit(0 if final_status == "PASS" else 1)


if __name__ == "__main__":
    main()