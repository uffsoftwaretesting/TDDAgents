"""
`/doctor`'s Sandbox section.

Ported from `reference/claude-code/src/components/sandbox/SandboxDoctorSection.tsx` -> `SandboxDoctorSection`,
as lines: nothing on an unsupported platform, when the settings do not enable the sandbox, or when its
dependencies are all there; otherwise its status ("Missing dependencies" with errors, "Available (with
warnings)" with only warnings), each error and warning, and, with errors, where to find install
instructions.
"""

from __future__ import annotations

from typing import Any

MISSING_DEPENDENCIES = "Missing dependencies"
AVAILABLE_WITH_WARNINGS = "Available (with warnings)"
INSTALL_HINT = "└ Run /sandbox for install instructions"


def sandbox_doctor_section(sandbox: Any) -> list[str]:
    """`SandboxDoctorSection` for the run's `SandboxAdapter`."""
    if sandbox is None or not sandbox.is_supported_platform() or not sandbox.is_sandbox_enabled_in_settings():
        return []
    check = sandbox.check_dependencies()
    if not check.errors and not check.warnings:
        return []
    status = MISSING_DEPENDENCIES if check.errors else AVAILABLE_WITH_WARNINGS
    lines = ["Sandbox", f"└ Status: {status}", *(f"└ {error}" for error in check.errors),
             *(f"└ {warning}" for warning in check.warnings)]
    if check.errors:
        lines.append(INSTALL_HINT)
    return lines


__all__ = ["AVAILABLE_WITH_WARNINGS", "INSTALL_HINT", "MISSING_DEPENDENCIES", "sandbox_doctor_section"]
