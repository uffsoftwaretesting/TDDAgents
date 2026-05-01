# =============================================================================
# plot_loglog_regression.py
#
# Script standalone para plotar gráficos de regressão log-log do erro numérico.
# Equação: log(E) ≈ p·log(h) + log(C)
#
# Lê os arquivos de avaliação JSON gerados pelos scripts evaluate_*.py e
# produz gráficos separados para métodos ODE e de Integração,
# além de gráficos individuais por método.
#
# Uso:
#   python script/plot_loglog_regression.py
# =============================================================================

import json
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from pathlib import Path

# =============================================================================
#  CONFIGURAÇÃO — arquivos de entrada e saída
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Métodos ODE (Aproximação de Função via IVP)
ODE_CONFIGS = [
    {
        "workspace": "workspace_output_tdd-c753409f79d42d21",
        "method_key": "euler_explicito",
        "label": "Euler Explícito",
        "color": "#2196F3",
        "marker": "o",
    },
    {
        "workspace": "workspace_output_tdd-3b1a956efe0ebe4b",
        "method_key": "rk4",
        "label": "Runge-Kutta 4",
        "color": "#4CAF50",
        "marker": "^",
    },
    {
        "workspace": "workspace_output_tdd-7681fdfd94580fd6",
        "method_key": "adams_bashforth_3",
        "label": "Adams-Bashforth 3",
        "color": "#FF5722",
        "marker": "P",
    },
]

# Métodos de Integração Numérica
INTEGRACAO_CONFIGS = [
    {
        "workspace": "workspace_output_tdd-c0d2da4616eb323c",
        "method_key": "trapezio",
        "label": "Trapézio Composto",
        "color": "#00BCD4",
        "marker": "D",
    },
    {
        "workspace": "workspace_output_tdd-a3f452e8f75c316f",
        "method_key": "simpson_1_3",
        "label": "Simpson 1/3",
        "color": "#9C27B0",
        "marker": "s",
    },
]

# Todos juntos (para compatibilidade e gráficos individuais)
EVALUATION_CONFIGS = ODE_CONFIGS + INTEGRACAO_CONFIGS

OUTPUT_DIR = PROJECT_ROOT / "script" / "loglog_plots"


# =============================================================================
#  CARREGAMENTO DOS RESULTADOS
# =============================================================================

def load_evaluation(workspace, method_key):
    """Carrega o arquivo JSON de avaliação de um método."""
    json_path = PROJECT_ROOT / workspace / "evaluation" / f"evaluation_{method_key}.json"
    if not json_path.exists():
        print(f"  [WARN] Arquivo não encontrado: {json_path}")
        return None
    with open(json_path) as f:
        return json.load(f)


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
#  GRÁFICO INDIVIDUAL
# =============================================================================

def plot_individual(ax, h_vals, errors, est_order, log_C, label, color, marker):
    """Plota regressão log-log em um eixo matplotlib."""
    log_h = np.log10(h_vals)
    log_e = np.log10(errors)

    ax.plot(log_h, log_e, marker, color=color, markersize=8,
            label='Erro observado', zorder=5)

    h_fit = np.linspace(min(log_h) - 0.15, max(log_h) + 0.15, 200)
    e_fit = est_order * h_fit + log_C
    ax.plot(h_fit, e_fit, '--', color='#555555', linewidth=2,
            label=f'Regressão: p = {est_order:.4f}')

    ax.set_xlabel('log₁₀(h)', fontsize=11)
    ax.set_ylabel('log₁₀(E)', fontsize=11)
    ax.set_title(label, fontsize=12, fontweight='bold')
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)


# =============================================================================
#  FUNÇÕES AUXILIARES PARA FILTRAR DADOS
# =============================================================================

def _extract_plot_data(cfg, data):
    """Extrai h_vals e errors filtrados de um resultado de avaliação."""
    if data is None:
        return None, None
    convergence = data.get("convergence", {})
    h_vals = convergence.get("h_vals", [])
    errors = convergence.get("errors", [])

    if not h_vals or not errors or len(h_vals) < 3:
        return None, None

    pairs = [(h, e) for h, e in zip(h_vals, errors) if e > 0]
    if len(pairs) < 3:
        return None, None
    h_vals_f, errors_f = zip(*pairs)
    return list(h_vals_f), list(errors_f)


# =============================================================================
#  PAINEL COMPARATIVO (por categoria)
# =============================================================================

def plot_comparison_panel(results, title_suffix=""):
    """Gera painel com gráficos individuais para um grupo de métodos."""
    n_methods = len(results)
    cols = min(3, n_methods)
    rows = (n_methods + cols - 1) // cols

    fig = plt.figure(figsize=(cols * 6.5, rows * 5.5))
    fig.suptitle(
        f'Regressão Log-Log do Erro Numérico{title_suffix}\n'
        'log(E) ≈ p·log(h) + log(C)',
        fontsize=16, fontweight='bold', y=1.01
    )

    gs = gridspec.GridSpec(rows, cols, figure=fig, hspace=0.45, wspace=0.35)

    for idx, (cfg, data) in enumerate(results):
        h_vals_f, errors_f = _extract_plot_data(cfg, data)
        if h_vals_f is None:
            continue

        est_order, log_C, r2 = compute_regression(h_vals_f, errors_f)

        row, col = divmod(idx, cols)
        ax = fig.add_subplot(gs[row, col])
        plot_individual(ax, h_vals_f, errors_f,
                        est_order, log_C, cfg["label"], cfg["color"], cfg["marker"])

        ax.annotate(f'R² = {r2:.4f}', xy=(0.97, 0.07),
                    xycoords='axes fraction', ha='right', fontsize=9,
                    color='#333333',
                    bbox=dict(boxstyle='round,pad=0.3', fc='#f5f5f5', ec='#cccccc', alpha=0.9))

    return fig


# =============================================================================
#  GRÁFICO OVERLAID (por categoria)
# =============================================================================

def plot_overlay(results, title_suffix=""):
    """Sobrepõe métodos de um grupo num único gráfico log-log."""
    fig, ax = plt.subplots(figsize=(10, 7))

    for cfg, data in results:
        h_vals_f, errors_f = _extract_plot_data(cfg, data)
        if h_vals_f is None:
            continue

        log_h = np.log10(h_vals_f)
        log_e = np.log10(errors_f)
        est_order, log_C, _ = compute_regression(h_vals_f, errors_f)

        ax.plot(log_h, log_e, cfg["marker"], color=cfg["color"],
                markersize=8, zorder=5)
        h_fit = np.linspace(min(log_h) - 0.1, max(log_h) + 0.1, 200)
        ax.plot(h_fit, est_order * h_fit + log_C, '-', color=cfg["color"],
                linewidth=2, label=f'{cfg["label"]} (p={est_order:.2f})')

    ax.set_xlabel('log₁₀(h)', fontsize=13)
    ax.set_ylabel('log₁₀(E)', fontsize=13)
    ax.set_title(
        f'Comparação de Métodos{title_suffix}\n'
        'log(E) ≈ p·log(h) + log(C)',
        fontsize=14, fontweight='bold')
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


# =============================================================================
#  RELATÓRIO TEXTO
# =============================================================================

def print_summary(results, group_label=""):
    if group_label:
        print(f"\n  ── {group_label} ──")
    print(f"  {'Método':<28}  {'p estimado':>10}  {'p esperado':>10}  {'R²':>8}  {'UT %':>6}")
    print(f"  {'-'*68}")
    for cfg, data in results:
        if data is None:
            print(f"  {cfg['label']:<28}  {'N/A':>10}  {'—':>10}  {'—':>8}  {'—':>6}")
            continue
        conv = data.get("convergence", {})
        ut   = data.get("unit_test_success", {})
        est  = conv.get("estimated_order")
        exp  = conv.get("expected_order")
        r2   = conv.get("r_squared")
        pct  = ut.get("success_rate_pct", "—")
        est_s = f"{est:.4f}" if est is not None else "N/A"
        exp_s = f"{exp:.1f}" if exp is not None else "—"
        r2_s  = f"{r2:.4f}" if r2 is not None else "—"
        print(f"  {cfg['label']:<28}  {est_s:>10}  {exp_s:>10}  {r2_s:>8}  {pct:>6}")


# =============================================================================
#  MAIN
# =============================================================================

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f" TDDAgents — Plotter de Regressão Log-Log")
    print(f"{'='*60}\n")

    # Carregar resultados separados por categoria
    ode_results = []
    for cfg in ODE_CONFIGS:
        print(f"  Carregando [ODE]: {cfg['label']} ...")
        data = load_evaluation(cfg["workspace"], cfg["method_key"])
        ode_results.append((cfg, data))

    integracao_results = []
    for cfg in INTEGRACAO_CONFIGS:
        print(f"  Carregando [INT]: {cfg['label']} ...")
        data = load_evaluation(cfg["workspace"], cfg["method_key"])
        integracao_results.append((cfg, data))

    all_results = ode_results + integracao_results

    # Resumo textual
    print(f"\n{'='*72}")
    print_summary(ode_results, "Métodos ODE (Aproximação de Função)")
    print_summary(integracao_results, "Métodos de Integração Numérica")
    print(f"{'='*72}\n")

    # Gráficos individuais por método
    for cfg, data in all_results:
        h_vals_f, errors_f = _extract_plot_data(cfg, data)
        if h_vals_f is None:
            print(f"  [SKIP] {cfg['label']}: dados insuficientes.")
            continue

        est_order, log_C, r2 = compute_regression(h_vals_f, errors_f)

        fig, ax = plt.subplots(figsize=(8, 6))
        plot_individual(ax, h_vals_f, errors_f, est_order, log_C,
                        cfg["label"], cfg["color"], cfg["marker"])
        ax.annotate(f'R² = {r2:.4f}', xy=(0.97, 0.07),
                    xycoords='axes fraction', ha='right', fontsize=10,
                    bbox=dict(boxstyle='round,pad=0.3', fc='#f5f5f5', ec='#cccccc'))
        fig.suptitle('log(E) ≈ p·log(h) + log(C)', fontsize=11, color='#555555')
        fig.tight_layout()

        out = OUTPUT_DIR / f"loglog_{cfg['method_key']}.png"
        fig.savefig(out, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"  Salvo: {out}")

    # ── Painéis comparativos separados ──

    # ODE
    panel_ode = plot_comparison_panel(ode_results, " — Métodos ODE")
    p_ode = OUTPUT_DIR / "loglog_comparison_ode.png"
    panel_ode.savefig(p_ode, dpi=150, bbox_inches='tight')
    plt.close(panel_ode)
    print(f"\n  Painel ODE salvo          : {p_ode}")

    # Integração
    panel_int = plot_comparison_panel(integracao_results, " — Métodos de Integração")
    p_int = OUTPUT_DIR / "loglog_comparison_integracao.png"
    panel_int.savefig(p_int, dpi=150, bbox_inches='tight')
    plt.close(panel_int)
    print(f"  Painel Integração salvo   : {p_int}")

    # ── Overlays separados ──

    overlay_ode = plot_overlay(ode_results, " — ODE (Aproximação de Função)")
    o_ode = OUTPUT_DIR / "loglog_overlay_ode.png"
    overlay_ode.savefig(o_ode, dpi=150, bbox_inches='tight')
    plt.close(overlay_ode)
    print(f"  Overlay ODE salvo         : {o_ode}")

    overlay_int = plot_overlay(integracao_results, " — Integração Numérica")
    o_int = OUTPUT_DIR / "loglog_overlay_integracao.png"
    overlay_int.savefig(o_int, dpi=150, bbox_inches='tight')
    plt.close(overlay_int)
    print(f"  Overlay Integração salvo  : {o_int}")

    print(f"\n  Todos os gráficos em: {OUTPUT_DIR}\n")


if __name__ == "__main__":
    main()
