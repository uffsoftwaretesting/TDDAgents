# =============================================================================
# plot_loglog_regression.py
#
# Script standalone para plotar gráficos de regressão log-log do erro numérico.
# Equação: log(E) ≈ p·log(h) + log(C)
#
# Para cada método numérico, descobre TODOS os workspaces disponíveis,
# carrega os erros de convergência de cada um e calcula a MÉDIA dos erros
# em cada ponto h. Isso produz uma única curva média por método.
#
# Gera dois gráficos overlay:
#   1. Comparação dos métodos ODE  (Euler, RK4, Adams-Bashforth 3)
#   2. Comparação dos métodos de Integração (Trapézio, Simpson 1/3)
#
# Uso:
#   python script/plot_loglog_regression.py
# =============================================================================

import json
import glob
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

# =============================================================================
#  CONFIGURAÇÃO
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Cada método é definido pelo diretório pai, o glob para encontrar workspaces,
# a chave do JSON de avaliação e metadados visuais.

ODE_CONFIGS = [
    {
        "method_dir": "euler",
        "eval_filename": "evaluation_euler_explicito.json",
        "label": "Euler Explícito",
        "color": "#2196F3",
        "marker": "o",
    },
    {
        "method_dir": "runge_kutta_4",
        "eval_filename": "evaluation_rk4.json",
        "label": "Runge-Kutta 4",
        "color": "#4CAF50",
        "marker": "^",
    },
    {
        "method_dir": "adams-bashforth_ordem3",
        "eval_filename": "evaluation_adams_bashforth_3.json",
        "label": "Adams-Bashforth 3",
        "color": "#FF5722",
        "marker": "P",
    },
]

INTEGRACAO_CONFIGS = [
    {
        "method_dir": "trapezoid",
        "eval_filename": "evaluation_trapezio.json",
        "label": "Trapézio Composto",
        "color": "#00BCD4",
        "marker": "D",
    },
    {
        "method_dir": "simpson_⅓",
        "eval_filename": "evaluation_simpson_1_3.json",
        "label": "Simpson 1/3",
        "color": "#9C27B0",
        "marker": "s",
    },
]

OUTPUT_DIR = PROJECT_ROOT / "script" / "loglog_plots"


# =============================================================================
#  DESCOBERTA DINÂMICA DE WORKSPACES
# =============================================================================

def discover_evaluation_files(cfg):
    """Descobre todos os JSONs de avaliação de um método via glob."""
    method_path = PROJECT_ROOT / cfg["method_dir"]
    if not method_path.exists():
        print(f"  [WARN] Diretório do método não encontrado: {method_path}")
        return []

    pattern = str(method_path / "workspace_output_*" / "evaluation" / cfg["eval_filename"])
    found = sorted(glob.glob(pattern))

    if not found:
        print(f"  [WARN] Nenhum arquivo de avaliação para {cfg['label']}: {pattern}")
    else:
        print(f"  [{cfg['label']}] {len(found)} workspace(s) encontrado(s)")

    return found


# =============================================================================
#  CARREGAMENTO E MÉDIA DOS RESULTADOS
# =============================================================================

def load_and_average(cfg):
    """
    Carrega todos os evaluation JSONs de um método, extrai h_vals e errors,
    e retorna a MÉDIA dos erros em cada ponto h.

    Retorna: (h_vals, avg_errors, n_workspaces) ou (None, None, 0)
    """
    eval_files = discover_evaluation_files(cfg)
    if not eval_files:
        return None, None, 0

    all_h_vals = []
    all_errors = []

    for fpath in eval_files:
        with open(fpath) as f:
            data = json.load(f)

        conv = data.get("convergence", {})
        h_vals = conv.get("h_vals", [])
        errors = conv.get("errors", [])

        if not h_vals or not errors or len(h_vals) < 3:
            print(f"    [SKIP] Dados insuficientes em {fpath}")
            continue

        # Filtrar pontos com erro <= 0 (log não definido)
        pairs = [(h, e) for h, e in zip(h_vals, errors) if e > 0]
        if len(pairs) < 3:
            print(f"    [SKIP] Menos de 3 pontos válidos em {fpath}")
            continue

        h_f, e_f = zip(*pairs)
        all_h_vals.append(list(h_f))
        all_errors.append(list(e_f))

    if not all_errors:
        return None, None, 0

    # Verificar que todos os workspaces usam os mesmos h_vals
    ref_h = all_h_vals[0]
    for i, h in enumerate(all_h_vals[1:], 1):
        if h != ref_h:
            print(f"    [WARN] h_vals diferem entre workspaces para {cfg['label']}! "
                  f"Usando interseção.")
            break

    # Calcular média dos erros ponto a ponto
    errors_matrix = np.array(all_errors)  # shape: (n_workspaces, n_points)
    avg_errors = np.mean(errors_matrix, axis=0).tolist()

    return ref_h, avg_errors, len(all_errors)


# =============================================================================
#  REGRESSÃO LOG-LOG
# =============================================================================

def compute_regression(h_vals, errors):
    """Retorna (estimated_order, log_C, r_squared) pela regressão log-log."""
    log_h = np.log10(h_vals)
    log_e = np.log10(errors)
    coeffs = np.polyfit(log_h, log_e, 1)
    est_order = float(coeffs[0])
    log_C = float(coeffs[1])

    residuals = log_e - np.polyval(coeffs, log_h)
    ss_tot = np.sum((log_e - np.mean(log_e)) ** 2)
    r2 = float(1.0 - np.sum(residuals ** 2) / ss_tot) if ss_tot > 0 else 0.0

    return est_order, log_C, r2


# =============================================================================
#  GRÁFICO OVERLAY (por categoria)
# =============================================================================

def plot_overlay(averaged_results, title_suffix=""):
    """
    Sobrepõe métodos num único gráfico log-log.

    averaged_results: lista de (cfg, h_vals, avg_errors, n_ws)
    """
    fig, ax = plt.subplots(figsize=(10, 7))

    for cfg, h_vals, avg_errors, n_ws in averaged_results:
        if h_vals is None:
            continue

        log_h = np.log10(h_vals)
        log_e = np.log10(avg_errors)
        est_order, log_C, r2 = compute_regression(h_vals, avg_errors)

        # Pontos observados (média)
        ax.plot(log_h, log_e, cfg["marker"], color=cfg["color"],
                markersize=8, zorder=5)

        # Reta de regressão
        h_fit = np.linspace(min(log_h) - 0.1, max(log_h) + 0.1, 200)
        ax.plot(h_fit, est_order * h_fit + log_C, '-', color=cfg["color"],
                linewidth=2,
                label=f'{cfg["label"]} (p={est_order:.2f}, n={n_ws})')

    ax.set_xlabel('log₁₀(h)', fontsize=13)
    ax.set_ylabel('log₁₀(E)', fontsize=13)
    ax.set_title(
        f'Comparação de Métodos{title_suffix}\n'
        'log(E) ≈ p·log(h) + log(C)  —  erro médio entre workspaces',
        fontsize=14, fontweight='bold')
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


# =============================================================================
#  GRÁFICO INDIVIDUAL
# =============================================================================

def plot_individual(ax, h_vals, errors, est_order, log_C, r2, label, color, marker, n_ws):
    """Plota regressão log-log em um eixo matplotlib."""
    log_h = np.log10(h_vals)
    log_e = np.log10(errors)

    ax.plot(log_h, log_e, marker, color=color, markersize=8,
            label=f'Erro médio (n={n_ws})', zorder=5)

    h_fit = np.linspace(min(log_h) - 0.15, max(log_h) + 0.15, 200)
    e_fit = est_order * h_fit + log_C
    ax.plot(h_fit, e_fit, '--', color='#555555', linewidth=2,
            label=f'Regressão: p = {est_order:.4f}')

    ax.set_xlabel('log₁₀(h)', fontsize=11)
    ax.set_ylabel('log₁₀(E)', fontsize=11)
    ax.set_title(label, fontsize=12, fontweight='bold')
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    ax.annotate(f'R² = {r2:.4f}', xy=(0.97, 0.07),
                xycoords='axes fraction', ha='right', fontsize=9,
                color='#333333',
                bbox=dict(boxstyle='round,pad=0.3', fc='#f5f5f5',
                          ec='#cccccc', alpha=0.9))


# =============================================================================
#  RELATÓRIO TEXTO
# =============================================================================

def print_summary(averaged_results, group_label=""):
    if group_label:
        print(f"\n  ── {group_label} ──")
    print(f"  {'Método':<28}  {'p estimado':>10}  {'R²':>8}  {'Workspaces':>10}")
    print(f"  {'-'*62}")
    for cfg, h_vals, avg_errors, n_ws in averaged_results:
        if h_vals is None:
            print(f"  {cfg['label']:<28}  {'N/A':>10}  {'—':>8}  {0:>10}")
            continue
        est_order, _, r2 = compute_regression(h_vals, avg_errors)
        print(f"  {cfg['label']:<28}  {est_order:>10.4f}  {r2:>8.4f}  {n_ws:>10}")


# =============================================================================
#  MAIN
# =============================================================================

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f" TDDAgents — Plotter de Regressão Log-Log (Média)")
    print(f"{'='*60}\n")

    # ── Carregar e calcular médias por método ──

    print("── Métodos ODE ──")
    ode_averaged = []
    for cfg in ODE_CONFIGS:
        h_vals, avg_errors, n_ws = load_and_average(cfg)
        ode_averaged.append((cfg, h_vals, avg_errors, n_ws))

    print("\n── Métodos de Integração ──")
    int_averaged = []
    for cfg in INTEGRACAO_CONFIGS:
        h_vals, avg_errors, n_ws = load_and_average(cfg)
        int_averaged.append((cfg, h_vals, avg_errors, n_ws))

    all_averaged = ode_averaged + int_averaged

    # ── Resumo textual ──
    print(f"\n{'='*72}")
    print_summary(ode_averaged, "Métodos ODE (Aproximação de Função)")
    print_summary(int_averaged, "Métodos de Integração Numérica")
    print(f"{'='*72}\n")

    # ── Gráficos individuais por método ──
    for cfg, h_vals, avg_errors, n_ws in all_averaged:
        if h_vals is None:
            print(f"  [SKIP] {cfg['label']}: dados insuficientes.")
            continue

        est_order, log_C, r2 = compute_regression(h_vals, avg_errors)

        fig, ax = plt.subplots(figsize=(8, 6))
        plot_individual(ax, h_vals, avg_errors, est_order, log_C, r2,
                        cfg["label"], cfg["color"], cfg["marker"], n_ws)
        fig.suptitle('log(E) ≈ p·log(h) + log(C)', fontsize=11, color='#555555')
        fig.tight_layout()

        method_key = cfg["method_dir"].replace("-", "_")
        out = OUTPUT_DIR / f"loglog_{method_key}.png"
        fig.savefig(out, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"  Salvo: {out}")

    # ── Overlay ODE ──
    valid_ode = [(c, h, e, n) for c, h, e, n in ode_averaged if h is not None]
    if valid_ode:
        overlay_ode = plot_overlay(valid_ode, " — ODE (Aproximação de Função)")
        o_ode = OUTPUT_DIR / "loglog_overlay_ode.png"
        overlay_ode.savefig(o_ode, dpi=150, bbox_inches='tight')
        plt.close(overlay_ode)
        print(f"\n  Overlay ODE salvo         : {o_ode}")
    else:
        print("\n  [SKIP] Overlay ODE: nenhum método com dados válidos.")

    # ── Overlay Integração ──
    valid_int = [(c, h, e, n) for c, h, e, n in int_averaged if h is not None]
    if valid_int:
        overlay_int = plot_overlay(valid_int, " — Integração Numérica")
        o_int = OUTPUT_DIR / "loglog_overlay_integracao.png"
        overlay_int.savefig(o_int, dpi=150, bbox_inches='tight')
        plt.close(overlay_int)
        print(f"  Overlay Integração salvo  : {o_int}")
    else:
        print("  [SKIP] Overlay Integração: nenhum método com dados válidos.")

    print(f"\n  Todos os gráficos em: {OUTPUT_DIR}\n")


if __name__ == "__main__":
    main()
