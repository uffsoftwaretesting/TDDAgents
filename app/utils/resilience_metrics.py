import os
import logging

def write_resilience_metrics(artifacts_dir: str, total_failures: int, corrected_failures: int, test_faults: int, implementation_faults: int):
    """Computes and exports the self-correction rate and failure-type ratio to a text file."""

    # Overall rate
    rate = 100.0 if total_failures == 0 else (corrected_failures / total_failures) * 100.0

    # Failure-type ratios
    total_red_green = test_faults + implementation_faults
    test_prop = (test_faults / total_red_green * 100) if total_red_green > 0 else 0.0
    impl_prop = (implementation_faults / total_red_green * 100) if total_red_green > 0 else 0.0

    metrics_path = os.path.join(artifacts_dir, "resilience_metrics.txt")

    with open(metrics_path, "w", encoding="utf-8") as f:
        f.write("RESILIENCE METRICS REPORT (TDD)\n")
        f.write("=" * 80 + "\n")
        f.write(f"Total Detected Failures (Overall):         {total_failures}\n")
        f.write(f"Autonomously Corrected Failures:           {corrected_failures}\n")
        f.write(f"Self-Correction Success Rate:              {rate:.2f}%\n")
        f.write("-" * 80 + "\n")
        f.write("FAILURE TYPE RATIO (Tests vs Implementation)\n")
        f.write(f"Test Failures (Runner Red):                {test_faults} ({test_prop:.2f}%)\n")
        f.write(f"Implementation Failures (Runner Green):    {implementation_faults} ({impl_prop:.2f}%)\n")
        f.write("=" * 80 + "\n")

    logging.info(f"📈 Resilience metrics saved to {metrics_path}")