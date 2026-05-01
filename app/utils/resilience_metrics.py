import os
import logging

def write_resilience_metrics(artifacts_dir: str, total_failures: int, corrected_failures: int, test_faults: int, implementation_faults: int):
    """Calcula e exporta a taxa de autocorreção e proporção de falhas para um arquivo de texto."""
    
    # Taxa Geral
    rate = 100.0 if total_failures == 0 else (corrected_failures / total_failures) * 100.0

    # Proporções de tipos de falhas
    total_red_green = test_faults + implementation_faults
    test_prop = (test_faults / total_red_green * 100) if total_red_green > 0 else 0.0
    impl_prop = (implementation_faults / total_red_green * 100) if total_red_green > 0 else 0.0

    metrics_path = os.path.join(artifacts_dir, "resilience_metrics.txt")
    
    with open(metrics_path, "w", encoding="utf-8") as f:
        f.write("RELATORIO DE METRICAS DE RESILIENCIA (TDD)\n")
        f.write("=" * 80 + "\n")
        f.write(f"Total de Falhas Detectadas (Geral):        {total_failures}\n")
        f.write(f"Falhas Corrigidas Autonomamente:           {corrected_failures}\n")
        f.write(f"Taxa de Sucesso de Autocorrecao:           {rate:.2f}%\n")
        f.write("-" * 80 + "\n")
        f.write("PROPORCAO DE TIPOS FALHAS (Testes vs Implementacao)\n")
        f.write(f"Falhas em Testes (Runner Red):             {test_faults} ({test_prop:.2f}%)\n")
        f.write(f"Falhas em Implementacao (Runner Green):    {implementation_faults} ({impl_prop:.2f}%)\n")
        f.write("=" * 80 + "\n")
        
    logging.info(f"📈 Métrica de resiliencia salva em {metrics_path}")