import logging
import os


def _index_results(subreq_results: list[dict]) -> dict[int, dict]:
	indexed: dict[int, dict] = {}
	for entry in subreq_results or []:
		if not isinstance(entry, dict):
			continue
		index = entry.get("index")
		if isinstance(index, int):
			indexed[index] = entry
	return indexed


def write_pass_rate_report(
	artifacts_dir: str,
	plan: list[str],
	subreq_results: list[dict],
	success_count: int,
	failure_count: int,
	filename: str = "subreq_results.txt",
) -> None:
	"""Write a plain-text report for sub-requirement outcomes."""
	total = len(plan) if plan else len(subreq_results or [])
	report_path = os.path.join(artifacts_dir, filename)
	indexed = _index_results(subreq_results)

	with open(report_path, "w", encoding="utf-8") as f:
		f.write("SUBREQ RESULTS REPORT\n")
		f.write("=" * 80 + "\n")
		f.write(f"Total sub-requirements: {total}\n")
		f.write(f"Success: {success_count}\n")
		f.write(f"Failed: {failure_count}\n")
		f.write("-" * 80 + "\n")

		if plan:
			for idx, req in enumerate(plan):
				entry = indexed.get(idx)
				if entry:
					status = entry.get("status", "unknown")
					label = "SUCCESS" if status == "success" else "FAILED" if status == "failed" else status.upper()
					f.write(f"{idx + 1}. [{label:<7}] {req}\n")
					if status != "success":
						reason = entry.get("reason", "")
						if reason:
							f.write(f"   Reason: {reason}\n")
				else:
					f.write(f"{idx + 1}. [NOT_RUN] {req}\n")
		elif indexed:
			for idx in sorted(indexed):
				entry = indexed[idx]
				req = entry.get("requirement", "")
				status = entry.get("status", "unknown")
				label = "SUCCESS" if status == "success" else "FAILED" if status == "failed" else status.upper()
				f.write(f"{idx + 1}. [{label:<7}] {req}\n")
				if status != "success":
					reason = entry.get("reason", "")
					if reason:
						f.write(f"   Reason: {reason}\n")
		else:
			f.write("(no results)\n")

		f.write("=" * 80 + "\n")

	logging.info("Sub-requirement report saved at %s", report_path)
