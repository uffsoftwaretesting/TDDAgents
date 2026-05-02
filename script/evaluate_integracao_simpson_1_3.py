# =============================================================================
# evaluate_integracao_simpson_1_3.py
#
# Avaliação numérica completa do método Simpson 1/3 gerado pelo TDDAgents.
# Inclui: Unit Test Success, verificação pontual, convergência, gráfico log-log.
#
# Integral (Challenge 4): ∫₀^π sin(x)/x dx  (sinc normalizado)
# Gabarito obtido via scipy.integrate.quad.
# Uso: python script/evaluate_integracao_simpson_1_3.py
# =============================================================================

import sys
import json
import numpy as np
from pathlib import Path
from scipy.integrate import quad

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _eval_utils import (
    load_ground_truth, load_agent_module, run_unit_tests,
    run_convergence_test, plot_loglog,
)

# =============================================================================
#  CONFIGURAÇÃO
# =============================================================================

WORKSPACE_DIR     = "simpson_⅓"
SUBFOLDER_DIR     = "workspace_output_simpson_⅓_1_tdd-2f8127d30a635de2"
AGENT_MODULE_PATH = f"{WORKSPACE_DIR}/{SUBFOLDER_DIR}/src/integracao.py"
FUNCTION_NAME     = "integracao_simpson_1_3"
CHALLENGE_ID      = 4
METHOD_KEY        = "simpson_1_3"
METHOD_LABEL      = "Simpson 1/3"
EXPECTED_ORDER    = 4.0
PROBLEM_TYPE      = "integracao"
PLOT_COLOR        = "#9C27B0"
PLOT_MARKER       = "s"

GROUND_TRUTH_FILE = Path(__file__).parent / "ground_truth.json"
OUTPUT_DIR        = Path(__file__).resolve().parent.parent / WORKSPACE_DIR / SUBFOLDER_DIR / "evaluation"
H_LEVELS          = [0.1, 0.05, 0.025, 0.0125, 0.00625, 0.003125]

A, B = 0.0, np.pi
INTEGRAL_EXACT = None


def _integrand(x):
    return np.sinc(x / np.pi)


def _integrand_quad(x):
    if x == 0.0:
        return 1.0
    return np.sin(x) / x


def _compute_scipy_reference():
    result, _ = quad(_integrand_quad, A, B, limit=200)
    return float(result)


def _call_solver(solver_fn, h):
    n = max(2, round((B - A) / h))
    if n % 2 != 0:
        n += 1
    return solver_fn(_integrand, A, B, n)


def _err(result):
    return abs(float(result) - INTEGRAL_EXACT)


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


def main():
    global INTEGRAL_EXACT

    print(f"\n{'='*60}\n TDDAgents — Avaliação Numérica: {METHOD_LABEL}")
    print(f" Módulo : {AGENT_MODULE_PATH}\n{'='*60}")

    gt = load_ground_truth(GROUND_TRUTH_FILE, CHALLENGE_ID)
    INTEGRAL_EXACT = _compute_scipy_reference()
    print(f"\n[INFO] Integral: ∫₀^π sin(x)/x dx")
    print(f"[INFO] Gabarito scipy (quad) = {INTEGRAL_EXACT}")

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
        lambda h: _call_solver(solver_fn, h), _err, H_LEVELS, EXPECTED_ORDER,
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
        "scipy_reference": INTEGRAL_EXACT,
        "unit_test_success": {
            "passed": ut["passed"], "failed": ut["failed"],
            "errors": ut["errors"], "total": ut["total"],
            "success_rate_pct": ut["success_rate_pct"],
        },
        "pointwise": pointwise, "convergence": convergence,
    }
    output_path = OUTPUT_DIR / f"evaluation_{METHOD_KEY}.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}\n RESULTADO FINAL : {final_status}\n Salvo em        : {output_path}\n{'='*60}\n")
    sys.exit(0 if final_status == "PASS" else 1)


if __name__ == "__main__":
    main()
