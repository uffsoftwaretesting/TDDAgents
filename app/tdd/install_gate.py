"""
The install gate: a built-in PreToolUse hook that asks the user before the agent's Bash changes the machine
outside the project (installs software, elevates, unpacks a download, writes outside the project folder).

TDDAgents-only; upstream has no install-specific gate. Each part is upstream's own code or classification:

- the mechanism: a PreToolUse hook's `permissionDecision: "ask"` (`reference/claude-code/src/utils/hooks.ts`
  `parseHookOutput`), turned by `services/tools/toolHooks.ts` `resolveHookPermissionDecision` into the
  `forceDecision` that `hooks/useCanUseTool.tsx` and `cli/structuredIO.ts` use instead of
  `hasPermissionsToUseTool`, so `bypassPermissions` cannot turn it into an allow;
- the registration: a `HookCallback` with `internal: true` (`types/hooks.ts`), registered like
  `utils/sessionFileAccessHooks.ts` `registerSessionFileAccessHooks` (`setup.ts`) through
  `bootstrap/state.ts` `registerHookCallbacks`;
- "writes outside the project": upstream's path validation, reused (`tools/BashTool/pathValidation.ts`
  `COMMAND_OPERATION_TYPE`, `PATH_EXTRACTORS`, `validatePath` with `isPathAllowed`'s acceptEdits rule, and
  `validateOutputRedirections` with `utils/bash/commands.ts` `extractOutputRedirections`); a path built from
  shell expansion (`$HOME/.local`) counts as outside, as `validatePath` asks for it;
- "Code from External" and "Security Weaken" (the auto-mode rules, `utils/permissions/yoloClassifier.ts`):
  a download piped into a shell or an unpacker, elevation, and the system, global and runtime-manager
  installs of `INSTALL_MANAGERS`, the one table of TDDAgents' own;
- command analysis: `tools/BashTool/bashPermissions.ts` `stripSafeWrappers`, `stripAllLeadingEnvVars`,
  `BARE_SHELL_PREFIXES` (shells and runners that run their arguments) and `utils/bash/commands.ts`
  `splitCommand_DEPRECATED`.

The project's own installs from its manifests (`pip install -r`, `npm ci`, `.venv/bin/pip …`) and writes inside
it ask nothing. `setup` and the final run run outside the hook pipeline and are not gated; the user's approval
of the plan's project configuration is their consent.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.schemas.hooks import CallbackHook, HookMatcher
from app.tools.bash_tool.bash_permissions import (
    BASH_TOOL_NAME,
    strip_all_leading_env_vars,
    strip_safe_wrappers,
    validate_output_redirections,
)
from app.tools.bash_tool.path_validation import (
    COMMAND_OPERATION_TYPE,
    PATH_EXTRACTORS,
    parse_command_arguments,
    validate_path,
)
from app.types.permissions import PermissionBehavior, PermissionMode, ToolPermissionContext
from app.utils.bash.commands import extract_output_redirections, split_command

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

#: Unpackers: a download piped into one installs whatever it unpacks.
EXTRACTORS = frozenset({"tar", "bsdtar", "unzip", "7z", "7za", "gunzip", "gzip", "xz", "unxz", "bunzip2",
                        "zstd", "cpio", "unar"})

#: A download piped into a program: `curl … | sh`, `wget -qO- … | sudo bash`, `curl … | tar -xz`.
_PIPE_FROM_DOWNLOAD = re.compile(
    r"(?:^|[\s;&|(])(?:curl|wget|fetch)\b[^;&|]*\|\s*(?:sudo\s+(?:-\S+\s+)*)?(?:/\S*/)?(?P<program>[\w.+-]+)")

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


#: `python`, `python3`, `python3.12`: an interpreter that can run `-m pip`.
_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?")


def _classify_tokens(tokens: list[str], depth: int, cwd: str | None) -> str | None:
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
                return classify_install(args[at + 1], depth + 1, cwd=cwd)
        return None
    if name in RUNNERS:
        return _classify_tokens(_skip_runner_options(name, args), depth + 1, cwd)
    if _PYTHON.fullmatch(name) and args[:2] == ["-m", "pip"]:
        label, installs = INSTALL_MANAGERS["pip"]
        return f"installs software with {label}" if installs(args[2:]) else None
    manager = INSTALL_MANAGERS.get(name)
    if manager is not None and manager[1](args):
        return f"installs software with {manager[0]}"
    return None


#: The project's context for `validate_path`: in acceptEdits a write inside the working directory is
#: allowed, so what is left is a write outside it (`isPathAllowed`).
_PROJECT_WRITES = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)


def _outside(path: str, cwd: str) -> bool:
    """
    `validate_path` refused `path` because it is not in the project, not for a safety check inside it (the
    protected `.tddagents/`, which the normal permission flow already asks about). A path it could not place
    (shell expansion such as `$HOME/.local`) is not absolute, so it counts as outside.
    """
    allowed, resolved, _reason = validate_path(path, cwd, _PROJECT_WRITES, "create")
    if allowed:
        return False
    if not os.path.isabs(resolved):
        return True
    root = os.path.normpath(cwd)
    return os.path.commonpath([root, os.path.normpath(resolved)]) != root


def _write_outside_project(subcommand: str, cwd: str) -> str | None:
    """
    A create or write target outside the project, as upstream's path validation sees it
    (`pathValidation.ts` `COMMAND_OPERATION_TYPE`, `PATH_EXTRACTORS`, `validatePath`): `mkdir`, `touch`,
    `rm`, `rmdir`, `mv`, `cp`, `sed -i`. A path built from shell expansion (`$HOME/.local`) cannot be
    placed, so it counts as outside, as `validatePath` asks for it. `COMMAND_VALIDATOR` (mv/cp flags) is
    left out on purpose: it asks inside the project too.
    """
    args = parse_command_arguments(strip_safe_wrappers(subcommand))
    if not args:
        return None
    base, rest = args[0], args[1:]
    operation = COMMAND_OPERATION_TYPE.get(base)
    if operation not in ("write", "create"):
        return None
    if base == "sed" and not any(a in ("-i", "--in-place") or a.startswith(("-i", "--in-place=")) for a in rest):
        return None
    for path in PATH_EXTRACTORS[base](rest):
        if _outside(path, cwd):
            resolved = validate_path(path, cwd, _PROJECT_WRITES, operation)[1]
            return f"writes outside the project ({base} {resolved})"
    return None


def _redirect_outside_project(command: str, cwd: str) -> str | None:
    """An output redirection outside the project (`validateOutputRedirections`, `extractOutputRedirections`)."""
    extraction = extract_output_redirections(command)
    if extraction.has_dangerous_redirection:
        return "writes to a path built from shell expansion"
    if validate_output_redirections(extraction.redirections, cwd, _PROJECT_WRITES).behavior \
            == PermissionBehavior.PASSTHROUGH:
        return None
    target = next((r.target for r in extraction.redirections if r.target != "/dev/null" and _outside(r.target, cwd)),
                  None)
    return f"writes outside the project (> {target})" if target is not None else None


def classify_install(command: str, depth: int = 0, *, cwd: str | None = None) -> str | None:
    """
    What the command does that needs the user's permission, in a few words, or `None`:

    * a download piped into a shell or an unpacker ("Code from External" in the auto-mode rules);
    * elevation, or an install by a system, global or runtime package manager (`INSTALL_MANAGERS`);
    * with the project folder `cwd`: a create or write target, or an output redirection, outside it, as
      upstream's path validation classifies it.

    Compound commands are split (`splitCommand_DEPRECATED`), wrappers and leading variables seen through
    (`stripSafeWrappers`, `stripAllLeadingEnvVars`), and `bash -c '…'`, `env …`, `xargs …` classified by
    what they run. The project's own installs from its manifests (`pip install -r`, `npm ci`) ask nothing.
    """
    if not isinstance(command, str) or not command.strip() or depth > 8:
        return None
    for match in _PIPE_FROM_DOWNLOAD.finditer(command):
        program = match.group("program")
        if program in SHELLS:
            return "pipes a download into a shell (curl | sh)"
        if program in EXTRACTORS:
            return f"unpacks a download (curl | {program})"
    if cwd is not None:
        redirect = _redirect_outside_project(command, cwd)
        if redirect is not None:
            return redirect
    for subcommand in split_command(command):
        stripped = strip_safe_wrappers(strip_all_leading_env_vars(_NEW_LINE_PLACEHOLDER.sub(" ", subcommand).strip()))
        found = _classify_tokens(_tokens(stripped), depth, cwd)
        if found is None and cwd is not None:
            found = _write_outside_project(stripped, cwd)
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
    cwd = hook_input.get("cwd")
    found = classify_install(command, cwd=cwd if isinstance(cwd, str) and cwd else None) \
        if isinstance(command, str) else None
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
