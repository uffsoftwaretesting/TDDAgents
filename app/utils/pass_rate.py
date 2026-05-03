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
	failed_requirements: list[dict] = None,
	filename: str = "subreq_results.txt",
) -> None:
	"""Write a plain-text report for sub-requirement outcomes."""
	total = success_count + failure_count
	indexed = _index_results(subreq_results)

	f1_count = 0
	f2_count = 0
	for entry in indexed.values():
		if entry.get("status") != "success":
			continue
		flow_type = entry.get("flow_type", "")
		if flow_type == "F1":
			f1_count += 1
		elif flow_type == "F2":
			f2_count += 1

	denominator = total if total > 0 else 0
	if denominator:
		f1_rate = f1_count / denominator
		f2_rate = f2_count / denominator
		fail_rate = failure_count / denominator
	else:
		f1_rate = 0.0
		f2_rate = 0.0
		fail_rate = 0.0

	report_path = os.path.join(artifacts_dir, filename)

	with open(report_path, "w", encoding="utf-8") as f:
		f.write("SUBREQ RESULTS REPORT\n")
		f.write("=" * 80 + "\n")
		f.write("EXPLANATION OF METRICS:\n")
		f.write("F1 (Clean TDD): The sub-requirement was successfully implemented with a proper TDD cycle (failed tests first, then fixed).\n")
		f.write("F2 (Green in Red): The tests passed immediately during the Red phase, but the sub-requirement was still considered successful.\n")
		f.write("FAILURE: The sub-requirement failed to meet the criteria, max retries were exceeded or infrastructure issues occurred.\n")
		f.write("-" * 80 + "\n")
		f.write(f"Total sub-requirements: {total}\n")
		f.write(f"Success: {success_count}\n")
		f.write(f"Failed: {failure_count}\n")
		f.write(f"Clean TDD Rate (F1) = {f1_count} / {denominator} ({f1_rate:.1%})\n")
		f.write(f"Green in Red Rate (F2) = {f2_count} / {denominator} ({f2_rate:.1%})\n")
		f.write(f"Fail rate = {failure_count} / {denominator} ({fail_rate:.1%})\n")
		f.write("-" * 80 + "\n")

		if plan:
			for idx, req in enumerate(plan):
				entry = indexed.get(idx)
				if entry:
					status = entry.get("status", "unknown")
					label = "SUCCESS" if status == "success" else "FAILED" if status == "failed" else status.upper()
					flow_type = entry.get("flow_type", "")
					flow_label = f" [{flow_type}]" if status == "success" and flow_type else ""
					f.write(f"{idx + 1}. [{label:<7}]{flow_label} {req}\n")
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
				flow_type = entry.get("flow_type", "")
				flow_label = f" [{flow_type}]" if status == "success" and flow_type else ""
				f.write(f"{idx + 1}. [{label:<7}]{flow_label} {req}\n")
				if status != "success":
					reason = entry.get("reason", "")
					if reason:
						f.write(f"   Reason: {reason}\n")
		else:
			f.write("(no results)\n")

		f.write("=" * 80 + "\n")
		
		# Print details from failed_requirements attribute
		if failed_requirements:
			f.write("FAILED SUB-REQUIREMENTS DETAILS:\n")
			for fail_info in failed_requirements:
				f.write(f"- Requirement: {fail_info.get('requirement', 'Unknown')}\n")
				f.write(f"  Reason: {fail_info.get('reason', 'Unknown')}\n")
			f.write("=" * 80 + "\n")

	logging.info("Sub-requirement report saved at %s", report_path)
