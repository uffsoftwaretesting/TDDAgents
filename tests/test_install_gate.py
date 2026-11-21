"""
The install gate (`app/tdd/install_gate.py`): a built-in PreToolUse hook that asks the user before the agent's
Bash installs software on the machine. Upstream's mechanism for a hook to start a question is `permissionDecision:
"ask"` (`utils/hooks.ts` `parseHookOutput`, `services/tools/toolHooks.ts` `resolveHookPermissionDecision`), and its
precedent for a built-in hook is `utils/sessionFileAccessHooks.ts` (`registerHookCallbacks`).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.schemas.hooks import CallbackHook, HookMatcher
from app.services.tools.tool_hooks import resolve_hook_permission_decision, run_pre_tool_use_hooks
from app.state.app_state_store import AppStateStore
from app.tdd.install_gate import (
    INSTALL_GATE_HOOKS,
    INSTALL_MANAGERS,
    REASON_SUFFIX,
    classify_install,
    install_gate_callback,
)
from app.tool import tool_context_for
from app.tools.bash_tool.bash_tool import build_bash_tool
from app.types.permissions import PermissionBehavior, PermissionMode, PermissionRule, ToolPermissionContext
from app.utils.hooks.hooks import HookDispatcher
from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot

# ── what counts as an installation ──────────────────────────────────────────────

INSTALLS = [
    ("apt install -y jq", "installs software with apt"),
    ("apt-get install jq", "installs software with apt"),
    ("apt upgrade", "installs software with apt"),
    ("/usr/bin/apt install jq", "installs software with apt"),
    ("dnf install jq", "installs software with dnf"),
    ("yum install -y jq", "installs software with yum"),
    ("zypper install jq", "installs software with zypper"),
    ("apk add jq", "installs software with apk"),
    ("pacman -S jq", "installs software with pacman"),
    ("pacman -Syu", "installs software with pacman"),
    ("snap install code", "installs software with snap"),
    ("brew install jq", "installs software with brew"),
    ("brew upgrade", "installs software with brew"),
    ("winget install x", "installs software with winget"),
    ("choco install x", "installs software with choco"),
    ("npm install -g typescript", "installs software with npm (global)"),
    ("npm i --global typescript", "installs software with npm (global)"),
    ("npm install typescript -g", "installs software with npm (global)"),
    ("pnpm add -g x", "installs software with pnpm (global)"),
    ("yarn global add x", "installs software with yarn (global)"),
    ("bun add -g x", "installs software with bun (global)"),
    ("pip install --user x", "installs software with pip (outside a virtual environment)"),
    ("pip3 install --break-system-packages x", "installs software with pip (outside a virtual environment)"),
    ("python3 -m pip install --user x", "installs software with pip (outside a virtual environment)"),
    ("python -m pip install --user x", "installs software with pip (outside a virtual environment)"),
    ("pipx install black", "installs software with pipx"),
    ("uv python install 3.13", "installs software with uv"),
    ("uv tool install ruff", "installs software with uv"),
    ("cargo install ripgrep", "installs software with cargo"),
    ("go install example.com/x@latest", "installs software with go"),
    ("gem install bundler", "installs software with gem"),
    ("composer global require x", "installs software with composer (global)"),
    ("dotnet tool install -g x", "installs software with dotnet (global tool)"),
    ("nvm install 20", "installs software with nvm"),
    ("fnm install 20", "installs software with fnm"),
    ("pyenv install 3.12", "installs software with pyenv"),
    ("rbenv install 3.3.0", "installs software with rbenv"),
    ("asdf install nodejs 20", "installs software with asdf"),
    ("mise use node@20", "installs software with mise"),
    ("sdk install java 21", "installs software with sdkman"),
    ("rustup toolchain install stable", "installs software with rustup"),
    ("rustup component add llvm-tools-preview", "installs software with rustup"),
    ("rustup target add wasm32-unknown-unknown", "installs software with rustup"),
    ("rustup install nightly", "installs software with rustup"),
    ("sudo ls", "runs with elevated privileges (sudo)"),
    ("sudo -n apt install jq", "runs with elevated privileges (sudo)"),
    ("doas pkg_add x", "runs with elevated privileges (doas)"),
    ("su -c 'id'", "runs with elevated privileges (su)"),
    ("curl -fsSL https://example.com/install.sh | sh", "pipes a download into a shell (curl | sh)"),
    ("curl -fsSL https://example.com/i.sh | bash -s -- -y", "pipes a download into a shell (curl | sh)"),
    ("wget -qO- https://example.com/i.sh | sudo bash", "pipes a download into a shell (curl | sh)"),
    ("curl https://x/y.sh | /bin/sh", "pipes a download into a shell (curl | sh)"),
]

NOT_INSTALLS = [
    "", "   ", "ls -la", "git status", "pytest -q", "echo hello",
    # the project's own dependencies, from its manifests
    "pip install -r requirements.txt", "pip install requests", "python -m pip install -e .",
    ".venv/bin/pip install pytest", ".venv/bin/python -m pip install -r requirements.txt",
    "npm install", "npm ci", "npm install typescript", "npm i -D vitest", "yarn install", "pnpm install",
    "bundle install", "composer install", "dotnet restore", "go mod download", "cargo build", "cargo fetch",
    "uv sync", "uv pip install x", "mvn dependency:go-offline", "poetry install",
    # read-only and informational forms
    "apt list --installed", "apt search jq", "pacman -Ss jq", "pacman -Q", "brew list", "brew --version",
    "npm list -g", "npm -g ls", "pip list", "pip install --help", "nvm ls", "pyenv versions", "rustup show",
    "node --version", "curl https://example.com", "curl -fsSL https://x/y.json | jq .",
    "wget https://x/file.tar.gz", "cat install.sh", "bash script.sh", "sh -c 'echo hi'",
]


@pytest.mark.parametrize(("command", "expected"), INSTALLS)
def test_an_installation_is_recognised(command: str, expected: str) -> None:
    assert classify_install(command) == expected


@pytest.mark.parametrize("command", NOT_INSTALLS)
def test_what_is_not_an_installation_is_left_alone(command: str) -> None:
    assert classify_install(command) is None


@pytest.mark.parametrize("command", [
    "cd project && sudo apt install jq", "make build; npm install -g typescript", "true || brew install jq",
    "echo x | pyenv install 3.12", "(cd a && cargo install ripgrep)", "ls\nsudo ls", "cd a &&\n  nvm install 20",
])
def test_an_installation_inside_a_compound_command_is_found(command: str) -> None:
    assert classify_install(command) is not None


@pytest.mark.parametrize("command", [
    "timeout 5 sudo ls", "nohup nvm install 20", "time brew install jq", "FOO=1 npm install -g typescript",
    "FOO=1 BAR=2 pyenv install 3.12", "env X=1 pyenv install 3.12", "env -u HOME pip install --user x",
    "xargs -n1 pip install --user", "bash -c 'brew install jq'", "sh -c \"cd a && sudo ls\"",
    "bash -lc 'nvm install 20'", "zsh -c 'apt install jq'", "command sudo ls", "exec nvm install 20",
    "bash -c \"bash -c 'sudo ls'\"",
])
def test_wrappers_and_shells_are_seen_through(command: str) -> None:
    assert classify_install(command) is not None


@pytest.mark.parametrize("command", [
    "timeout 5 ls", "FOO=1 pytest", "env X=1 pytest", "bash -c 'ls -la'", "xargs -n1 echo", "command ls",
    "bash -c", "bash -c ''",
])
def test_a_wrapper_around_something_harmless_is_harmless(command: str) -> None:
    assert classify_install(command) is None


def test_a_malformed_command_is_still_classified() -> None:
    assert classify_install("sudo 'unterminated") == "runs with elevated privileges (sudo)"
    assert classify_install("echo 'unterminated") is None


def test_a_non_string_is_not_an_installation() -> None:
    assert classify_install(None) is None  # type: ignore[arg-type]
    assert classify_install(42) is None  # type: ignore[arg-type]


def test_nesting_is_bounded() -> None:
    command = "echo hi"
    for _ in range(12):
        command = f"bash -c {command!r}"
    assert classify_install(command) is None
    assert classify_install(command.replace("echo hi", "sudo ls"), depth=0) is None


def test_every_manager_decides_on_its_own_arguments() -> None:
    for name, (label, installs) in INSTALL_MANAGERS.items():
        assert label
        assert installs(["--version"]) is False or name == "emerge", name


# ── the hook callback ───────────────────────────────────────────────────────────


def call(hook_input: Any) -> dict[str, Any]:
    return asyncio.run(install_gate_callback(hook_input, "t1", None))


def test_an_installation_asks_with_a_reason() -> None:
    output = call({"tool_name": "Bash", "tool_input": {"command": "nvm install 20"}})
    assert output == {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "ask",
        "permissionDecisionReason": f"installs software with nvm: {REASON_SUFFIX}"}}


def test_anything_else_says_nothing() -> None:
    assert call({"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}) == {}


@pytest.mark.parametrize("hook_input", [
    {}, {"tool_input": None}, {"tool_input": {}}, {"tool_input": {"command": None}},
    {"tool_input": {"command": 7}}, {"tool_input": "sudo ls"}, {"tool_name": "Bash"},
])
def test_an_input_without_a_command_says_nothing(hook_input: dict[str, Any]) -> None:
    assert call(hook_input) == {}


def test_the_callback_needs_only_the_hook_input() -> None:
    assert asyncio.run(install_gate_callback({"tool_input": {"command": "sudo ls"}}))


# ── the registration ────────────────────────────────────────────────────────────


def test_the_gate_is_an_internal_pretooluse_callback_on_bash() -> None:
    assert list(INSTALL_GATE_HOOKS) == ["PreToolUse"]
    [matcher] = INSTALL_GATE_HOOKS["PreToolUse"]
    assert isinstance(matcher, HookMatcher)
    assert matcher.matcher == "Bash"
    assert matcher.plugin_root is None
    [hook] = matcher.hooks
    assert isinstance(hook, CallbackHook)
    assert hook.internal is True
    assert hook.callback is install_gate_callback
    assert hook.timeout == 1
    assert hook.if_condition is None


# ── through the real hook pipeline ──────────────────────────────────────────────


def dispatcher(tmp_path: Any) -> HookDispatcher:
    dispatcher = HookDispatcher(snapshot=HooksConfigSnapshot(), session_id="s", cwd=str(tmp_path),
                                project_dir=str(tmp_path))
    dispatcher.register_hook_callbacks(INSTALL_GATE_HOOKS)
    return dispatcher


def context(tmp_path: Any, *, interactive: bool = True, mode: PermissionMode = PermissionMode.DEFAULT,
            deny: tuple[PermissionRule, ...] = ()) -> Any:
    return tool_context_for(
        AppStateStore(), hook_dispatcher=dispatcher(tmp_path),
        permission_context=ToolPermissionContext(mode=mode, should_avoid_permission_prompts=not interactive,
                                                 always_deny_rules=deny))


def decide(ctx: Any, command: str) -> Any:
    async def go() -> Any:
        outcome = await run_pre_tool_use_hooks(ctx, "Bash", {"command": command}, "t1")
        return await resolve_hook_permission_decision(outcome.verdict, build_bash_tool(), {"command": command}, ctx)

    return asyncio.run(go()).decision


def test_the_hook_turns_an_install_into_a_forced_ask_with_its_reason(tmp_path: Any) -> None:
    decision = decide(context(tmp_path), "nvm install 20")
    assert decision.behavior == PermissionBehavior.ASK
    assert decision.message == f"installs software with nvm: {REASON_SUFFIX}"
    assert decision.decision_reason["type"] == "hook"
    assert decision.decision_reason["hookName"] == "PreToolUse:Bash"


def test_bypass_permissions_still_asks(tmp_path: Any) -> None:
    decision = decide(context(tmp_path, mode=PermissionMode.BYPASS_PERMISSIONS), "sudo apt install jq")
    assert decision.behavior == PermissionBehavior.ASK


def test_accept_edits_and_plan_modes_still_ask(tmp_path: Any) -> None:
    for mode in (PermissionMode.ACCEPT_EDITS, PermissionMode.PLAN):
        assert decide(context(tmp_path, mode=mode), "brew install jq").behavior == PermissionBehavior.ASK


def test_without_a_prompt_the_install_is_denied_with_the_ask_message(tmp_path: Any) -> None:
    from app.utils.messages import auto_reject_message

    decision = decide(context(tmp_path, interactive=False), "nvm install 20")
    assert decision.behavior == PermissionBehavior.DENY
    assert decision.message == auto_reject_message("Bash")


def test_in_dont_ask_mode_the_install_is_denied(tmp_path: Any) -> None:
    from app.utils.messages import dont_ask_reject_message

    decision = decide(context(tmp_path, mode=PermissionMode.DONT_ASK), "nvm install 20")
    assert (decision.behavior, decision.message) == (PermissionBehavior.DENY, dont_ask_reject_message("Bash"))


def test_a_deny_rule_wins_over_the_ask(tmp_path: Any) -> None:
    rule = PermissionRule(tool_name="Bash", rule_content="sudo:*", rule_behavior=PermissionBehavior.DENY)
    decision = decide(context(tmp_path, deny=(rule,)), "sudo apt install jq")
    assert decision.behavior == PermissionBehavior.DENY
    assert "installs software" not in decision.message
    assert decision.decision_reason["type"] == "subcommandResults"
    assert decision.decision_reason["reasons"]["sudo apt install jq"].decision_reason == {"type": "rule", "rule": rule}


def test_a_permission_request_hook_can_answer_where_no_prompt_can_be_shown(tmp_path: Any) -> None:
    from tests.hook_doubles import ScriptedDispatcher
    from app.types.hooks import AggregatedHookResult

    outer = context(tmp_path, interactive=False)
    allow = AggregatedHookResult(permission_request_result={"behavior": "allow"})

    class Both(ScriptedDispatcher):
        def __init__(self) -> None:
            super().__init__(script={"PermissionRequest": [allow]})
            self.real = outer.hook_dispatcher

        async def execute_hooks(self, hook_input: Any, tool_use_id: str, **kwargs: Any) -> Any:
            if hook_input["hook_event_name"] == "PreToolUse":
                async for item in self.real.execute_hooks(hook_input, tool_use_id, **kwargs):
                    yield item
            else:
                async for item in super().execute_hooks(hook_input, tool_use_id, **kwargs):
                    yield item

    ctx = tool_context_for(AppStateStore(), hook_dispatcher=Both(),
                           permission_context=ToolPermissionContext(should_avoid_permission_prompts=True))
    assert decide(ctx, "nvm install 20").behavior == PermissionBehavior.ALLOW


def test_the_project_dependencies_are_not_asked_about(tmp_path: Any) -> None:
    for command in ("pip install -r requirements.txt", "npm ci", "composer install", ".venv/bin/pip install x"):
        ctx = context(tmp_path, interactive=False)
        outcome = asyncio.run(run_pre_tool_use_hooks(ctx, "Bash", {"command": command}, "t1"))
        assert outcome.verdict is None, command


def test_only_bash_calls_reach_the_gate(tmp_path: Any) -> None:
    ctx = context(tmp_path)
    outcome = asyncio.run(run_pre_tool_use_hooks(ctx, "Read", {"command": "sudo ls"}, "t1"))
    assert outcome.verdict is None


def test_an_unregistered_dispatcher_does_not_ask(tmp_path: Any) -> None:
    bare = HookDispatcher(snapshot=HooksConfigSnapshot(), session_id="s", cwd=str(tmp_path), project_dir=str(tmp_path))
    ctx = tool_context_for(AppStateStore(), hook_dispatcher=bare, permission_context=ToolPermissionContext())
    outcome = asyncio.run(run_pre_tool_use_hooks(ctx, "Bash", {"command": "sudo ls"}, "t1"))
    assert outcome.verdict is None


def test_the_registration_survives_dropping_the_plugin_hooks(tmp_path: Any) -> None:
    live = dispatcher(tmp_path)
    live.clear_registered_plugin_hooks()
    assert live.registered_matchers["PreToolUse"] == list(INSTALL_GATE_HOOKS["PreToolUse"])


# ── registration at session open ────────────────────────────────────────────────


def open_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, *, sandboxed: bool) -> HookDispatcher:
    """The dispatcher a session opens with, the sandbox faked on or left off (the suite's `sandbox_off_settings`)."""
    import app.cli.print as session
    import app.utils.hooks.hook_dispatcher as runtime
    from app.utils.sandbox.sandbox_adapter import SandboxAdapter
    from tests.conftest import FakeWorkspace
    from tests.session_run_helpers import run_item
    from tests.test_loop_hooks_mutation_a import Model, loop_deps
    from langchain_core.messages import AIMessage

    built: list[HookDispatcher] = []
    real_build = runtime.build_hook_dispatcher

    def capture(locations: Any, **kw: Any) -> HookDispatcher:
        fakes: dict[str, Any] = {"prompt_model_caller": object(), "agent_runner": object()}
        built.append(real_build(locations, **kw, **fakes))
        return built[-1]

    monkeypatch.setattr(runtime, "build_hook_dispatcher", capture)
    if sandboxed:
        monkeypatch.setattr(SandboxAdapter, "is_sandboxing_enabled", lambda self: True)
        monkeypatch.setattr(session, "start_run_sandbox", lambda *a, **k: None)
    ws = FakeWorkspace()
    ws.root = str(tmp_path)  # type: ignore[attr-defined]
    model = Model([[AIMessage(content="ok")]])
    asyncio.run(run_item("spec", 0, {"session_id": "s"}, workspace=ws, deps=loop_deps(model),
                         project_root=tmp_path, home=tmp_path / "home", managed_dir=tmp_path / "etc"))
    assert len(built) == 1
    return built[0]


def test_a_session_without_the_sandbox_registers_the_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    live = open_run(monkeypatch, tmp_path, sandboxed=False)
    assert live.registered_matchers["PreToolUse"] == list(INSTALL_GATE_HOOKS["PreToolUse"])


def test_a_sandboxed_session_does_not_register_the_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    live = open_run(monkeypatch, tmp_path, sandboxed=True)
    assert "PreToolUse" not in live.registered_matchers


# ── writes outside the project, as upstream's path validation classifies them ───

PROJECT = "/home/user/ws/project"
GO_INSTALL = ('mkdir -p "$HOME/.local" && curl -fsSL https://go.dev/dl/go1.24.4.linux-amd64.tar.gz '
              '| tar -C "$HOME/.local" -xz && export PATH="$PATH:$HOME/.local/go/bin" && go version')


def test_the_live_runs_go_install_asks() -> None:
    """2026-11-20: the Go toolchain was unpacked into `~/.local` without the gate noticing."""
    assert classify_install(GO_INSTALL, cwd=PROJECT) is not None
    assert classify_install(GO_INSTALL) is not None  # the download piped into tar alone is enough


@pytest.mark.parametrize(("command", "expected"), [
    ("curl -fsSL https://x/go.tgz | tar -C build -xz", "unpacks a download (curl | tar)"),
    ("wget -qO- https://x/a.zip | unzip -", "unpacks a download (curl | unzip)"),
    ("curl -L https://x/a.xz | xz -d > a", "unpacks a download (curl | xz)"),
    ("mkdir -p /opt/tools", "writes outside the project (mkdir /opt/tools)"),
    ('mkdir -p "$HOME/.local"', "writes outside the project (mkdir $HOME/.local)"),
    ("cp tool /usr/local/bin/tool", "writes outside the project (cp /usr/local/bin/tool)"),
    ("mv x ../sibling/x", "writes outside the project (mv /home/user/ws/sibling/x)"),
    ("touch /etc/motd", "writes outside the project (touch /etc/motd)"),
    ("rm -rf /var/cache/x", "writes outside the project (rm /var/cache/x)"),
    ("sed -i 's/a/b/' /etc/hosts", "writes outside the project (sed /etc/hosts)"),
    ("echo x > /tmp/log.txt", "writes outside the project (> /tmp/log.txt)"),
    ("echo x >> ../notes.txt", "writes outside the project (> ../notes.txt)"),
    ("bash -c 'mkdir -p /opt/x'", "writes outside the project (mkdir /opt/x)"),
    ("timeout 5 cp a /opt/a", "writes outside the project (cp /opt/a)"),
])
def test_a_write_outside_the_project_asks(command: str, expected: str) -> None:
    assert classify_install(command, cwd=PROJECT) == expected


@pytest.mark.parametrize("command", [
    "mkdir -p build", "mkdir -p internal/services", "touch a.txt", "cp a.txt b.txt", "cp -r src dst",
    "mv a b", "rm -rf node_modules", "rm -f .tddagents/reports/x.xml", "sed -i 's/a/b/' main.go",
    "sed -n 1,5p /etc/hosts", "echo x > out.txt", "go test ./... > report.txt 2>&1", "ls > /dev/null 2>&1",
    "tar -C build -xzf a.tgz", "unzip a.zip -d out", "go test -coverprofile=/tmp/c.out ./...",
    "cat /etc/hosts", "grep -rn x ~/go/pkg/mod", "npm ci", "pip install -r requirements.txt",
    "echo x > .tddagents/notes.txt",  # the protected folder: the normal flow asks, it is not outside
])
def test_work_inside_the_project_asks_nothing(command: str) -> None:
    assert classify_install(command, cwd=PROJECT) is None


def test_without_a_project_folder_only_installs_are_classified() -> None:
    assert classify_install("mkdir -p /opt/tools") is None
    assert classify_install("echo x > /tmp/log.txt") is None
    assert classify_install("sudo mkdir -p /opt/tools") == "runs with elevated privileges (sudo)"


def test_an_install_wins_over_a_write_in_the_same_subcommand() -> None:
    assert classify_install("sudo cp a /opt/a", cwd=PROJECT) == "runs with elevated privileges (sudo)"


@pytest.mark.parametrize("name", ["python", "python3", "python3.12", "/usr/bin/python3.11"])
def test_any_python_runs_pip(name: str) -> None:
    assert classify_install(f"{name} -m pip install --user x") == \
        "installs software with pip (outside a virtual environment)"


def test_the_callback_uses_the_hook_inputs_cwd() -> None:
    inside = call({"tool_name": "Bash", "cwd": PROJECT, "tool_input": {"command": "mkdir -p build"}})
    outside = call({"tool_name": "Bash", "cwd": PROJECT, "tool_input": {"command": "mkdir -p /opt/x"}})
    assert inside == {}
    assert outside["hookSpecificOutput"]["permissionDecisionReason"] == (
        f"writes outside the project (mkdir /opt/x): {REASON_SUFFIX}")


@pytest.mark.parametrize("cwd", [None, "", 7])
def test_a_missing_cwd_classifies_installs_only(cwd: Any) -> None:
    assert call({"cwd": cwd, "tool_input": {"command": "mkdir -p /opt/x"}}) == {}
    assert call({"cwd": cwd, "tool_input": {"command": "sudo ls"}}) != {}


def test_through_the_pipeline_a_write_outside_asks_in_bypass_mode(tmp_path: Any) -> None:
    ctx = context(tmp_path, mode=PermissionMode.BYPASS_PERMISSIONS)
    decision = decide(ctx, "cp tool /opt/tool")
    assert decision.behavior == PermissionBehavior.ASK
    assert "writes outside the project" in decision.message


# ── cd in a compound command (bashPermissions.ts compoundCommandHasCd; pathValidation.ts:645) ──


@pytest.mark.parametrize("command", [
    "cd /opt && mkdir x", "cd ~ && touch a", "cd && touch a", "cd $HOME/x && cp a b", "cd - && mkdir x",
    "cd /tmp; echo x > y", "cd internal && cd ../.. && mkdir z", "cd .. && mkdir sibling",
    "cd /opt && sed -i 's/a/b/' conf", "cd ~/.local && rm -rf go",
])
def test_a_cd_out_of_the_project_makes_a_later_write_outside(command: str) -> None:
    found = classify_install(command, cwd=PROJECT)
    assert found is not None and found.startswith("writes outside the project"), found


@pytest.mark.parametrize("command", [
    "cd internal && mkdir y", "cd internal/services && touch a.go", "cd .. && ls", "cd /opt && ls",
    "cd /tmp && cat x", "cd internal && echo x > out.txt", "cd internal && cd .. && mkdir build",
    "pushd /opt && mkdir x",  # pushd is not cd (upstream's cdCommands counts `cd` only)
])
def test_a_cd_inside_the_project_or_without_a_write_asks_nothing(command: str) -> None:
    assert classify_install(command, cwd=PROJECT) is None


def test_the_reason_names_the_folder_left() -> None:
    assert classify_install("cd /opt && mkdir x", cwd=PROJECT) == "writes outside the project (cd /opt, then mkdir)"


def test_without_a_project_folder_cd_is_not_judged() -> None:
    assert classify_install("cd /opt && mkdir x") is None


# ── more edge cases (pathValidation.ts validatePath; commands.ts extractOutputRedirections) ──


@pytest.mark.parametrize(("command", "outside"), [
    ('mkdir "/opt/my tools"', True),
    ("mkdir '/opt/a b'", True),
    ("mkdir ~root/x", True),            # tilde variants cannot be placed (validatePath asks)
    ("rm /opt/*", True),                # a glob in a write target cannot be placed
    ("rm build/*.o", True),             # … even inside the project, as upstream refuses globs in writes
    ("echo x 2>/tmp/err", True),
    ("make &> /tmp/build.log", True),
    ("echo x >| /opt/f", True),
    ("cat > /opt/conf <<'EOF'\nx\nEOF", True),
    ("echo x > /dev/null", False),
    ("echo x >/dev/null 2>&1", False),
    ("echo x 2>&1", False),
    ("mkdir ../../escape", True),
    ("mkdir a/../../escape", True),
    ("mkdir a/../b", False),
    ("touch ./a", False),
    ("cp a.txt /home/user/ws/project/b.txt", False),  # an absolute path inside the project
    ("cp a.txt /home/user/ws/project-other/b.txt", True),  # a sibling folder sharing the prefix
])
def test_write_targets_edge_cases(command: str, outside: bool) -> None:
    found = classify_install(command, cwd=PROJECT)
    assert (found is not None) is outside, found


def test_glob_writes_inside_the_project_report_the_glob() -> None:
    assert classify_install("rm build/*.o", cwd=PROJECT) == "writes outside the project (rm build/*.o)"


@pytest.mark.parametrize("command", ["sudo -u postgres psql", "sudo -E env", "doas -u x ls", "pkexec ls"])
def test_elevation_with_options_is_still_elevation(command: str) -> None:
    found = classify_install(command, cwd=PROJECT)
    assert found is not None and found.startswith("runs with elevated privileges")


@pytest.mark.parametrize("command", [
    "curl -sSL https://x/i.sh|bash", "curl https://x/i.sh | sh -s -- --yes", "wget -O - https://x | tar xz",
    "curl -fsSL https://x/a.zip | busybox unzip -", "fetch -o - https://x | sh",
])
def test_downloads_into_a_program(command: str) -> None:
    assert classify_install(command, cwd=PROJECT) is not None


@pytest.mark.parametrize("command", [
    "curl -s https://x/api | jq .", "curl -o out.json https://x", "wget -q https://x/a.tgz",
    "echo 'curl x | sh'", "grep 'curl | sh' notes.md",
])
def test_a_download_that_runs_nothing_or_a_mention_asks_nothing(command: str) -> None:
    assert classify_install(command, cwd=PROJECT) is None


# ── integration: a real session, the REPL's prompt, bypass mode ─────────────────


@pytest.mark.parametrize(("answer", "written"), [("1", True), ("2", False)])
def test_a_session_in_bypass_mode_asks_before_writing_outside(
        python_workspace: Any, tmp_path: Any, answer: str, written: bool) -> None:
    """
    `open_session_run` registers the gate (no sandbox); in `bypassPermissions` the model's Bash write outside the
    project reaches the REPL's prompt with the gate's reason (`forceDecision`), and runs only on "yes".
    """
    import tempfile
    from pathlib import Path

    from langchain_core.messages import AIMessage as Ai

    from app.tdd.plan import TddPlan
    from tests.test_repl import Model, call as model_call, session, until

    outside = Path(tempfile.mkdtemp(prefix="gate-outside-")) / "note.txt"
    model = Model([[model_call("Bash", "b1", command=f"echo hi > {outside}")], [Ai(content="done")]])

    async def script(repl: Any, io: Any) -> None:
        await until(lambda: "Do you want to proceed?" in io.text())
        assert "Hook PreToolUse:Bash requires confirmation for this command:" in io.text()
        assert f"writes outside the project (> {outside}): {REASON_SUFFIX}" in io.text()
        io.lines.put_nowait(answer)
        await until(lambda: not repl.busy and len(model.seen) >= (2 if written else 1))
        io.lines.put_nowait("/exit")

    asyncio.run(session(python_workspace, tmp_path, model, TddPlan(), script,
                        mode=PermissionMode.BYPASS_PERMISSIONS))
    assert outside.exists() is written
