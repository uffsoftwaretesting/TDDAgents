# =============================================================================
# evaluate_adams_bashforth_2.py
#
# Avaliação numérica completa do método Adams-Bashforth 2 passos (TDDAgents).
# Inclui: Unit Test Success, verificação pontual, convergência, gráfico log-log.
#
# ODE (Challenge 8): y' = -y,  y(0) = 1,  t ∈ [0, 1]  →  y = exp(-t)
# Uso: python script/evaluate_adams_bashforth_2.py
# =============================================================================

import sys
import json
import inspect
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _eval_utils import (
    load_ground_truth, load_agent_module, run_unit_tests,
    run_convergence_test, plot_loglog,
)

# =============================================================================
#  CONFIGURAÇÃO
# =============================================================================

WORKSPACE_DIR     = "workspace_output_tdd-c6b7fefebb87a87b"
AGENT_MODULE_PATH = f"{WORKSPACE_DIR}/src/adams_bashforth_2.py"
FUNCTION_NAME     = "adams_bashforth_2"
CHALLENGE_ID      = 8
METHOD_KEY        = "adams_bashforth_2"
METHOD_LABEL      = "Adams-Bashforth 2 Passos"
EXPECTED_ORDER    = 2.0
PROBLEM_TYPE      = "ode_ivp"
PLOT_COLOR        = "#FF5722"
PLOT_MARKER       = "P"

GROUND_TRUTH_FILE = Path(__file__).parent / "ground_truth.json"
OUTPUT_DIR        = Path(__file__).resolve().parent.parent / WORKSPACE_DIR / "evaluation"
H_LEVELS          = [0.1, 0.05, 0.025, 0.0125, 0.00625, 0.003125]

_STATE = {}


def _setup_ode(gt):
    cond     = gt.get("condicao_inicial", {})
    interval = gt.get("intervalo", [0.0, 1.0])
    _STATE["t0"]    = float(cond.get("t0", interval[0]))
    _STATE["y0"]    = float(cond.get("y0", 1.0))
    _STATE["t_f"]   = float(interval[1])
    _STATE["exact"] = float(gt["valor_esperado"])
    _STATE["rhs"]   = lambda t, y: -y


def _call_solver(solver_fn, h):
    """
    Suporta assinaturas (f, t0, y0, t_eval, h) e (f, t0, y0, t_final, h).
    Adams-Bashforth 2 usa o nome t_eval mas semanticamente é t_final.
    """
    sig = inspect.signature(solver_fn)
    if len(sig.parameters) <= 5:
        result = solver_fn(_STATE["rhs"], _STATE["t0"], _STATE["y0"], _STATE["t_f"], h)
    else:
        result = solver_fn(_STATE["rhs"], _STATE["t0"], _STATE["y0"], _STATE["t_f"], h, 1e-8, 50)
    if isinstance(result, tuple):
        return result[1][-1] if len(result) >= 2 else result[0][-1]
    if hasattr(result, "__len__") and hasattr(result, "__getitem__"):
        return result[-1]
    return result


def _err(result):
    return abs(float(result) - _STATE["exact"])


def _pointwise(solver_fn, gt):
    try:
        result = _call_solver(solver_fn, H_LEVELS[-1])
        error  = _err(result)
        np.testing.assert_allclose(actual=error, desired=0.0, atol=gt["tolerancia_absoluta"])
        return {"status": "PASS", "abs_error": error}
    except AssertionError as e:
        return {"status": "FAIL", "reason": str(e)}
    except Exception as e:
        return {"status": "ERROR", "reason": str(e)}


# =============================================================================
#  MAIN
# =============================================================================

def main():
    print(f"\n{'='*60}\n TDDAgents — Avaliação Numérica: {METHOD_LABEL}")
    print(f" Módulo : {AGENT_MODULE_PATH}\n{'='*60}")

    gt = load_ground_truth(GROUND_TRUTH_FILE, CHALLENGE_ID)
    _setup_ode(gt)
    print(f"\n[INFO] ODE: y'=-y, y({_STATE['t0']})={_STATE['y0']}, t_final={_STATE['t_f']}")
    print(f"[INFO] Valor exato = {_STATE['exact']}")

    module    = load_agent_module(AGENT_MODULE_PATH)
    solver_fn = getattr(module, FUNCTION_NAME)
    print(f"[OK]  Função '{FUNCTION_NAME}' carregada.\n")

    print("── Unit Test Success ──────────────────────────────────")
    ut = run_unit_tests(WORKSPACE_DIR)
    print(f"  Passed : {ut['passed']}  |  Failed : {ut['failed']}  |  Total : {ut['total']}")
    print(f"  Success: {ut['success_rate_pct']}%")

    print("\n── Verificação Pontual ────────────────────────────────")
    pointwise = _pointwise(solver_fn, gt)
    print(f"  Status : {pointwise['status']}")
    if pointwise["status"] != "PASS":
        print(f"  Motivo : {pointwise.get('reason')}")

    print("\n── Teste de Convergência ──────────────────────────────")
    convergence = run_convergence_test(
        lambda h: _call_solver(solver_fn, h),
        _err,
        H_LEVELS,
        EXPECTED_ORDER,
    )
    print(f"\n  Status         : {convergence['status']}")
    print(f"  Ordem estimada : {convergence.get('estimated_order')}")
    print(f"  R²             : {convergence.get('r_squared')}")
    if convergence["status"] == "FAIL":
        print(f"  Motivo         : {convergence.get('reason')}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if convergence.get("h_vals") and convergence.get("errors") and convergence.get("estimated_order") is not None:
        plot_loglog(
            convergence["h_vals"], convergence["errors"],
            convergence["estimated_order"], METHOD_LABEL,
            OUTPUT_DIR / f"loglog_{METHOD_KEY}.png",
            color=PLOT_COLOR, marker=PLOT_MARKER,
        )

    final_status = (
        "PASS" if pointwise["status"] == "PASS" and convergence["status"] == "PASS"
        else "FAIL"
    )

    result = {
        "challenge_id": CHALLENGE_ID, "module": AGENT_MODULE_PATH,
        "method": METHOD_LABEL, "method_key": METHOD_KEY,
        "problem_type": PROBLEM_TYPE, "final_status": final_status,
        "unit_test_success": {
            "passed": ut["passed"], "failed": ut["failed"],
            "errors": ut["errors"], "total": ut["total"],
            "success_rate_pct": ut["success_rate_pct"],
        },
        "pointwise": pointwise,
        "convergence": convergence,
    }
    output_path = OUTPUT_DIR / f"evaluation_{METHOD_KEY}.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}\n RESULTADO FINAL : {final_status}\n Salvo em        : {output_path}\n{'='*60}\n")
    sys.exit(0 if final_status == "PASS" else 1)


if __name__ == "__main__":
    main()
