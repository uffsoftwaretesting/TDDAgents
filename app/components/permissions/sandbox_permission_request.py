"""
The dialog for a network request the sandbox's allowlist does not settle.

Ported from `reference/claude-code/src/components/permissions/SandboxPermissionRequest.tsx` ->
`SandboxPermissionRequest`: the title "Network request outside of sandbox", the host, "Do you want to
allow this connection?", and three choices — Yes; Yes, and don't ask again for <host> (hidden under the
policy's `allowManagedDomainsOnly`); No (Esc). The REPL prints it as lines and reads the answer by
number (`app/screens/repl.py::Repl.ask_sandbox_permission`). Product substitution: "tell Claude" is
"tell TDDAgents".
"""

from __future__ import annotations

from dataclasses import dataclass

from app.components.permissions.permission_dialog import PermissionDialog

TITLE = "Network request outside of sandbox"
QUESTION = "Do you want to allow this connection?"
YES, YES_DONT_ASK_AGAIN, NO = "yes", "yes-dont-ask-again", "no"


@dataclass(frozen=True, slots=True)
class SandboxPermissionResponse:
    """`onUserResponse`'s `{allow, persistToSettings}`."""

    allow: bool
    persist_to_settings: bool


#: Esc and a refused connection: denied, nothing remembered.
DENIED = SandboxPermissionResponse(allow=False, persist_to_settings=False)

RESPONSES = {
    YES: SandboxPermissionResponse(allow=True, persist_to_settings=False),
    YES_DONT_ASK_AGAIN: SandboxPermissionResponse(allow=True, persist_to_settings=True),
    NO: DENIED,
}


def sandbox_permission_options(host: str, managed_domains_only: bool) -> list[tuple[str, str]]:
    """The choices, as (value, label)."""
    return [
        (YES, "Yes"),
        *([] if managed_domains_only else [(YES_DONT_ASK_AGAIN, f"Yes, and don't ask again for {host}")]),
        (NO, "No, and tell TDDAgents what to do differently (esc)"),
    ]


def sandbox_permission_dialog(host: str) -> PermissionDialog:
    return PermissionDialog(title=TITLE, body=f"Host: {host}", question=QUESTION)


def render_sandbox_permission_request(host: str, options: list[tuple[str, str]]) -> str:
    """The dialog as the REPL prints it, options numbered."""
    first, *rest = sandbox_permission_dialog(host).lines()
    lines = [f"⚠ {first}", *rest, *(f"  {i}. {label}" for i, (_value, label) in enumerate(options, start=1))]
    lines.append(f"Choose 1-{len(options)}.")
    return "\n".join(lines)


def parse_sandbox_permission_answer(options: list[tuple[str, str]], line: str) -> SandboxPermissionResponse | None:
    """A number picks its choice; anything else asks again."""
    text = line.strip()
    if not text.isdigit():
        return None
    index = int(text) - 1
    if not 0 <= index < len(options):
        return None
    return RESPONSES[options[index][0]]


__all__ = [
    "DENIED",
    "NO",
    "QUESTION",
    "TITLE",
    "YES",
    "YES_DONT_ASK_AGAIN",
    "SandboxPermissionResponse",
    "parse_sandbox_permission_answer",
    "render_sandbox_permission_request",
    "sandbox_permission_dialog",
    "sandbox_permission_options",
]
