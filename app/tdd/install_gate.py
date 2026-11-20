"""
The install gate: a built-in PreToolUse hook that asks the user before the agent's Bash installs software
on the machine (a runtime, a system or global package, `curl | sh`, `sudo`).

TDDAgents-only; upstream has no install-specific gate, and an unlisted install is an ordinary
non-allowlisted Bash command there. What it ports is the mechanism upstream gives a hook to start a user
question, and the precedent for a hook that is code rather than settings:

- `reference/claude-code/src/utils/hooks.ts` `parseHookOutput` (PreToolUse `permissionDecision: "ask"` and
  `permissionDecisionReason`), `services/tools/toolHooks.ts` `runPreToolUseHooks` / `resolveHookPermissionDecision`
  (the ask becomes `forceDecision`, which `hooks/useCanUseTool.tsx` and `cli/structuredIO.ts` use instead of
  `hasPermissionsToUseTool`, so `bypassPermissions` does not turn it into an allow);
- `utils/sessionFileAccessHooks.ts` `registerSessionFileAccessHooks` and `setup.ts` (a `HookCallback` with
  `internal: true`, registered through `bootstrap/state.ts` `registerHookCallbacks`), `types/hooks.ts`
  `HookCallback` / `HookCallbackMatcher`;
- `tools/BashTool/bashPermissions.ts` `stripSafeWrappers`, `stripAllLeadingEnvVars` and
  `BARE_SHELL_PREFIXES` (wrappers and shells that run their arguments as a command) for seeing through
  `timeout 5 sudo …`, `FOO=1 …`, `bash -c '…'`; `utils/bash/commands.ts` `splitCommand_DEPRECATED`.

What counts as an installation (TDDAgents' own table, `INSTALL_MANAGERS` and the helpers): what changes the
machine outside the project. Installing the project's own dependencies from its manifests (`pip install -r`,
`npm ci`, `bundle install`, `composer install`, `.venv/bin/pip …`) is not one: every TDD cycle does it.
`setup` and the final run run outside the hook pipeline and are not gated; the user's approval of the plan's
project configuration is their consent.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.schemas.hooks import CallbackHook, HookMatcher
from app.tools.bash_tool.bash_permissions import BASH_TOOL_NAME, strip_all_leading_env_vars, strip_safe_wrappers
from app.utils.bash.commands import split_command

#: `HookCallback.timeout` (seconds): the callback only reads the command.
INSTALL_GATE_TIMEOUT = 1

#: The reason's tail, after what the command does.
REASON_SUFFIX = "this changes the machine outside the project"

#: `BARE_SHELL_PREFIXES` (`bashPermissions.ts`): a shell run with `-c` executes its argument as a command.
SHELLS = frozenset({"sh", "bash", "zsh", "fish", "csh", "tcsh", "ksh", "dash"})
#: Commands that run the command they are given (`env`, `xargs`, and what `stripSafeWrappers` leaves behind).
RUNNERS = frozenset({"env", "xargs", "command", "exec", "builtin", "setsid", "ionice", "chronic"})
#: Elevation: always asked, whatever follows.
ELEVATORS = frozenset({"sudo", "su", "doas", "pkexec"})

#: A download piped into a shell: `curl … | sh`, `wget -qO- … | sudo bash`.
_PIPE_TO_SHELL = re.compile(
    r"(?:^|[\s;&|(])(?:curl|wget|fetch)\b[^;&|]*\|\s*(?:sudo\s+(?:-\S+\s+)*)?(?:/\S*/)?(?:"
    + "|".join(sorted(SHELLS)) + r")\b")

#: Verbs that install, per package manager (the first argument that is not an option).
_INSTALL_VERBS = frozenset({"install", "add", "upgrade", "dist-upgrade", "full-upgrade", "reinstall", "i"})


def _verbs(args: Sequence[str]) -> list[str]:
    """The arguments that are not options, in order."""
    return [arg for arg in args if not arg.startswith("-")]


def _has_flag(args: Sequence[str], *flags: str) -> bool:
    return any(arg in flags or any(arg.startswith(f"{flag}=") for flag in flags if flag.startswith("--"))
               for arg in args)


def _system_package_manager(args: Sequence[str]) -> bool:
    return any(verb in _INSTALL_VERBS for verb in _verbs(args))


def _pacman(args: Sequence[str]) -> bool:
    return any(arg.startswith(("-S", "-U")) and not arg.startswith(("-Ss", "-Si", "-Sl", "-Q")) for arg in args)


def _node_manager(args: Sequence[str]) -> bool:
    verbs = _verbs(args)
    if verbs[:1] == ["global"]:
        return True
    is_global = _has_flag(args, "-g", "--global", "--location=global")
    return is_global and any(verb in _INSTALL_VERBS | {"update"} for verb in verbs)


def _pip(args: Sequence[str]) -> bool:
    return bool(_verbs(args)[:1] == ["install"] and _has_flag(args, "--user", "--break-system-packages"))


def _first_verb_in(*verbs: str) -> Callable[[Sequence[str]], bool]:
    return lambda args: bool(_verbs(args)[:1]) and _verbs(args)[0] in verbs


def _verb_pair(first: str, *seconds: str) -> Callable[[Sequence[str]], bool]:
    return lambda args: len(_verbs(args)) >= 2 and _verbs(args)[0] == first and _verbs(args)[1] in seconds


def _dotnet(args: Sequence[str]) -> bool:
    return _verb_pair("tool", "install", "update")(args) and _has_flag(args, "-g", "--global")


def _composer(args: Sequence[str]) -> bool:
    return _verbs(args)[:1] == ["global"]


def _any_args(_args: Sequence[str]) -> bool:
    return True


#: The command's name -> (what it installs, whether these arguments install). The match is on the name
#: alone (a path is reduced to its last part), so `/usr/bin/apt install x` and `apt install x` are one.
INSTALL_MANAGERS: dict[str, tuple[str, Callable[[Sequence[str]], bool]]] = {
    "apt": ("apt", _system_package_manager), "apt-get": ("apt", _system_package_manager),
    "aptitude": ("apt", _system_package_manager), "dnf": ("dnf", _system_package_manager),
    "yum": ("yum", _system_package_manager), "zypper": ("zypper", _system_package_manager),
    "apk": ("apk", _first_verb_in("add", "upgrade")), "pacman": ("pacman", _pacman),
    "emerge": ("emerge", _any_args), "snap": ("snap", _first_verb_in("install", "refresh")),
    "flatpak": ("flatpak", _first_verb_in("install", "update")),
    "brew": ("brew", _first_verb_in("install", "reinstall", "upgrade")),
    "winget": ("winget", _first_verb_in("install", "upgrade")),
    "choco": ("choco", _first_verb_in("install", "upgrade")), "scoop": ("scoop", _first_verb_in("install")),
    "npm": ("npm (global)", _node_manager), "pnpm": ("pnpm (global)", _node_manager),
    "yarn": ("yarn (global)", _node_manager), "bun": ("bun (global)", _node_manager),
    "pip": ("pip (outside a virtual environment)", _pip), "pip3": ("pip (outside a virtual environment)", _pip),
    "pipx": ("pipx", _first_verb_in("install", "upgrade", "inject", "upgrade-all", "reinstall")),
    "uv": ("uv", lambda args: _verb_pair("python", "install")(args) or _verb_pair("tool", "install")(args)),
    "cargo": ("cargo", _first_verb_in("install")), "go": ("go", _first_verb_in("install")),
    "gem": ("gem", _first_verb_in("install", "update")), "composer": ("composer (global)", _composer),
    "dotnet": ("dotnet (global tool)", _dotnet),
    "nvm": ("nvm", _first_verb_in("install")), "fnm": ("fnm", _first_verb_in("install")),
    "n": ("n", _first_verb_in("install", "lts", "latest", "stable")),
    "pyenv": ("pyenv", _first_verb_in("install")), "rbenv": ("rbenv", _first_verb_in("install")),
    "jenv": ("jenv", _first_verb_in("add")), "asdf": ("asdf", _first_verb_in("install", "plugin")),
    "mise": ("mise", _first_verb_in("install", "use")), "rtx": ("rtx", _first_verb_in("install", "use")),
    "sdk": ("sdkman", _first_verb_in("install", "update", "upgrade")),
    "rustup": ("rustup", lambda args: _first_verb_in("install", "update", "default")(args)
               or _verb_pair("toolchain", "install", "add")(args)
               or _verb_pair("component", "add")(args) or _verb_pair("target", "add")(args)),
}


#: `splitCommand_DEPRECATED` leaves its newline placeholder at the start of a part that follows `&&` or `||`
#: and a line break; it is not a word of the command.
_NEW_LINE_PLACEHOLDER = re.compile(r"__NEW_LINE_[0-9a-f]+__")


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _name(token: str) -> str:
    """A command's name: its path reduced to the last part."""
    return os.path.basename(token)


def _skip_runner_options(runner: str, args: list[str]) -> list[str]:
    """What `env` / `xargs` / `exec` … runs: their own options and `VAR=value` assignments removed."""
    index = 0
    while index < len(args):
        arg = args[index]
        if runner == "env" and ("=" in arg and not arg.startswith("-")):
            index += 1
        elif arg.startswith("-") and arg != "--":
            index += 1
            # `env -u NAME`, `xargs -I {}`, `xargs -n 1`: the option takes the next word.
            if arg in ("-u", "-C", "-S", "-I", "-n", "-P", "-L", "-d", "-E") and index < len(args):
                index += 1
        elif arg == "--":
            index += 1
            break
        else:
            break
    return args[index:]


def _classify_tokens(tokens: list[str], depth: int) -> str | None:
    if not tokens or depth > 8:
        return None
    name = _name(tokens[0])
    args = tokens[1:]
    if name in ELEVATORS:
        return f"runs with elevated privileges ({name})"
    if name in SHELLS:
        if "-c" in args or any(arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:] for arg in args):
            at = next((i for i, arg in enumerate(args) if arg == "-c" or (
                arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:])), None)
            if at is not None and at + 1 < len(args):
                return classify_install(args[at + 1], depth + 1)
        return None
    if name in RUNNERS:
        return _classify_tokens(_skip_runner_options(name, args), depth + 1)
    if name in ("python", "python3") and args[:2] == ["-m", "pip"]:
        label, installs = INSTALL_MANAGERS["pip"]
        return f"installs software with {label}" if installs(args[2:]) else None
    manager = INSTALL_MANAGERS.get(name)
    if manager is not None and manager[1](args):
        return f"installs software with {manager[0]}"
    return None


def classify_install(command: str, depth: int = 0) -> str | None:
    """
    What the command does that needs the user's permission, in a few words, or `None`. Compound commands
    are split (`splitCommand_DEPRECATED`), wrappers and leading variables seen through (`stripSafeWrappers`,
    `stripAllLeadingEnvVars`), and `bash -c '…'`, `env …`, `xargs …` classified by what they run.
    """
    if not isinstance(command, str) or not command.strip() or depth > 8:
        return None
    if _PIPE_TO_SHELL.search(command):
        return "pipes a download into a shell (curl | sh)"
    for subcommand in split_command(command):
        stripped = strip_safe_wrappers(strip_all_leading_env_vars(_NEW_LINE_PLACEHOLDER.sub(" ", subcommand).strip()))
        found = _classify_tokens(_tokens(stripped), depth)
        if found is not None:
            return found
    return None


async def install_gate_callback(
    hook_input: Mapping[str, Any], tool_use_id: str | None = None, signal: Any = None,
) -> dict[str, Any]:
    """
    The `HookCallback` (`handleSessionFileAccess`'s shape): a PreToolUse `ask` for an install, `{}` for
    anything else, so the command takes the normal permission path.
    """
    tool_input = hook_input.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, Mapping) else None
    found = classify_install(command) if isinstance(command, str) else None
    if found is None:
        return {}
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": f"{found}: {REASON_SUFFIX}",
    }}


#: `registerSessionFileAccessHooks`' registration: an internal callback on one tool.
INSTALL_GATE_HOOKS: dict[str, tuple[HookMatcher, ...]] = {
    "PreToolUse": (HookMatcher(
        matcher=BASH_TOOL_NAME,
        hooks=(CallbackHook(callback=install_gate_callback, timeout=INSTALL_GATE_TIMEOUT, internal=True),),
    ),),
}


__all__ = ["INSTALL_GATE_HOOKS", "INSTALL_MANAGERS", "classify_install", "install_gate_callback"]
