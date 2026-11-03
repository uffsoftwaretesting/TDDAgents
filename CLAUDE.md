# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**History lives in [`docs/claude_md_phase_record.md`](docs/claude_md_phase_record.md)**: the
per-phase record ("What Phase N changed"), every recorded divergence with its reason, the
mutation baselines and the survivor triage. Read the relevant phase there before changing a
subsystem it describes. The design is `docs/transition_elaboration_plan.md`, and the phase
outcomes are in `docs/refactoring_transition_plan.md`.

## Start-of-session ritual (mandatory)

Before doing **any** work in this repository (reading, planning, answering or editing):

1. **Read this entire `CLAUDE.md`.**
2. **Read the relevant source before changing it.** Never edit or reason about `app/` from
   memory or from this document alone: the source is the truth, and the two drift.
3. **Start from `reference/claude-code-map.md`** whenever the task touches Claude Code
   behavior, agent/tool design, or a pattern worth mirroring, and follow both of its routes
   (see [Claude Code reference](#claude-code-reference)). Never answer a question about
   Claude Code internals from memory when the source is in `reference/claude-code/`.
4. **Run the [Quality gate](#quality-gate-mandatory)** before code you create or refactor is
   done. Tests are written *with* the change, not after it.

**Decisions go to the user.** Every architecture decision, open question or ambiguity goes to
the user through `AskUserQuestion` before acting on it, never resolved silently by picking a
default. A `UserPromptSubmit` hook in `.claude/settings.json` restates this on every prompt.
The user's standing direction is **follow claude-code's source and architecture, while
enforcing the TDD cycle**. Each divergence from upstream is a recorded user decision.

## What this is

TDDAgents is a research prototype (UFF / SBES 2026). A multi-agent system turns a
natural-language request into tested code, in any language, by driving the Red–Green–Refactor
phases of TDD. It is **claude-code's loop (v2.1.88) ported to Python**, one conversation per session:
plan mode (the Analyst) writes the plan and gets it approved, then the same conversation
develops it item by item.

Generated code is written and run in a **per-session project folder outside TDDAgents' own**:
`~/.tddagents/workspaces/<thread_id>/project` (`TDDAGENTS_WORKSPACES_DIR` overrides). TDDAgents
prepares no environment: the project declares how it sets up and runs its tests in
`.tddagents/project.json` (see [The declared project](#subsystems-current-behaviour)). Reports go to
`<thread_id>/metrics_and_logging/`. `E2BWorkspace` remains available behind the `Workspace`
protocol; `E2B_API_KEY` is optional.

`pytest`, `flake8`, `mypy` and `mutmut` apply to **`app/` itself**. `tests/` is an offline
suite (no API keys, sandbox or database). The seam modules are verified end to end instead.

## The architecture — claude-code's loop, laid out like its source

**`app/` mirrors `reference/claude-code/src/` file for file, in snake_case**:
`src/tools/BashTool/bashPermissions.ts` is `app/tools/bash_tool/bash_permissions.py`. Where a
`.ts` file and a directory share a name, the file's code is `<name>/<name>.py` (`query.ts` →
`app/query/query.py`). Each module's docstring names what it ports in `Ported from` lines.
TDD-only code lives in **`app/tdd/`**, the session driver in **`app/session/`**.

| Path (`app/`) | Upstream | Ships |
|---|---|---|
| `main.py` | `entrypoints/cli.tsx`, `main.tsx` | Arguments, interactive / headless (`-p`), reports |
| `cli/print.py` | `cli/print.ts` | `open_session_run` → `DevelopmentRun`: MCP, SessionStart / UserPromptSubmit, one `QueryEngine` per session, the background-task wait loop, resume, SessionEnd; `run_headless` + `HeadlessStreaming` (the stream-json loop and every control request) |
| `cli/structured_io.py`, `utils/sdk_messages.py`, `utils/sdk_event_queue.py` | `cli/structuredIO.ts`, `queryHelpers.ts`, `sdkEventQueue.ts` | NDJSON in/out, control requests both ways, the host's `can_use_tool`; the SDK wire shapes; task / session-state events |
| `screens/repl.py`, `screens/terminal_io.py` | `screens/REPL.tsx` | The REPL: queued input, Esc abort, permission questions, slash commands; `terminal_io.py` is the `prompt_toolkit` seam |
| `commands/commands.py`, `commands/init.py`, `project_onboarding_state.py` | `commands.ts`, `commands/init.ts`, `projectOnboardingState.ts` | `local` slash commands, `/plan` operations; built-in prompt commands (`/init`, both upstream prompts, `TDDAGENTS_NEW_INIT`); the "Tips for getting started" onboarding |
| `hooks/use_can_use_tool.py`, `components/permissions/permission_options.py` | `useCanUseTool.tsx`, permission options | The interactive `canUseTool` and each tool's choices |
| `query_engine.py` | `QueryEngine.ts` | `submit`: transcript recorded before the loop, `ResultMessage` |
| `query/query.py` | `query.ts` | `run_loop` (yields `Terminated` last), `drain`, every continue/terminal site, the pre-model pipeline |
| `query/{state,transitions,config,deps,stop_hooks,withhold}.py` | `query.ts`, `query/*.ts` | `LoopState`, the `Continue` / `Terminal` vocabulary, `RunConfig` + `Gates`, `LoopDeps`, stop hooks (**TDD first**), recovery predicates |
| `tool.py`, `tools/tools.py` | `Tool.ts`, `tools.ts` | `Tool`, `build_tool` (fail-closed), `ToolContext`; `assemble_tool_pool` with phase deny rules |
| `tools/<name>_tool/` | `tools/<Name>Tool/` | One directory per tool, its `prompt.md` beside it. `tools/utils.py` holds what the five file tools share |
| `services/api/` | `claude.ts`, `withRetry.ts` | `stream_call_model` (one message per content block), cache breakpoints; `DEFAULT_MAX_RETRIES = 10` |
| `services/tools/` | `services/tools/` | `run_tool_use` pipeline, orchestration, streaming executor, tool hooks |
| `services/compact/` | `services/compact/` | Compaction, autocompact, microcompact, the compact prompt + TDD sections, forked summarizer |
| `services/mcp/` | `services/mcp/` | Config scopes, client, connection manager; MCP-server OAuth (`auth.py`; the TS SDK's `auth()` in `sdk_auth.py`), `needs-auth` and its 15-minute cache; elicitation (`elicitation_handler.py`); SDK servers over `mcp_message` (`sdk_control_transport.py`). claude.ai account OAuth is not ported |
| `cli/session_mcp.py`, `cli/mcp_auth_control.py` | `cli/print.ts` | `SessionMcp` (`DevelopmentRun.mcp`): the run's configured, SDK and dynamic servers, read live by the pools; reconnect / toggle / set-servers / elicitation routing. The `mcp_authenticate`, `mcp_oauth_callback_url` and `mcp_clear_auth` bodies, dependencies injected |
| `tools/mcp_auth_tool/` | `tools/McpAuthTool/` | `mcp__<server>__authenticate`, offered for `needs-auth` servers |
| `utils/plugins/`, `services/plugins/`, `plugins/`, `utils/dxt/`, `cli/handlers/plugins.py` | same | The plugin system: marketplaces, installation, loader, refresh, zip cache, MCPB bundles, autoupdate, startup checks, `--plugin-dir`; plugin commands/skills, agents, hooks and MCP servers in the session; `pluginOperations` and the `plugin` CLI; `/reload-plugins`, `reload_plugins`. The package folder is `.tddagents-plugin/` |
| `components/`, `hooks/notifs/` | `components/`, `hooks/notifs/` | The REPL's line-based dialogs: `/mcp` (`components/mcp/`), the MCP elicitation dialog, the trust dialog (`components/trust_dialog/`, `utils/config.py`); the plugin-autoupdate notice |
| `utils/secure_storage/` | `utils/secureStorage/` | The plaintext credential store (`<config home>/.credentials.json`, 0600) on every platform |
| `context.py`, `constants/system_prompt_sections.py` | `context.ts`, `prompts.ts` | System prompt + `tdd-contract.md`, system/user context, per-run memoization |
| `state/app_state_store.py` | `AppStateStore.ts` | `AppState` (ledger, plan, todos, permission context, last test run, tasks) |
| `task.py`, `tasks/`, `utils/task/` | `Task.ts`, `tasks/` | `LocalAgentTask`, `<task-notification>`, `stopTask` |
| `coordinator/coordinator_mode.py` | `coordinatorMode.ts` | Coordinator mode (`TDDAGENTS_COORDINATOR_MODE`, off by default) |
| `skills/` | `skills/` | Skill loader (bundled `app/prompts/skills` < policy < user < project), bundled-skill extraction; code-built bundled skills (`bundled_skills.py`, `bundled/`: `update-config`, `tdd-project-config`) |
| `cost_tracker.py`, `utils/model_cost.py` | `cost-tracker.ts`, `modelCost.ts` | `CostTracker` callback; Claude + OpenAI price tables |
| `utils/permissions/`, `utils/settings/`, `types/permissions.py` | same | `hasPermissionsToUseTool`, rules, filesystem, updates, dangerous patterns, denial tracking, the auto-mode classifier; the five settings sources |
| `utils/hooks/`, `schemas/hooks.py`, `types/hooks.py` | `utils/hooks*` | `executeHooks`, all four hook types, the config snapshot, SSRF guard, `build_hook_dispatcher` |
| `utils/attachments.py` | `attachments.ts` | Every ported attachment source (the TDD source is in `app/tdd/`) |
| `utils/` (rest) | `utils/*.ts` | Messages, queue, `claudemd.py`, session storage + resume, tokens, tool-result storage, ripgrep, file state, plans and slugs, plan mode |
| **`tdd/`** | — | `ledger.py`, `run_tests_tool/`, `phase_permissions.py` + `bash_phase_permissions.py`, `stop_hook.py`, `phase_on_spawn.py`, `tdd_state_attachment.py`, `events.py` + `event_log.py`, `plan.py` (`TddPlan`, `update_tdd_plan`), `plan_advance.py`, `tdd_plan_tool/` (`UpdatePlan`), `coverage_gate.py` (cobertura / lcov), `collection_gate.py`, `suite_fingerprint.py`, `project_config.py` (`.tddagents/project.json`), `junit_report.py`, `run_judgement.py` (`judge`), `final_run.py`, `prompt_vars.py`, `project_config_skill.py` |
| `session/` | — | `driver.py` (`develop_interactive` / `develop_headless`), `prompts.py` |
| `workspace/`, `sandbox/` | — | `Workspace` protocol, `LocalWorkspace` (strips TDDAgents' own interpreter from commands; pruned `walk_files`), `project.py`, `E2BWorkspace` over the E2B adapter |
| `prompts/` | prompts, bundled agents/skills | System sections, TDD agents (`AGENT.md` + `references/`), skills, compact / permission prompts, specs |

### Loop invariants (load-bearing, easy to undo by accident)

- **`LoopState` has no defaults, on purpose.** Every continue site names every field, so the
  reset-or-preserve decision is visible in the diff. A default lets a site stay silent about a
  field, which is what produced upstream's `compact → error → stop hook → compact` spiral.
- **Ceilings follow claude-code, nothing else.** Where upstream bounds something, port the
  bound **with upstream's constant and a citation**. Where upstream is unbounded, stay
  unbounded: **no turn limit by default**. Upstream's `--max-turns`, `--max-budget-usd` and
  `--json-schema` exist only as opt-in `-p` flags (`run_loop(max_turns=…)`, `QueryEngine`; user
  decision 2026-11-01). The retry layer keeps `withRetry`'s ceiling.
  `has_attempted_reactive_compact` is a latch, not a budget. Invent no other ceiling.
- **`run_loop` yields `Terminated` last** instead of returning it (PEP 525 forbids a value in
  an async generator's `return`). `drain` stands in for JS's `yield*` return.
- The **API-error guard** skips stop hooks and terminates `model_error`, never `completed`: a
  provider outage must never be recorded as an observed TDD cycle.
- The model emits **one message per content block**. Blocks of one response share a response
  id, and the request builder merges them back into one assistant message
  (`merge_split_assistant_messages`), or OpenAI rejects parallel tool calls.

## The TDD cycle: three barriers no model output can reach

1. **The ledger** (`app/tdd/ledger.py`: `phase, tests_written, green_passed, refactor_passed,
   coverage`). **`RunTests` is the only observation writer.** Phase-on-spawn (Agent tool,
   forked skills) and plan changes may move the phase or reset evidence, never create it.
   `test_run_tests_is_the_only_ledger_writer` pins exactly this. The ledger is **never
   restored** on resume; RED must be observed again.
2. **The phase permission tier**, in the gate above step 1a, **failing closed**, **only while
   the plan has a current item** (an empty or finished plan leaves the normal rules in charge):
   RED writes tests only, GREEN production only (tests locked), REFACTOR both. Test files are
   what the project's declared `testFiles` / `testSupportFiles` globs match. It covers the file
   tools, Bash redirect targets and the pool's deny rules. Agent memory, the session plan file and
   the declared manifests (`manifestFiles`) are exempt in every phase. With no declared
   configuration while an item is current, every write is denied (fail closed). A PreToolUse hook's `allow`, a hook `ask`, an
   auto-mode classifier allow and a PermissionRequest hook's `updatedInput` are all re-checked
   against it.
3. **The stop hook** (`app/tdd/stop_hook.py`) refuses to end an incomplete cycle, in any
   phase, with **no latch**. A turn ends early only on `/pause`, Esc, a configured hook's
   `continue: false`, a model/API error, or a denied `AskUserQuestion` headless. In mode
   `plan` the plan advance and the TDD hook are skipped.

**What each phase needs:**
- **RED**: a RunTests run (pass or fail) after the test files changed. The fingerprint is
  compared with `AppState.tdd_item_test_baseline`, taken when an item starts. A run that fails to
  load or compile its tests is a failed run, so it counts.
- **GREEN**: a passing run.
- **REFACTOR** (mandatory): a passing run. Coverage is measured and reported every run;
  `COVERAGE_RULE_OF_THUMB = 90` is a rule of thumb, **not a gate**. Tests assert behaviour and
  never only execute code to cover it.
- **A skipped test is not a pass**, and **an uncollected test file is not a pass**, in GREEN
  and REFACTOR. Every test must run in a plain RunTests run.

**RunTests reports what it observed, never a cause or advice** (as upstream's Bash). It runs the
project's declared `test` command (always the whole suite) with `TDDAGENTS_JUNIT_REPORT` /
`TDDAGENTS_COVERAGE_REPORT` pointing into `.tddagents/reports/`, then reports the exit code with
its shell meaning (`SHELL_EXIT_SEMANTICS`), what the JUnit report records (tests, failures, errors,
skips, the files it names), the coverage the declared cobertura/lcov report records, a
`TDD ledger:` line with the phase rule (`PHASE_RULES`), then the raw output. `judge` decides:
exit 0 is a pass only if the report records tests and no failures, errors or skips; any other
non-zero exit is a failed run; 126/127, signals, timeouts and a missing configuration are errors
and leave the ledger untouched. A test file the report does not name did not run ("unknown" when
the report names no files). A background worker's RunTests writes nothing.

**The plan advances only through the ledger.** `TddPlan` lives on `AppState` and in
`<session dir>/tdd-plan.json`. `plan_advance` runs between tool batches on the main thread and
first among the stop hooks: a complete cycle marks the item done and restarts the ledger with
the next item. `update_tdd_plan` is the one place the plan changes, and **any change of the
current item resets the ledger**. `UpdatePlan` has no "done" operation; it asks with a diff.
A wrong test in GREEN goes back by spawning the tester agent (phase-on-spawn restarts RED).

## Subsystems: current behaviour

- **Plan mode (the Analyst)** is upstream's: permission mode `plan`, a plan file
  (`<plans dir>/<slug>.md`), `EnterPlanMode` / `ExitPlanMode`. The plan's
  `## Implementation Plan` numbered items become the sub-requirements through
  `update_tdd_plan(replace_pending)`; a plan without items fails `validate_input`. The plan also
  carries a `## Project Configuration` fenced-json block (the declared project, below); a missing
  or invalid one fails `validate_input` too, naming the `tdd-project-config` skill. A session
  whose plan has no items opens in `plan`. **Plain `-p` denies `ExitPlanMode`**, so a new session
  plans and stops there; under `--input-format stream-json` the SDK host answers it.
- **Permissions** are `hasPermissionsToUseToolInner` step for step. The shipped
  `.tddagents/settings.json` says `"defaultMode": "acceptEdits"`. In `default` mode every
  write asks and headless denies it. **A test that drives a write through the pipeline must
  set `acceptEdits` (or an `Edit` allow rule).** Path rules are gitignore patterns on
  `Edit`/`Read`: `/x` from the workspace, `//x` from the filesystem root, `~/x` from home.
  **Dangerous allows such as `Bash(python:*)` are stripped** from editable sources on every
  run (they survive in policy and flag settings). An MCP call needs an allow rule.
  `auto` mode (the classifier) is opt-in and fails closed.
- **Interactive asks** go through `ToolContext.can_use_tool`; the TDD tier and deny rules are
  decided before any question. Workers and the agent-hook verifier have `can_use_tool=None`
  (headless deny), except a built-in `bubble` agent.
- **`AskUserQuestion`** is upstream's tool, main thread only. Headless denies it, and the
  session stops `stop_hook_prevented` with the questions; `--thread-id <id> "<answer>"`
  continues.
- **Hooks**: all four types and 27 events, from a frozen snapshot of the five settings
  sources, plus upstream's `callback` hooks (an SDK host's, `initialize.hooks`) and `function`
  hooks (`--json-schema`'s Stop enforcement). Hook output becomes messages. Configured stop hooks
  can only *add* blocks.
- **`-p` is the SDK protocol** (`app/cli/structured_io.py`, `run_headless`): a text prompt is the
  one user message of a closed input; `--output-format text|json|stream-json` (stream-json needs
  `--verbose`), `--input-format stream-json` (user messages and every control request on stdin;
  every permission 'ask' goes to the host as `can_use_tool`, a changed input re-checked against
  the TDD tier). **The exit code is the last result's `is_error`**, never the plan. Banners and
  reports go to stderr. The SDK `session_id` is the thread id; TDD state streams as
  `system/tdd_ledger|tdd_plan|tdd_transition`. Thinking is off unless asked (`--thinking`,
  `--max-thinking-tokens`); a custom `--system-prompt` keeps `tdd-contract.md`.
- **Compaction** summarizes the whole history through the forked, cache-sharing summarizer
  (same system prompt, tools and model as the main thread), then falls back to the regular
  request. TDD state is re-attached after the boundary from authoritative state (ledger and
  last `RunTests` record), never from the summary.
- **Agents**: workers run a second `run_loop` sharing the parent's ledger (synchronous).
  Background agents are allowed **only without a TDD `phase`**, because an async worker's
  ledger writes are discarded. Coordinator workers run synchronously. There is no
  general-purpose agent; `subagent_type` is required. The verification agent is opt-in
  (`TDDAGENTS_VERIFICATION_AGENT`).
- **Transcripts** are at `<config home>/projects/<sanitized cwd>/<run id>.jsonl`
  (`TDDAGENTS_CONFIG_DIR`, default `~/.tddagents`); events at `<run id>/events.jsonl`.
  Resume uses the transcript, `tdd-plan.json` and the plan slug; there is no checkpoint store.
- **The project is the root of every lookup** (settings, skills, agents, `.mcp.json`, hooks),
  never `Path.cwd()`. Absolute paths are real paths, bounded to the project
  (`normalize_path`). TDDAgents never runs `git init` and never commits.
  `GIT_CEILING_DIRECTORIES` isolates each project. TDDAgents prepares no environment and strips
  its own interpreter from the commands' `PATH`; an external `TDDAGENTS_ENV_FILE` still applies.
- **The declared project** (`app/tdd/project_config.py`): `.tddagents/project.json`, written by
  `ExitPlanMode` on approval into the protected `.tddagents/` and loaded at session open: `setup`,
  `test`, `junitReport`, `coverageReport` (`format` cobertura | lcov, optional `path`), `testFiles`,
  `testSupportFiles`, `manifestFiles`, `exclude`. The contract tells the model to declare every
  dependency it installs in the project's own manifest. The `tdd-project-config` skill (claude-api
  pattern) injects the reference for the project's ecosystem.
- **The final run** (`app/tdd/final_run.py`) copies the project into a clean folder (upstream's
  ripgrep listing with `--follow`, so every `.gitignore` applies and links are recreated as links;
  broken or looping links are left out), runs the
  declared `setup` then `test` there, judges it with the same `judge`, writes
  `metrics_and_logging/final_test_run.txt` and deletes the copy: a dependency installed but not
  declared reads "NOT passing".
- **Models**: `Config.CHAT_MODEL = "anthropic"`, `Config.MODEL = "claude-haiku-5-5"`; the
  small fast model is Claude Haiku 4.5 on a Claude run (`TDDAGENTS_SMALL_FAST_MODEL` wins).
  Anthropic clients get `get_max_output_tokens_for_model` unless a caller passes one.
  Unknown-model cost is 0 with `has_unknown_model_cost`.

### Prompt conventions

- **No templating engine.** Markdown in English with `{{PLACEHOLDER}}` substitution. An
  unknown placeholder stays visible rather than rendering empty.
- **Directory per unit**: `AGENT.md` / `SKILL.md` plus `references/` loaded on demand. A
  tool's prompt is `prompt.md` beside the tool.
- **Upstream texts are verbatim** (external build, our tool names), pinned by
  `tests/test_system_prompt_sections.py` and `tests/test_doing_tasks_prompt.py` against the
  reference snapshot. Product substitutions: "Claude Code" → "TDDAgents", "Anthropic's
  official CLI for Claude" → "UFF Software Testing Team's official CLI tool for TDDAgents",
  `CLAUDE.md` → `TDDAGENTS.md`.
- **No values in prose.** No model id, path, threshold or count in a prompt file: each has one
  resolution point in code and reaches the prompt as a placeholder (`TDD_PROMPT_VARS`).
- **No invented defaults in frontmatter.** Omission means unset. `max_turns: 8` is exactly the
  anti-pattern: upstream's `maxTurns` is `number | undefined`, and falsy means unbounded.
- **Invalid frontmatter is logged and ignored**, except an invalid `phase`, which must fail the
  load loudly: dropping it silently disables the TDD invariant.

## Quality gate (mandatory)

Every piece of code created or refactored in `app/` passes this gate before it is done. Run
the steps **in this order**.

```bash
P=.venv/bin      # the repo-local, gitignored venv; see Commands

# 1. Unit tests — written alongside the change, never bolted on afterwards
$P/pytest tests/ -q

# 2. Lint — zero findings (ruff too, with flake8's codes listed as external)
$P/flake8 app tests

# 3. Types — zero errors (strict, all of app/ and tests/)
$P/mypy

# 4. Mutation testing — do the tests actually assert anything?
TMPDIR=<a dir on disk> $P/mutmut run --max-children 8    # NOT `python -m mutmut`
$P/python scripts/mutmut_score.py --timeouts             # the timed-out mutants
$P/mutmut run --max-children 1 <those names>             # re-run each alone, on an idle host
$P/python scripts/mutmut_score.py --timeouts-reverified  # the gate score, and the triage list
```

- **Step 1**: a module behind a protocol seam is tested against a fake, never a live LLM or
  sandbox. Reuse `FakeWorkspace` / `make_fake_workspace` from `tests/conftest.py`.
- **Steps 2–3** cover the whole tree. Config: `setup.cfg` (flake8, `max-line-length = 120`),
  `pyproject.toml` (mypy `strict = true`, pytest, mutmut). Every deliberate catch-all carries
  `# noqa: BLE001 - <reason>`.
- **Step 4: ≥ 90% detected**, `(killed + timeout) / total`, a timeout counting only once
  re-verified alone (one that passes is a survivor, one that still hangs counts as detected).
  Then **triage every survivor**: kill it with a test, or record why it is equivalent or
  unreachable. Recorded equivalent classes: log wording and arguments, codec aliases
  (`"utf-8"` → `"UTF-8"`), default arguments, `False` → `None`, `getattr` defaults on
  attributes that always exist, dataclass defaults. Deleting dead code beats covering it.
- **Watch the host**: report an overloaded host (load far above the cores, under ~4 GB free)
  instead of reading its numbers. 8 children is the agreed setting.

### Exempt: the seam modules

| Module | Boundary |
|---|---|
| `app/sandbox/adapter.py` | the E2B SDK |
| `app/utils/chat_model_factory.py` | the LLM provider |
| `app/screens/terminal_io.py` | the terminal (`prompt_toolkit` on a real TTY) |

`app/utils/token_metrics.py` and `app/config/` are also outside the mutation set. All are in
`[tool.mutmut] do_not_mutate`; everything else under `app/` is mutated
(`paths_to_mutate = ["app/"]`). Each names its exemption in its docstring and requires instead
**a static surface check** (every SDK method and kwarg it calls exists on the installed
package) **and an end-to-end pipeline run**. Code layered above them is not exempt:
`app/workspace/e2b.py` is tested against a fake adapter, the pattern to copy.

### Gotchas

- **Invoke `mutmut run`, never `python -m mutmut`**: under `-m` each forked child re-runs
  `set_start_method('fork')` and the run dies with `context has already been set`.
- **Run mutmut with `TMPDIR` on disk** and clear it afterwards; the 9.8G `/tmp` tmpfs fills.
- **`also_copy` must name every non-code file a test reads**, in directory form:
  `["app/", "requirements.txt", ".tddagents/", "docs/", "scripts/"]`. A missing one makes stats
  collection fail ("not checked"), or every mutant die of `ImportError` (a fake 100%).
- **Verify a survivor from inside `mutants/`**, with `MUTANT_UNDER_TEST=""` as the baseline.
  From the snapshot root the unmutated `app/` is imported; left unset, the trampoline raises
  `KeyError` and every mutant looks killed. A subprocess test gives its child
  `MUTANT_UNDER_TEST=""`.
- **Never build the thing under test at test-module import**: collection-time code is
  attributed to no test, so its mutants all "survive".
- **Default-argument mutations are equivalent by construction**: mutmut resolves defaults on
  the public wrapper and passes every argument positionally into the mutant.
- **Source-reading tests go through `tests/source_scan.py` (`original_nodes`)**, or they see
  every mutant copy.
- **A top-level async generator whose cleanup matters** must be a plain function returning a
  nested generator (as `run_agent`): mutmut's trampoline never forwards `aclose()`.
- **Count survivors with `scripts/mutmut_score.py`**, never by grouping `mutmut results`
  lines (that output includes timeouts).
- **Watch for assertions that pass for the wrong reason**: where output and input can share
  text, make them differ (e.g. put a hook's body in a file, so its command and its output
  share nothing).
- `tests/conftest.py` seeds `OPENAI_API_KEY` / `E2B_API_KEY` before any `app.*` import,
  because `config.py` raises at import without `OPENAI_API_KEY`. An autouse fixture points
  `TDDAGENTS_CONFIG_DIR` at a temp dir; the suite has its own bundled-skills temp root.
- **Live tests** (`@pytest.mark.live`) run only with `RUN_LIVE_LLM_TESTS=1`;
  `config.py` calls `load_dotenv()`, so a key in `.env` would otherwise turn skips into calls.
- **The integration tests' venv is built once** (`shared_test_venv`, under `$TMPDIR`, behind a
  file lock), with plain `venv` + `pip`; it is a test asset, not part of TDDAgents.
- **Real-pytest integration tests use the `py_project` fixture** (alias `python_workspace`): a
  project whose `project.json` declares the shared venv's pytest. `script_project` is a fake
  POSIX-sh ecosystem for the language-neutral core and the final run.
- **A test asserts no `sleep 30` process exists** (`test_execute_timeout_kills_the_whole_group`):
  don't keep one running on the host during the suite.
- `--coordinator` writes `os.environ`; a fixture must `setenv` before `delenv` or the mode
  leaks into later tests.

## Commands

```bash
# Setup
cp .env.example .env          # then fill ANTHROPIC_API_KEY, OPENAI_API_KEY (E2B_API_KEY optional)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # also vendors rg, types-PyYAML

# Interactive (from the repo root: the spec menu reads app/prompts/specs relatively)
python -m app.main

# Headless (upstream `claude -p`): ExitPlanMode is denied, so a new session plans and stops.
# stdout carries only the result; the exit code is the result's is_error
python -m app.main -p --spec app/prompts/specs/<file>.txt
echo "Build a calculator" | python -m app.main -p --output-format json
python -m app.main -p --input-format stream-json --output-format stream-json --verbose   # SDK host

python -m app.main --thread-id "tdd-<hash>" ["<next message>"]   # continue a session (= --resume)
python -m app.main -c | --resume [id] | --fork-session | --session-id <uuid>
python -m app.main --fresh                                       # new thread
python -m app.main plugin list | install <p@m> | marketplace add <src> …   # [--thread-id <id>] for project/local scope
# Flags: --permission-mode, --settings <file>, --dangerously-skip-permissions, --coordinator,
# --system-prompt[-file], --append-system-prompt[-file], --agents <json>, --rewind-files <uuid>;
# -p only: --max-turns, --max-budget-usd, --json-schema, --include-partial-messages,
# --replay-user-messages, --include-hook-events, --permission-prompt-tool <mcp tool|stdio>
```

The REPL opens in plan mode (`⏸ plan mode on`). `ExitPlanMode` shows upstream's choices
("Yes", "No, keep planning" with feedback, or `/edit`). Afterwards the same conversation
develops the plan. Type at any time; Esc interrupts. `/help` lists `/plan`, `/pause`,
`/resume`, `/requirements`, `/context`, `/mcp`, `/reload-plugins`, `/init` and `/exit` (resumable
with `--thread-id`). A new interactive session first asks to trust its folder (upstream's trust dialog),
then to approve the project's pending `.mcp.json` servers; `-p` treats trust as implicit and
approves them, as `claude -p`.

**Experiment tooling:** `python mutation_tests/<task>/run_mutation_tests.py` (hardcoded
`/home/amaro/...` paths; edit before running). SonarQube: `docker compose -f
docker-compose.sonarqube.yaml up -d`, then `backup/analyze.sh` + `backup/sonar-project.properties`
in the workspace with `SONAR_TOKEN` set.

## Claude Code reference

```
reference/
├── claude-code-map.md      # the index — always read first
├── claude-code/            # source snapshot v2.1.88, gitignored (~35MB)
└── analysis/
    ├── 01-dive-into-claude-code.md            # arXiv paper — the main reference ("why")
    ├── 02-inside-claude-code-leaked-source.md # O-mega article ("what is surprising")
    ├── 03-architecture-deep-dive-hasan.md     # Zain Hasan ("whole system"); transcribed from a PDF, verify figures
    ├── decode/00.md … 11.md                   # code-level teardown ("how X works")
    └── assets/
```

- **Read `reference/claude-code-map.md` before grepping `reference/claude-code/`.** The map is
  a router, not an answer: a lookup is finished only after you read the cited analysis section
  (the *why*) **and** the cited code (the *how*, and the truth).
- **Where an analysis and the code disagree, the code wins.** Check the map's "Known
  divergences" first.
- **A missing file does not mean a missing symbol.** A module exporting only types is erased
  from the source map (37 modules, including `src/query/transitions.ts`). If the import reads
  `import type`, the symbol is real.
- The snapshot is reproducible with `rsync -a ~/claude-code-2.1.88/source/src reference/claude-code/`.
  The old `~/claude-code` mirror and the `claude-code-explorer` MCP server are gone.
- **Verify before you cite.** A claude-code claim added to a repository document must name a
  path and symbol that exist, and both checks must pass:
  ```bash
  python3 scripts/reference-checks/verify_paths.py   <doc.md>...
  python3 scripts/reference-checks/verify_anchors.py <doc.md>...
  ```

## Runtime path

`app/main.py` → `app/session/driver.py` → `develop_interactive` (REPL) or `develop_headless`
(`-p`). `open_session_run` (`app/cli/print.py`) builds the run once per session: `RunConfig`,
permission context, hook dispatcher, MCP, skills, transcript store, cost tracker, and an
`AppStateStore` holding the `TddPlan` and a ledger in RED. It fires SessionStart. Each turn
fires UserPromptSubmit and goes through `QueryEngine.submit` → `run_loop`. Each iteration:
the pre-model pipeline (boundary → tool-result budget → microcompact → autocompact → blocking
precheck), `stream_call_model`, the tool calls (`run_tools` / `StreamingToolExecutor` →
`run_tool_use`: validation → `validate_input` → PreToolUse → gate → call → governance →
PostToolUse), the turn's attachments, then a continue reason or a terminal. `main.py` writes
`approved_plan.md`, `planning_dialogue.txt` and `subreq_results.txt`.

## Conventions

- **Everything is in English**: prompts, logs, docstrings, comments, identifiers.
- **Prompts are Markdown and they are the behaviour.**
- **A new module goes where upstream keeps its counterpart**, snake_cased; TDD-only code goes
  in `app/tdd/`. Its docstring names the upstream file and symbols it ports.
- **Package `__init__.py` files re-export nothing.** Import from the defining module. Two
  import cycles are broken by commented function-level imports (TDD stop hook, TDD state
  source).
- `experimental_executions/` and `mutation_tests/` are **frozen research artifacts** backing
  the paper's results. Do not regenerate or reformat them.
