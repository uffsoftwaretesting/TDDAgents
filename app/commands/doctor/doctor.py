"""
The `/doctor` command: diagnose the installation and the settings.

Ported from `reference/claude-code/src/commands/doctor/index.ts` (the description, with the product
substitution; `isEnabled`: off when `DISABLE_DOCTOR_COMMAND` is truthy; not `immediate`, so it waits for
the turn to end; registered in `app/commands/commands.py`) and `doctor.tsx` -> `call` (the `Doctor`
screen, `app/screens/doctor.py`; its result is what the REPL shows).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.components.design_system.dialog import DialogIO

if TYPE_CHECKING:
    from app.screens.doctor import DoctorContext

#: `commands/doctor/index.ts`' description.
DESCRIPTION = "Diagnose and verify your TDDAgents installation and settings"
#: The variable that turns the command off.
DISABLE_DOCTOR_COMMAND_ENV = "DISABLE_DOCTOR_COMMAND"


def is_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """`isEnabled`: `!isEnvTruthy(process.env.DISABLE_DOCTOR_COMMAND)`."""
    from app.utils.claudemd import is_env_truthy

    return not is_env_truthy((os.environ if environ is None else environ).get(DISABLE_DOCTOR_COMMAND_ENV))


async def call(io: DialogIO, context: DoctorContext) -> str:
    """`call(onDone)`: the screen; its result. The screen is loaded on use (upstream's `load`)."""
    from app.screens.doctor import doctor

    return await doctor(io, context)


__all__ = ["DESCRIPTION", "DISABLE_DOCTOR_COMMAND_ENV", "call", "is_enabled"]
