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
import inspect
import numpy as np
from pathlib import Path


# =============================================================================
#  CONFIGURAÇÃO DO USUÁRIO — EDITE APENAS ESTE BLOCO
# =============================================================================

AGENT_MODULE_PATH = "workspace_output_tdd-c840710a6233feba/src/interpolacao_lagrange.py"
FUNCTION_NAME     = "interpolacao_lagrange"
CHALLENGE_ID      = 12

GROUND_TRUTH_FILE = Path(__file__).parent / "ground_truth.json"

METHOD_KEY      = "lagrange"
ORDER_TOLERANCE = 0.20
H_LEVELS        = [0.1, 0.05, 0.025, 0.0125, 0.00625, 0.003125]

ODE_SOLVER_TOL      = 1e-8
ODE_SOLVER_MAX_ITER = 50


# =============================================================================
#  DEFINIÇÃO DO PROBLEMA
# =============================================================================

# Usado APENAS quando problem_type == "integracao" (ex.: trapezio/simpson).
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
ODE_EXACT_FINAL = None
ODE_T0 = None
ODE_Y0 = None
ODE_T_FINAL = None
ODE_RHS = None
ODE_DF = None
DERIV_X = None
DERIV_EXACT = None
DERIV_F = None
INTERP_NODES = None
INTERP_X = None
INTERP_EXACT = None
INTERP_F = None
SIMPSON_PI_SINC_HIGH_PREC = 1.851937051982466


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
    if INTEGRAL_EXACT is None:
        raise RuntimeError("INTEGRAL_EXACT não foi inicializado.")
    return abs(float(result) - INTEGRAL_EXACT)


def _error_ode_ivp(result, h):
    if ODE_EXACT_FINAL is None:
        raise RuntimeError("ODE_EXACT_FINAL não foi inicializado.")
    return abs(float(result) - ODE_EXACT_FINAL)


def _error_derivacao(result, h):
    if DERIV_EXACT is None:
        raise RuntimeError("DERIV_EXACT não foi inicializado.")
    return abs(float(result) - DERIV_EXACT)


def _error_interpolacao(result, h):
    if INTERP_EXACT is None:
        raise RuntimeError("INTERP_EXACT não foi inicializado.")
    return abs(float(result) - INTERP_EXACT)

ERROR_FN = {
    "integracao": _error_integracao,
    "ode_ivp": _error_ode_ivp,
    "derivacao": _error_derivacao,
    "interpolacao": _error_interpolacao,
}


# =============================================================================
#  CHAMADAS AO SOLVER
# =============================================================================

def _call_integracao(solver_fn, h):
    n = max(2, round((B - A) / h))  # round garante que n dobra quando h é dividido por 2
    if METHOD_KEY == "simpson_1_3" and n % 2 != 0:
        n += 1
    return solver_fn(INTEGRAND, A, B, n)


def _call_ode_ivp(solver_fn, h):
    signature = inspect.signature(solver_fn)
    n_params = len(signature.parameters)

    # Taylor 2 exige derivada total adicional (df), não parâmetros de solver implícito.
    if METHOD_KEY == "taylor_2":
        if ODE_DF is None:
            raise RuntimeError("ODE_DF não foi inicializado para o método taylor_2")
        result = solver_fn(ODE_RHS, ODE_DF, ODE_T0, ODE_Y0, ODE_T_FINAL, h)
    # Mantém compatibilidade com solvers que recebem apenas (f, t0, y0, t_final, h)
    # e com variantes implícitas que exigem tolerância e máximo de iterações.
    elif n_params <= 5:
        result = solver_fn(ODE_RHS, ODE_T0, ODE_Y0, ODE_T_FINAL, h)
    else:
        result = solver_fn(
            ODE_RHS,
            ODE_T0,
            ODE_Y0,
            ODE_T_FINAL,
            h,
            ODE_SOLVER_TOL,
            ODE_SOLVER_MAX_ITER,
        )

    # Alguns solvers retornam (ts, ys); outros retornam apenas ys.
    if isinstance(result, tuple):
        if len(result) >= 2:
            return result[1][-1]
        if len(result) == 1:
            return result[0][-1]

    if not isinstance(result, tuple) and hasattr(result, "__len__") and hasattr(result, "__getitem__"):
        return result[-1]

    return result


def _call_derivacao(solver_fn, h):
    if DERIV_F is None:
        raise RuntimeError("DERIV_F não foi inicializado.")
    if DERIV_X is None:
        raise RuntimeError("DERIV_X não foi inicializado.")
    return solver_fn(DERIV_F, DERIV_X, h)


def _call_interpolacao(solver_fn, h):
    if INTERP_F is None:
        raise RuntimeError("INTERP_F não foi inicializado.")
    if INTERP_NODES is None:
        raise RuntimeError("INTERP_NODES não foi inicializado.")
    if INTERP_X is None:
        raise RuntimeError("INTERP_X não foi inicializado.")
    return solver_fn(INTERP_NODES, INTERP_F, INTERP_X)

CALL_FN = {
    "integracao": _call_integracao,
    "ode_ivp": _call_ode_ivp,
    "derivacao": _call_derivacao,
    "interpolacao": _call_interpolacao,
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
    raw_path = Path(module_path)
    path = raw_path.resolve()

    if not path.exists() and not raw_path.is_absolute():
        project_root = Path(__file__).resolve().parent.parent
        candidate = (project_root / raw_path).resolve()
        if candidate.exists():
            path = candidate

    # Fallback automático: procura o mesmo arquivo dentro de src/** do workspace alvo.
    if not path.exists() and len(raw_path.parts) >= 3 and raw_path.parts[0].startswith("workspace_output_"):
        project_root = Path(__file__).resolve().parent.parent
        workspace_dir = project_root / raw_path.parts[0]
        if workspace_dir.exists():
            matches = sorted((workspace_dir / "src").glob(f"**/{raw_path.name}"))
            if matches:
                path = matches[0].resolve()

    if not path.exists():
        raise FileNotFoundError(f"Módulo não encontrado: {path}")

    added_sys_path = None
    module_name = "agent_module"
    src_dir = next((parent for parent in path.parents if parent.name == "src"), None)

    if src_dir is not None:
        module_root = src_dir
        module_name = ".".join(path.relative_to(src_dir).with_suffix("").parts)
    else:
        module_root = path.parent

    module_root_str = str(module_root)
    if module_root_str not in sys.path:
        sys.path.insert(0, module_root_str)
        added_sys_path = module_root_str

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Não foi possível criar spec para o módulo: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    if added_sys_path and added_sys_path in sys.path:
        sys.path.remove(added_sys_path)

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
    if expected_order is None:
        return {
            "status": "PASS",
            "reason": "Método sem ordem de convergência teórica aplicável neste validador.",
            "estimated_order": None,
            "r_squared": None,
            "h_vals": [],
            "errors": [],
        }

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

    # Se todos os erros ficaram no piso numérico, a ordem por regressão não é observável.
    # Nessa situação o método está, na prática, acertando dentro de precisão de máquina.
    if max(errors) <= 1e-14:
        return {
            "status": "PASS",
            "reason": "Erros no piso numérico; ordem de convergência não observável (solução essencialmente exata).",
            "estimated_order": None,
            "r_squared": 1.0,
            "h_vals": h_vals,
            "errors": errors,
        }

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
    global INTEGRAL_EXACT, ODE_EXACT_FINAL, ODE_T0, ODE_Y0, ODE_T_FINAL, ODE_RHS, ODE_DF, DERIV_X, DERIV_EXACT, DERIV_F, INTERP_NODES, INTERP_X, INTERP_EXACT, INTERP_F

    method         = METHODS[METHOD_KEY]
    problem_type   = method["problem_type"]
    expected_order = method["expected_order"]

    print(f"\n{'='*60}")
    print(f" TDDAgents — Validador de Convergência")
    print(f" Método : {method['label']}")
    print(f" Módulo : {AGENT_MODULE_PATH}")
    print(f"{'='*60}")

    gt = load_ground_truth(GROUND_TRUTH_FILE, CHALLENGE_ID)
    gt_method = gt.get("method")
    if gt_method and gt_method != METHOD_KEY:
        raise ValueError(
            f"Configuração inconsistente: METHOD_KEY='{METHOD_KEY}', "
            f"mas CHALLENGE_ID={CHALLENGE_ID} corresponde a method='{gt_method}' no ground_truth."
        )

    print(f"[INFO] Tipo de problema ativo: {problem_type}")

    if problem_type == "integracao":
        if METHOD_KEY == "simpson_1_3":
            INTEGRAL_EXACT = SIMPSON_PI_SINC_HIGH_PREC
        else:
            INTEGRAL_EXACT = float(gt["valor_esperado"])
        print(f"\n[INFO] INTEGRAL_EXACT = {INTEGRAL_EXACT}")
    elif problem_type == "ode_ivp":
        cond = gt.get("condicao_inicial", {})
        interval = gt.get("intervalo", [0.0, 1.0])

        ODE_T0 = float(cond.get("t0", interval[0]))
        ODE_Y0 = float(cond.get("y0", 1.0))
        ODE_T_FINAL = float(interval[1])
        ODE_EXACT_FINAL = float(gt["valor_esperado"])

        rhs_by_challenge = {
            1: lambda t, y: -y,
            2: lambda t, y: -y,
            5: lambda t, y: -2.0 * t * y,
            6: lambda t, y: -2.0 * t * y,
            7: lambda t, y: -2.0 * t * y,
            8: lambda t, y: -y,
            9: lambda t, y: -y,
            10: lambda t, y: -y,
        }
        ODE_RHS = rhs_by_challenge.get(CHALLENGE_ID)
        if ODE_RHS is None:
            raise ValueError(
                f"Challenge {CHALLENGE_ID} não possui RHS mapeado para problema ODE neste validador."
            )

        # Necessário para métodos de Taylor que exigem derivada total adicional de y'.
        df_by_challenge = {
            10: lambda t, y: y,
        }
        ODE_DF = df_by_challenge.get(CHALLENGE_ID)
        if METHOD_KEY == "taylor_2" and ODE_DF is None:
            raise ValueError(
                f"Challenge {CHALLENGE_ID} não possui df(t,y) mapeado para método Taylor neste validador."
            )

        print(f"\n[INFO] ODE alvo: y' = f(t,y), y({ODE_T0})={ODE_Y0}, t_final={ODE_T_FINAL}")
        print(f"[INFO] Valor exato em t_final = {ODE_EXACT_FINAL}")
    elif problem_type == "derivacao":
        DERIV_X = float(gt["ponto"])
        DERIV_EXACT = float(gt["valor_esperado"])

        deriv_by_challenge = {
            11: np.sin,
        }
        DERIV_F = deriv_by_challenge.get(CHALLENGE_ID)
        if DERIV_F is None:
            raise ValueError(
                f"Challenge {CHALLENGE_ID} não possui função f(x) mapeada para problema de derivação neste validador."
            )

        print(f"\n[INFO] Derivação alvo: f'(x) em x={DERIV_X}")
        print(f"[INFO] Valor exato da derivada = {DERIV_EXACT}")
    elif problem_type == "interpolacao":
        INTERP_NODES = [float(x) for x in gt["nos"]]
        INTERP_X = float(gt["ponto_avaliacao"])
        INTERP_EXACT = float(gt["valor_esperado"])

        interp_f_by_challenge = {
            12: np.exp,
        }
        INTERP_F = interp_f_by_challenge.get(CHALLENGE_ID)
        if INTERP_F is None:
            raise ValueError(
                f"Challenge {CHALLENGE_ID} não possui função f(x) mapeada para problema de interpolação neste validador."
            )

        print(f"\n[INFO] Interpolação alvo: p(x) em x={INTERP_X} com {len(INTERP_NODES)} nós")
        print(f"[INFO] Valor exato no ponto = {INTERP_EXACT}")

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
