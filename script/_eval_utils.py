# =============================================================================
# _eval_utils.py
#
# Módulo utilitário compartilhado pelos scripts de avaliação numérica.
# Fornece: run_unit_tests, load_ground_truth, load_agent_module, plot_loglog
# =============================================================================

import importlib.util
import subprocess
import sys
import json
import re
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

# Caminho para o pytest do projeto (venv ou sistema)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VENV_PYTHON  = _PROJECT_ROOT / ".venv" / "bin" / "python"
_PYTEST_PYTHON = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable


# ---------------------------------------------------------------------------

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
        candidate = (_PROJECT_ROOT / raw_path).resolve()
        if candidate.exists():
            path = candidate
    if not path.exists():
        raise FileNotFoundError(f"Módulo não encontrado: {path}")
    parent_str = str(path.parent)
    added = parent_str not in sys.path
    if added:
        sys.path.insert(0, parent_str)
    spec = importlib.util.spec_from_file_location("agent_module", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_module"] = module
    spec.loader.exec_module(module)
    if added and parent_str in sys.path:
        sys.path.remove(parent_str)
    return module


def run_unit_tests(workspace_dir):
    """Executa pytest no workspace usando o Python do projeto e retorna métricas."""
    ws_path = _PROJECT_ROOT / workspace_dir
    tests_path = ws_path / "tests"

    try:
        result = subprocess.run(
            [_PYTEST_PYTHON, "-m", "pytest",
             "--tb=short", "-q", "--no-header",
             "--override-ini=addopts=",
             "--rootdir", str(ws_path),
             str(tests_path)],
            capture_output=True, text=True, cwd=str(ws_path), timeout=120
        )
        output = result.stdout + result.stderr

        def _int(pattern):
            m = re.search(pattern, output)
            return int(m.group(1)) if m else 0

        passed = _int(r"(\d+)\s+passed")
        failed = _int(r"(\d+)\s+failed")
        errors = _int(r"(\d+)\s+error")
        total  = passed + failed + errors
        pct    = round(passed / total * 100.0, 2) if total > 0 else 0.0

        return {
            "passed": passed, "failed": failed, "errors": errors,
            "total": total, "success_rate_pct": pct,
            "raw_output": output[-3000:] if len(output) > 3000 else output,
        }
    except Exception as e:
        return {"passed": 0, "failed": 0, "errors": 0, "total": 0,
                "success_rate_pct": 0.0, "raw_output": str(e)}


def run_convergence_test(call_solver_fn, compute_error_fn, h_levels, expected_order,
                         order_tolerance=0.20):
    """Executa o teste de convergência e retorna dicionário de resultados."""
    h_vals, errors, prev_error = [], [], None

    print(f"\n  {'h':>10}  {'erro':>14}  {'fator redução':>16}")
    print(f"  {'-'*45}")

    for h in h_levels:
        try:
            result = call_solver_fn(h)
            error = max(compute_error_fn(result), 1e-15)
            fator = f"{prev_error / error:.3f}" if prev_error else "—"
            print(f"  {h:>10.6f}  {error:>14.2e}  {fator:>16}")
            h_vals.append(h); errors.append(error); prev_error = error
        except Exception as e:
            print(f"  [WARN] h={h}: {e}")

    if len(h_vals) < 3:
        return {"status": "INCONCLUSIVE", "reason": f"Apenas {len(h_vals)} níveis válidos (mínimo: 3)"}

    if max(errors) <= 1e-14:
        return {"status": "PASS", "reason": "Piso numérico; ordem não observável.",
                "estimated_order": None, "r_squared": 1.0,
                "h_vals": h_vals, "errors": errors}

    log_h = np.log10(h_vals); log_e = np.log10(errors)
    coeffs = np.polyfit(log_h, log_e, 1)
    est_order = float(coeffs[0])
    residuals = log_e - np.polyval(coeffs, log_h)
    ss_tot = np.sum((log_e - np.mean(log_e)) ** 2)
    r2 = float(1.0 - np.sum(residuals**2) / ss_tot) if ss_tot > 0 else 0.0

    if r2 < 0.9:
        return {"status": "INCONCLUSIVE", "reason": f"Regressão fraca R²={r2:.3f}",
                "estimated_order": round(est_order, 4), "r_squared": round(r2, 4),
                "h_vals": h_vals, "errors": errors}

    avg_red = float(np.mean([errors[i] / errors[i+1] for i in range(len(errors)-1)]))
    exp_factor = 2 ** expected_order
    lower, upper = expected_order * (1 - order_tolerance), expected_order * (1 + order_tolerance)
    passed = (lower <= est_order <= upper) and (exp_factor * 0.6 <= avg_red <= exp_factor * 1.4)

    return {
        "status": "PASS" if passed else "FAIL",
        "estimated_order": round(est_order, 4), "expected_order": expected_order,
        "acceptable_range": [round(lower, 4), round(upper, 4)],
        "r_squared": round(r2, 4), "avg_reduction": round(avg_red, 4),
        "expected_reduction": round(exp_factor, 4),
        "h_vals": h_vals, "errors": errors,
        "reason": "OK" if passed else (
            f"Ordem {est_order:.3f} (esperado {expected_order:.1f} ±{order_tolerance*100:.0f}%)  |  "
            f"Fator médio {avg_red:.2f} (esperado ≈ {exp_factor:.1f})"
        ),
    }


def plot_loglog(h_vals, errors, est_order, method_label, output_path,
                color="#2196F3", marker="o"):
    """Salva gráfico de regressão log-log: log(E) ≈ p·log(h) + log(C)."""
    log_h = np.log10(h_vals); log_e = np.log10(errors)
    coeffs = np.polyfit(log_h, log_e, 1)
    log_C = float(coeffs[1])
    r2 = _r_squared(log_h, log_e, coeffs)

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.plot(log_h, log_e, marker, color=color, markersize=8, label='Erro observado', zorder=5)
    h_fit = np.linspace(min(log_h) - 0.15, max(log_h) + 0.15, 200)
    ax.plot(h_fit, est_order * h_fit + log_C, '--', color='#333333', linewidth=2,
            label=f'Regressão: p={est_order:.4f}')
    ax.annotate(f'R² = {r2:.4f}', xy=(0.97, 0.07), xycoords='axes fraction',
                ha='right', fontsize=10,
                bbox=dict(boxstyle='round,pad=0.3', fc='#f5f5f5', ec='#cccccc', alpha=0.9))
    ax.set_xlabel('log₁₀(h)', fontsize=12)
    ax.set_ylabel('log₁₀(E)', fontsize=12)
    ax.set_title(f'Regressão Log-Log — {method_label}\nlog(E) ≈ p·log(h) + log(C)', fontsize=14)
    ax.legend(fontsize=11); ax.grid(True, alpha=0.3)
    fig.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"  Gráfico salvo em: {output_path}")


def _r_squared(log_h, log_e, coeffs):
    residuals = log_e - np.polyval(coeffs, log_h)
    ss_tot = np.sum((log_e - np.mean(log_e)) ** 2)
    return float(1.0 - np.sum(residuals**2) / ss_tot) if ss_tot > 0 else 0.0
