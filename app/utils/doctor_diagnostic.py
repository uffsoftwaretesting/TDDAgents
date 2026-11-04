"""
What `/doctor` says about the running installation.

Ported from `reference/claude-code/src/utils/doctorDiagnostic.ts` -> `InstallationType`, `DiagnosticInfo`,
`getCurrentInstallationType`, `getInstallationPath`, `getInvokedBinary`, `detectConfigurationIssues`,
`getDoctorDiagnostic`, for what this build has:

* TDDAgents always runs from its source tree (`python -m app.main`), which is upstream's `development`
  installation: the path is that tree, and only the configuration check that runs before upstream's
  `development` early return applies (the policy settings' `strictPluginOnlyCustomization` values that
  the schema silently drops). The version is `app/constants/product.py` -> `VERSION` (`MACRO.VERSION`).
* The search status is the session workspace's ripgrep (`app/utils/ripgrep.py` -> `get_ripgrep_status`).

*Divergence:* upstream's development path is the working directory; here it is the folder TDDAgents runs
from, which is what the line describes. Not ported, having no counterpart here: the package-manager,
npm, native and local installation checks (`detectMultipleInstallations`, the PATH and alias checks, the
leftover-installation warnings), the config install method, the auto-updater (`autoUpdates`,
`hasUpdatePermissions`) and the sandbox's Linux glob warnings (`detectLinuxGlobPatternWarnings`).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.constants.product import VERSION
from app.utils.ripgrep import get_ripgrep_status
from app.utils.settings.settings import MANAGED_SETTINGS_FILENAME, SettingsLocations
from app.utils.settings.types import CUSTOMIZATION_SURFACES

InstallationType = Literal["npm-global", "npm-local", "native", "package-manager", "development", "unknown"]

#: The folder TDDAgents runs from (the parent of the `app` package).
INSTALLATION_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class RipgrepStatus:
    """`DiagnosticInfo.ripgrepStatus`."""

    working: bool
    mode: str
    system_path: str | None


@dataclass(frozen=True, slots=True)
class ConfigurationWarning:
    """One `{issue, fix}` of `DiagnosticInfo.warnings`."""

    issue: str
    fix: str


@dataclass(frozen=True, slots=True)
class DiagnosticInfo:
    """`DiagnosticInfo`, the fields this build fills."""

    installation_type: InstallationType
    version: str
    installation_path: str
    invoked_binary: str
    ripgrep_status: RipgrepStatus
    warnings: tuple[ConfigurationWarning, ...] = field(default_factory=tuple)


def get_current_installation_type() -> InstallationType:
    """`getCurrentInstallationType`: always a source tree here."""
    return "development"


def get_invoked_binary() -> str:
    """`getInvokedBinary`: the script the interpreter runs (`process.argv[1]` upstream)."""
    return sys.argv[0] if sys.argv and sys.argv[0] else "unknown"


def _type_name(value: Any) -> str:
    """JavaScript's `typeof` for a JSON value that is not a boolean (`null` is an `object`)."""
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return "object"


def detect_configuration_issues(managed_dir: Path) -> list[ConfigurationWarning]:
    """
    `detectConfigurationIssues`, up to its `development` return: the policy file's
    `strictPluginOnlyCustomization` when the schema drops it whole, or drops some of its surfaces.
    """
    try:
        parsed = json.loads((managed_dir / MANAGED_SETTINGS_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # No policy file, or one that does not parse: the settings loader reports that.
        return []
    if not isinstance(parsed, dict) or "strictPluginOnlyCustomization" not in parsed:
        return []
    value = parsed["strictPluginOnlyCustomization"]
    if isinstance(value, bool):
        return []
    surfaces = ", ".join(CUSTOMIZATION_SURFACES)
    if not isinstance(value, list):
        return [ConfigurationWarning(
            issue=("managed-settings.json: strictPluginOnlyCustomization has an invalid value (expected true or an "
                   f"array, got {_type_name(value)})"),
            fix=("The field is silently ignored (schema .catch rescues it). Set it to true, or an array of: "
                 f"{surfaces}."))]
    unknown = [v for v in value if isinstance(v, str) and v not in CUSTOMIZATION_SURFACES]
    if not unknown:
        return []
    return [ConfigurationWarning(
        issue=(f"managed-settings.json: strictPluginOnlyCustomization has {len(unknown)} value(s) this client "
               f"doesn't recognize: {', '.join(unknown)}"),
        fix=(f"These are silently ignored (forwards-compat). Known surfaces for this version: {surfaces}. Either "
             "remove them, or this client is older than the managed-settings intended."))]


def get_doctor_diagnostic(locations: SettingsLocations | None, workspace: Any) -> DiagnosticInfo:
    """`getDoctorDiagnostic`."""
    mode, command, working = get_ripgrep_status(workspace)
    return DiagnosticInfo(
        installation_type=get_current_installation_type(),
        version=VERSION,
        installation_path=str(INSTALLATION_ROOT),
        invoked_binary=get_invoked_binary(),
        # "Assume working if not yet tested".
        ripgrep_status=RipgrepStatus(working=True if working is None else working, mode=mode,
                                     system_path=command if mode == "system" else None),
        warnings=tuple(detect_configuration_issues(locations.managed_dir)) if locations is not None else (),
    )


__all__ = [
    "INSTALLATION_ROOT",
    "ConfigurationWarning",
    "DiagnosticInfo",
    "InstallationType",
    "RipgrepStatus",
    "detect_configuration_issues",
    "get_current_installation_type",
    "get_doctor_diagnostic",
    "get_invoked_binary",
]
