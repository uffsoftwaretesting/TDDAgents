# CLAUDE.md phase record and baselines (archived)

This is the full `CLAUDE.md` as of commit `d6c8505` (2026-10-30), kept verbatim when
`CLAUDE.md` was condensed under Claude Code's 40k-character limit (2026-10-31). It holds the
per-phase record ("What Phase N changed"), every recorded divergence, the mutation baselines
and the survivor triage. `CLAUDE.md` is the current guide; this file is the history behind it.

---

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Start-of-session ritual (mandatory)

TDDAgents was rebuilt on claude-code's loop architecture (Phases 0–10 of `docs/refactoring_transition_plan.md`, completed 2026-10-26). Before doing **any** work in this repository — reading, planning, answering a question, or editing — always:

1. **Read this entire `CLAUDE.md`.** It is the map of the architecture and of the gotchas a change has to preserve or deliberately break.
2. **Read the relevant source code before changing it.** Never edit or reason about `app/` from memory or from this document alone; open the actual files. This file describes intent, the source is the truth, and the two drift.
3. **Start from `reference/claude-code-map.md`** whenever the task touches Claude Code behavior, agent/tool design, or a pattern worth mirroring. The map is short, covers every subsystem, and routes you to the analysis and the code. Follow both routes — see [Claude Code reference](#claude-code-reference). Never answer a question about Claude Code internals from memory when the source is sitting in `reference/claude-code/`.
4. **Run the [Quality gate](#quality-gate-mandatory) before any code you create or refactor is considered done.** It is not optional and not a final polish step: the tests are written *with* the change, not after it.

Only after these steps should you propose a plan or start editing.

**Decisions go to the user.** Every architecture decision, open question, or ambiguity is put
to the user with the `AskUserQuestion` tool before acting on it — never resolved silently by
picking a default. A `UserPromptSubmit` hook in `.claude/settings.json` restates this on every
prompt.

## The architecture — claude-code's loop, laid out like its source

TDDAgents is built on claude-code's *loop engineering architecture*: one `while True` loop as
the system centre, with permissions, hooks, context assembly, compaction, recovery and tool
orchestration as systems arranged around it. The design is `docs/transition_elaboration_plan.md`
and the phase record is `docs/refactoring_transition_plan.md` — **read the plan before designing
anything new in `app/`.**

Since Phase 10 (2026-10-26) **`app/` mirrors `reference/claude-code/src/`**, file for file, in
snake_case: `src/tools/BashTool/bashPermissions.ts` is `app/tools/bash_tool/bash_permissions.py`,
`src/services/api/withRetry.ts` is `app/services/api/with_retry.py`. Where a `.ts` file and a
directory share a name, the file's code is `<name>/<name>.py` (`query.ts` →
`app/query/query.py`, beside `query/deps.py`). To find our port of an upstream file, translate
its path; to find what a module ports, read its docstring's `Ported from` lines. Everything
TDDAgents adds lives in **`app/tdd/`**, and the session driver in `app/session/`.

| Path (`app/`) | Upstream | Ships |
|---|---|---|
| `main.py` | `entrypoints/cli.tsx`, `main.tsx` | Argument parsing, interactive / headless (`-p`) session, report and artifacts |
| `cli/print.py` | `cli/print.ts` → `runHeadless` | One development session (`open_session_run` → `DevelopmentRun`): MCP connect, SessionStart / UserPromptSubmit, one `QueryEngine` over the whole plan, `begin_session` / `submit_prompt` per turn, the background-task wait loop, resume (`load_initial_messages` + the plan sidecar), SessionEnd; `run_headless` |
| `screens/repl.py`, `screens/terminal_io.py` | `screens/REPL.tsx` | The interactive session (`Repl`): queued input, Esc abort, permission questions, slash commands; `terminal_io.py` is the `prompt_toolkit` seam |
| `commands.py` | `commands.ts` | The REPL's `local` slash commands: parser, `/plan` operations, help text |
| `hooks/use_can_use_tool.py`, `components/permissions/permission_options.py` | `hooks/useCanUseTool.tsx`, `components/permissions/*/…Options` | The interactive `canUseTool` (`PermissionPrompt` → `PermissionAnswer`, persisting "don't ask again" rules); each tool's choices |
| `query_engine.py` | `QueryEngine.ts` | `QueryEngine.submit`: transcript recorded before the loop, `ResultMessage` with usage, cost and `terminal_reason` |
| `query/query.py` | `query.ts` | `run_loop` (async generator, yields `Terminated` last) and `drain`; every continue/terminal site; the pre-model pipeline (boundary → budget → microcompact → autocompact → blocking precheck) |
| `query/state.py`, `query/transitions.py` | `query.ts` `State`, `query/transitions.ts` | `LoopState` (frozen, **no defaults**), `CompactionTracking`, `CompactionResult`; the `Continue` / `Terminal` vocabulary |
| `query/config.py`, `query/deps.py` | `query/config.ts`, `query/deps.ts` | `RunConfig` + `Gates` (snapshotted at entry); `LoopDeps` (the DI seam) and the production deps |
| `query/stop_hooks.py`, `query/withhold.py` | `query/stopHooks.ts`, `query.ts` | `StopHookResult`, `build_stop_hooks_runner` (**TDD hook first**); withhold-then-decide recovery predicates |
| `tool.py`, `tools/tools.py` | `Tool.ts`, `tools.ts` / `utils/toolPool.ts` | `Tool`, `build_tool` (fail-closed defaults), `ToolResult`, `ToolContext` (`ToolUseContext`); `assemble_tool_pool` with phase-derived deny rules |
| `tools/<name>_tool/` | `tools/<Name>Tool/` | One directory per tool, its `prompt.md` beside it: `bash_tool` (+ `bash_permissions`, `bash_security`, `path_validation`, `read_only_validation`, `sed_validation`), `file_read_tool`, `file_write_tool`, `file_edit_tool` (+ `utils`, `constants`), `glob_tool`, `grep_tool`, `todo_write_tool`, `ask_user_question_tool`, `agent_tool` (`agent_tool`, `run_agent`, `load_agents_dir`, `agent_tool_utils`, `resume_agent`, `agent_memory`, `prompt`), `skill_tool`, `send_message_tool`, `task_output_tool`, `task_stop_tool`, `mcp_tool`, `list_mcp_resources_tool`, `read_mcp_resource_tool`. `tools/utils.py` holds what the five file tools share |
| `services/api/` | `services/api/claude.ts`, `withRetry.ts` | `claude.py`: `stream_call_model`, one message per content block, prompt-cache breakpoints; `with_retry.py`: the one ceiling, `DEFAULT_MAX_RETRIES = 10` |
| `services/tools/` | `services/tools/` | `tool_execution.py` (`runToolUse`: validation → `validate_input` → PreToolUse → gate → call → governance → PostToolUse), `tool_orchestration.py`, `streaming_tool_executor.py`, `tool_hooks.py` (+ `resolveHookPermissionDecision`) |
| `services/compact/` | `services/compact/` | `compactConversation`, `autoCompactIfNeeded`, microcompact, the compact prompt + **TDD sections**, post-compact cleanup, the summarizer seam |
| `services/mcp/` | `services/mcp/` | Config scopes, env expansion, `client.py` (connect, transports, results), `McpConnectionManager`, normalization, types |
| `context.py`, `constants/system_prompt_sections.py` | `context.ts`, `constants/prompts.ts`, `constants/systemPromptSections.ts` | `getSystemPrompt` + `tdd-contract.md`, system/user context, per-run memoization; `systemPromptSection`, the dynamic boundary, `invalidate_context_caches` |
| `state/app_state_store.py` | `state/AppStateStore.ts` | `AppState` (ledger, todos, permission context, denial tracking, last test run, tasks), `AppStateStore`, `discard_app_state_update` |
| `task.py`, `tasks/`, `utils/task/` | `Task.ts`, `tasks/`, `utils/task/` | Task ids, `LocalAgentTask` + `<task-notification>`, `stopTask`, the task framework and disk output |
| `coordinator/coordinator_mode.py` | `coordinator/coordinatorMode.ts` | Coordinator mode (`TDDAGENTS_COORDINATOR_MODE`), its tool filter and user context |
| `skills/load_skills_dir.py` | `skills/loadSkillsDir.ts` | Skill definitions and the loader (bundled `app/prompts/skills` < policy < user < project) |
| `cost_tracker.py`, `utils/model_cost.py` | `cost-tracker.ts`, `utils/modelCost.ts` | `CostTracker` callback per run; Claude + hand-maintained OpenAI price tables |
| `utils/permissions/` | `utils/permissions/` | `permissions.py` (`hasPermissionsToUseTool` inner/outer, rules, auto mode, PermissionRequest), `filesystem.py`, `permission_update.py`, `permission_rule_parser.py`, `dangerous_patterns.py`, `denial_tracking.py`, `classifier_decision.py`, `yolo_classifier.py`, `shell_rule_matching.py` |
| `utils/settings/`, `types/permissions.py` | `utils/settings/`, `types/permissions.ts` | The five settings sources and `build_tool_permission_context`; `PermissionMode`, rules, `ToolPermissionContext` |
| `utils/hooks/`, `schemas/hooks.py`, `types/hooks.py` | `utils/hooks.ts`, `utils/hooks/`, `schemas/hooks.ts`, `types/hooks.ts` | `hooks.py` (`executeHooks`, matching, `execCommandHook`, every per-event executor), the config snapshot, async registry, prompt/agent/HTTP hooks, SSRF guard, `hook_dispatcher.py` (`build_hook_dispatcher`); the hook schema; `processHookJSONOutput` |
| `utils/attachments.py` | `utils/attachments.ts` | `getAttachments` / `getAttachmentMessages` and every ported source (the TDD source is in `app/tdd/`) |
| `utils/` (rest) | `utils/*.ts` | `messages.py`, `message_queue_manager.py`, `claudemd.py` (memory files), `session_storage.py` + `session_storage_portable.py` + `conversation_recovery.py` (transcripts, resume), `session_start.py`, `tokens.py`, `tool_result_storage.py`, `tool_errors.py`, `ripgrep.py`, `file.py`, `glob.py`, `file_state_cache.py`, `abort_controller.py`, `argument_substitution.py`, `frontmatter_parser.py`, `mcp_validation.py`, `shell_command.py`, `bash/commands.py`, `shell/read_only_command_validation.py`, `model/model.py` (model aliases) |
| **`tdd/`** | — (TDDAgents) | `ledger.py` (`PhaseLedger`, the single source of truth), `run_tests_tool/` (`RunTests`, the only observation writer), `phase_permissions.py` + `bash_phase_permissions.py` (barrier 2), `stop_hook.py` (barrier 3), `phase_on_spawn.py`, `tdd_state_attachment.py`, `events.py` + `event_log.py` (`events.jsonl`); `plan.py` (`TddPlan`, the `tdd-plan.json` sidecar, `update_tdd_plan`), `plan_advance.py` (the ledger-driven advance), `tdd_plan_tool/` (`UpdatePlan`); `coverage_gate.py` (REFACTOR's coverage threshold and reading), `collection_gate.py` (uncollected test files), `suite_fingerprint.py` (RED's evidence: the test files' fingerprint and the item baseline) |
| `session/` | — (TDDAgents) | `driver.py`: thread ids, `SessionOptions`, `develop_interactive` (the REPL) / `develop_headless` (`-p`); `prompts.py`: the development prompts (approved, resume, continue) |
| `utils/plans.py`, `utils/words.py`, `utils/plan_mode_v2.py`, `utils/permissions/permission_setup.py`, `tools/{enter,exit}_plan_mode_tool/` | `utils/plans.ts`, `utils/words.ts`, `utils/planModeV2.ts`, `utils/permissions/permissionSetup.ts`, `tools/{Enter,ExitPlanModeV2}Tool/` | Plan mode, the Analyst: the session plan file and its slug, the workflow switches, the mode transitions (`prePlanMode`), `EnterPlanMode` / `ExitPlanMode` |
| `workspace/`, `sandbox/` | — (TDDAgents) | The `Workspace` protocol, `LocalWorkspace` (`workspaces_root()`, `for_run` → `<root>/<thread>/project`), `project.py` (`prepare_project`: git init, ignore file, seeded settings), `pyenv.py` (the project's `.venv`), `E2BWorkspace` over the E2B adapter |
| `prompts/` | `constants/prompts.ts`, bundled agents and skills | System sections, the TDD agents (`AGENT.md` + `references/`), bundled skills, compact / permission / session prompts, specs. Tool prompts live beside their tools |

Three things about the loop are load-bearing and easy to undo by accident:

- **`LoopState` has no defaults, on purpose.** Every continue site must name every field, so
  the reset-or-preserve decision is visible in the diff. A default would let a future site
  stay silent about a field, which is the silence that produced upstream's
  `compact → error → stop hook → compact` spiral.
- **Ceilings follow claude-code, nothing else** *(revised 2026-10-11 at the user's explicit
  direction, superseding the earlier blanket "no ceilings anywhere" rule, which was written
  against the pre-loop TDDAgents)*. Where upstream bounds something, this codebase ports the
  bound **with upstream's own constant and a citation**; where upstream leaves something
  unbounded, it stays unbounded here. So: **no turn limit** and no `max_turns` (upstream's is
  `number | undefined` and falsy means unbounded), but the **API retry layer keeps
  `withRetry`'s attempt ceiling**, because a provider that never recovers would otherwise hang
  a run with no exit. `has_attempted_reactive_compact` remains a latch,
  not a budget, and must not be mistaken for removable. `stop_hook_active` is still set and
  passed to hooks as upstream does, but **the TDD stop hook no longer latches on it** (Phase 12,
  user decision 2026-10-28): it sends every early stop back, as upstream's Stop hooks may. A ceiling that is in neither category
  is still forbidden — invent nothing.
- **`run_loop` yields `Terminated` as its last event** rather than returning it. That is the
  single forced divergence from the reference: PEP 525 forbids a value in an async
  generator's `return`, so JS's `yield*`-returns-`Terminal` has no Python equivalent. `drain`
  is the stand-in.

### The phase record

The sections below record what each phase changed and how its gate measured. **They cite the
paths of their time** — `app/loop/...` before Phase 10 — and are kept as written; the table
above gives each module's current home.

### What Phases 1 and 2 changed (2026-10-11)

Both phases are built, tested and under the gate. The premise, at the user's explicit
direction, is **follow claude-code's design for every problem**; each divergence below was
put to the user and chosen deliberately.

**Phase 1 — the model call and the loop's recovery paths**
- `withRetry` is ported with upstream's constants. Retry wraps **opening the stream only**:
  upstream wraps request creation and lets a mid-stream failure fall to a non-streaming
  request, a seam LangChain's `astream` does not have, so the achievable equivalent is to
  retry while nothing has been emitted.
- The model emits **one message per content block**, so a tool call is dispatchable the
  moment its own block closes rather than when the answer ends.
- The model-fallback branch is ported whole, as an **inner retry inside one iteration**
  (upstream's `while (attemptWithFallback)`), which is why no fallback reason joins the
  continue vocabulary — upstream's has none either. `fallback_model` is unset by default.
- `model_error` repairs history first (invariant B7), then reports.
- The **API-error guard** skips stop hooks, breaking upstream's `error → hook blocks →
  retry → error` spiral. *Divergence:* it terminates `model_error`, not upstream's
  `completed`, because the runner maps `completed` to a passed sub-requirement and a
  provider outage must never be recorded as an observed TDD cycle.
- `refresh_tools` rebuilds the phase-filtered pool between turns, compared **by identity**
  so an unchanged roster does not break the prompt cache.

**Phase 2 — the tool pipeline**
- Pipeline order is upstream's: schema validation first, then `validate_input`, PreToolUse
  hooks, permissions, the call, **governance, then PostToolUse**.
- A PreToolUse hook's `allow` is **re-checked** against the gate, so it cannot defeat a
  phase rule.
- Oversized results are filed under `<config>/projects/<cwd>/<run>/tool-results/` and
  replaced by a head-only 2000-byte preview. `ReadFile` is allowed to read that one
  directory, because ours is workspace-bounded and upstream's `Read` is not.
- **`Bash` is phase-aware.** Its redirection targets run through the phase check, closing
  the `echo code > calculator.py` bypass of barrier 2. As upstream, `tee`/`dd`/`truncate`
  get no special handling — the ledger remains the authority on what was proven.
- **One TDD enforcement point.** `tdd_pre_tool_use_hook` duplicated the gate and
  `tdd_post_tool_use_hook` decided the phase by grepping output for "fail"/"error"; both
  are deleted. `RunTests` and its exit code are the only evidence the phase moves on.
- Prefix tool denial applies to `mcp__` namespaces only — a deny of `Run` used to also
  remove `RunTests`.
- Concurrent results are emitted in **completion order**, matching `runTools`; only context
  modifiers are replayed in call order.

*Divergences, all recorded:* the API-error terminal; reactive compaction kept although
`reactiveCompact.ts` is absent from the shipped build (its call sites in `query.ts` are
not, and TDD runs produce long output); `tool_use_id` sanitized for the result filename,
which is the identity transform for a well-formed id and blocks path traversal.

### What Phase 3 changed (2026-10-14)

Permissions and settings are wired at runtime. The user's direction for every choice was
**"always follow claude-code"**, so each item below is a port, and each divergence is named.

- **The gate is `hasPermissionsToUseToolInner` step for step.** Three local inventions are
  gone: a `checkPermissions` exception is now *passthrough* (upstream logs it), not deny;
  `acceptEdits`/`plan` no longer allow read-only calls in the gate (the tool's own check
  decides); and a tool that "requires interaction" no longer gets an invented ask.
  The **TDD phase check sits above step 1a and fails closed**. It used to be
  `except Exception: pass`.
- **The context is built like upstream's startup** (`build_tool_permission_context`):
  `initialPermissionModeFromCLI` takes `RunConfig.permission_mode` first, then settings
  `permissions.defaultMode`. `initializeToolPermissionContext` loads user, project, local,
  flag (`RunConfig.settings_path`) and policy (`/etc/tddagents/managed-settings.json` +
  `managed-settings.d/*.json`), and honours `allowManagedPermissionRulesOnly`. It lives in
  `AppState.tool_permission_context`, so a worker reading its parent's state gets it too.
  `is_bypass_permissions_mode_available` defaults to `False`.
- **`WriteFile`/`Edit` check permissions** with `checkWritePermissionForTool`. In `default`
  mode every write is an 'ask', and a headless run denies it, which is why the shipped
  `.tddagents/settings.json` now says `"defaultMode": "acceptEdits"`. That is upstream's way
  to let a headless run edit. **A test that drives a write through the pipeline must set
  `acceptEdits` (or an `Edit` allow rule)**, or the write is refused.
- **Path rules are gitignore patterns on `Edit`/`Read`**, matched by `pathspec`. `/x` is
  anchored at the workspace (upstream's original cwd), `//x` at the filesystem root, `~/x`
  at home. A deny on a raw absolute string no longer matches. Write `Edit(//abs/path)`.
- **TDD managed tier = upstream's dangerous-rule stripping, always on.** `Bash(python:*)`
  lets the model write any file through the interpreter, and the phase gate (like the
  auto-mode classifier upstream built `stripDangerousPermissionsForAutoMode` for) cannot see
  where it writes. So `isDangerousBashPermission` strips such allows from editable sources
  for every run. The shipped `Bash(python:*)` and `Bash(python -m pytest:*)` are stripped at
  load; `pytest:*` survives.
- **PermissionRequest hooks** run before the headless deny, and a hook `allow` approves a
  call no rule covers (upstream v2.1.88). Its `updatedPermissions` are persisted to the
  editable settings files and applied to `AppState` (`permissions/updates.py`, ports of
  `applyPermissionUpdate`/`persistPermissionUpdate`/`updateSettingsForSource`). Two TDD
  guards sit on top, at the user's direction: a hook's **`updatedInput` is re-checked** by
  the phase tier and deny rules (the re-check upstream added in v2.1.110), and the applied
  context is **re-stripped** of dangerous allows, so `addRules: Bash(python:*)` cannot
  re-open the interpreter loophole. `setMode` stays unguarded, as v2.1.88.
- **A PreToolUse hook `allow` is re-checked by `check_rule_based_permissions` only**
  (upstream `resolveHookPermissionDecision`): the TDD tier, deny/ask rules, the tool's deny,
  content ask rules and safety checks. With no objection the hook's allow stands, so it
  skips an 'ask' no rule produced, headless included. A **hook `ask`** (upstream's
  `forceDecision`, which skips the gate) first meets the TDD tier and deny rules — the TDD
  barrier, and v2.1.101's "ask no longer overrides deny" — and is then handled as an ask by
  `resolve_ask_without_prompt` (dontAsk deny, PermissionRequest hooks, headless deny).
- **`ReadFile`, `Glob` and `Grep` check `Read(...)` rules** (`checkReadPermissionForTool`):
  reads in the workspace and additional dirs are allowed in any mode, this project's
  `<config>/projects/<cwd>/` tree (persisted tool results) is a readable internal path,
  anything else asks. `Glob`/`Grep` pass Read-deny patterns to ripgrep as exclude globs;
  `ReadFile` refuses a Read-denied path in `validate_input` (errorCode 1).

*Divergences, all recorded:* a dangerous allow in **policy or flag** settings survives, as
upstream's `isPermissionUpdateDestination` filter dictates (kept at the user's direction).
Additional directories carry no source; the local settings file is not added to
`.gitignore`; the editable internal paths, the `.claude/**` session carve-out, the sandbox
write allowlist and `requireCanUseTool` have no equivalent here.

**Phase 3 is measured under `paths_to_mutate` = `permissions/{gate,settings,filesystem,
dangerous,messages,permission_request}`, `runner` and clears the bar**, at 4991 tests:

| | |
|---|---|
| Mutants | 1955 (2 timeout) |
| Killed | 1853 |
| Survived | 100 |
| **Mutation score** | **94.9%** (1853 / 1953) |

The first run scored 91.7%. Triage killed two real gaps and removed two dead-code clusters:
`runner.py` was never in Phase 1's mutation set, so `refresh_tools`' identity cache (rebuild
only on a phase move, then return the same tuple) and the `run_id`/`refresh_tools` wiring
were untested. `run_permission_context` is now pinned input by input. The gate's seed
passthrough message (always rebuilt by step 3) and the write check's unused `tool_name` were
deleted. The 100 survivors are equivalent:
- log wording and arguments (runner 31, `permission_request` 9, gate 10);
- `PermissionResult(behavior=ALLOW)` with the argument dropped, which is the dataclass
  default (5);
- default-argument mutations (8);
- `False`→`None` initializers;
- the `utf-8` codec alias;
- `"ask"`→`None`/`"ASK"` in `_rules_for`, whose `else` branch *is* the ask rules;
- `"read"` mutations where only `== "edit"` is tested;
- `path_in_allowed_working_path` given the same candidates either way;
- `_escaped`'s `-= 1` (parity is unchanged);
- the unreachable `close_idx`/`range` bounds of `permission_rule_value_from_string`;
- the macOS `/private` case folds (later lowercased);
- `load_permission_rules`' managed dir (absent on the test host);
- the runner's pool `phase_ledger` and `messages` mutants (Phase 0's equivalent classes).

**The Phase 3 follow-up is measured under `paths_to_mutate` = `permissions/{rules,gate,
filesystem,updates,permission_request,dangerous}`, `tools/hook_permissions` and clears the
bar**, at 5067 tests:

| | |
|---|---|
| Mutants | 2300 (0 timeout) |
| Killed | 2200 |
| Survived | 100 |
| **Mutation score** | **95.7%** (2200 / 2300) |

Triage killed real gaps in the hook resolver: nothing checked that `tool_use_id` and the
hook's rewritten input reach the full gate and the ask handler, or that a no-opinion verdict
reaches the full gate. It also deleted dead code: `normalizePatternToPath`'s third branch
cannot succeed (a root inside the reference root always takes the second), and a redundant
`join("/", …)`. The 100 survivors are the recorded equivalent classes:
- `"read"`/`"ask"` string mutations where only `== "edit"` or the `else` branch is tested;
- the dataclass-default `behavior=ALLOW`;
- the same candidate list passed either way;
- `apply_permission_update`'s `and`→`or` guards (the parser never yields a `setMode` without
  a mode, or a rule update without a behavior) and its debug-log arguments;
- default arguments, codec aliases and log wording;
- the POSIX-unreachable backslash UNC branch (a `\\server` path is relative and joined onto
  the cwd first, as upstream's `expandPath` does).

### What Phase 4 changed (2026-10-21)

Hooks are rebuilt against the loop in `app/loop/hooks/`, and `app/hooks/` and `app/tools/`
are deleted. The user's direction for every choice was **"follow claude-code's source and
architecture"**.

- **All four hook types** (command, prompt, agent, http) and all 27 events parse. The run's
  hooks are a **frozen snapshot** taken at startup from the same five settings sources as the
  permission rules. Policy `disableAllHooks`, `allowManagedHooksOnly` and
  `strictPluginOnlyCustomization` decide first, and a user-editable `disableAllHooks` can
  only reduce the run to policy hooks.
- **`executeHooks` is upstream's**: matching hooks run in parallel and their results stream
  back as they complete. The permission verdict is aggregated deny > ask > allow, and that
  aggregate is re-yielded after later results exactly as upstream does it. The JSON contract
  (`continue`, legacy `decision`, `hookSpecificOutput`) is `processHookJSONOutput` field for
  field. Exit 2 blocks, any other non-zero exit is a non-blocking error, and timeouts and
  aborts kill the hook's whole process group.
- **Hook output is messages now**, no longer text appended to a tool result. An attachment
  upstream shows the model becomes a `<system-reminder>` meta message, and one it hides is
  logged. `reorder_attachments_for_api` moves them after a batch's tool results, because a
  provider rejects anything between tool calls and their results.
- **Call sites**:
  - PreToolUse, PostToolUse and PostToolUseFailure in the tool pipeline. A raising tool now
    takes the failure path, and a hook's `updatedMCPToolOutput` replaces an MCP result.
  - PermissionRequest at the headless ask.
  - Stop, or SubagentStop when the context has an `agent_id`. Workers now carry
    `agent_id` and `agent_type`.
  - StopFailure at the API-error and prompt-too-long exits, fire-and-forget as upstream's
    `void`: the dispatcher holds the task and the runner drains it before SessionEnd.
  - SessionStart (`startup`), UserPromptSubmit and SessionEnd (`other`) in the runner. A
    blocking or `continue: false` UserPromptSubmit ends the run `hook_stopped` before any
    model call.
  - PreCompact, SessionStart (`compact`) and PostCompact in reactive compaction.
  - Async and `asyncRewake` hooks are delivered after a turn's tools.
- **`if` conditions** use a new per-tool `prepare_permission_matcher`. File tools match on
  `file_path`, Glob and Grep on `pattern`. Bash follows the external build: without
  `TREE_SITTER_BASH`, `parseForSecurity` is `parse-unavailable`, so every content condition
  matches and the hook always runs (fail safe — more hook runs can only add blocks).
- **Barrier 3 is finally wired in production.** `production_stop_hooks` used to be a no-op;
  `build_stop_hooks_runner` runs `tdd_phase_incomplete_hook` first, configured hooks can
  only *add* blocks, and a configured `continue: false` ends the run `stop_hook_prevented`,
  never `completed`. A worker is guarded by the coordinator, not by the TDD hook.
- **Prompt and agent hooks** default to `TDDAGENTS_SMALL_FAST_MODEL` (upstream's
  `ANTHROPIC_SMALL_FAST_MODEL`), then the run's model *(since 2026-10-29: upstream's default
  Haiku on a Claude run)*.
- **The agent-hook verifier** runs in `dontAsk`. Its pool is upstream's: the parent's
  phase-filtered tools minus the disallowed set, plus `StructuredOutput`, with upstream's
  50-turn ceiling. It reads the parent's ledger and rules, so the phase tier applies to every
  call it makes.

*Divergences, all recorded:*
- **The agent-hook verifier's app-state writes are discarded** (TDD: only the workers'
  `RunTests` moves the ledger).
- `null`-valued fields in a hook's *output* JSON are ignored.
- `file_path` is matched as written, with no `backfillObservableInput`.
- The async registry is per run.

*Resolved toward upstream (2026-10-22, each test-first):*
- an invalid `hooks` block now discards its whole settings file (`parseSettingsFileUncached`:
  `settings: null`), its permission rules and `defaultMode` included. The TDD tier lives in
  code and is unaffected;
- StopFailure is fire-and-forget;
- the Bash `if` matcher is the external build's;
- any exception outside the tool's own call (validation, hooks, the gate) becomes the call's
  `<tool_use_error>Error calling tool (X): …</tool_use_error>` — `runToolUse`'s outer `catch`.
- Not ported:
  - plugins and their environment variables;
  - `CLAUDE_ENV_FILE`, `CLAUDE_CODE_SHELL_PREFIX` and the prompt-request protocol;
  - `watchPaths` and FileChanged;
  - the sandbox network proxy for HTTP hooks;
  - progress messages and the stop-hook summary (UI).

**Mutation, Phase 4 modules** (`hooks/`, `tools/execution.py`,
`permissions/permission_request.py`, `runner.py`). The first run scored **89.5%**
(4901 / 5474; 5489 mutants, 15 timeout, 541 survived, 32 with no tests). Triage killed 304
more, verified one by one inside `mutants/`, and deleted 4 dead-code mutants, for a
**projected 95.2%** (≈5205 / 5470). That projection is confirmed by the pre-push mutmut run.

The remaining ≈265 survivors are the recorded equivalent classes:
- log wording and arguments (≈139);
- codec aliases;
- default arguments;
- `getattr` defaults on attributes that always exist;
- `False`→`None`;
- timing granularity under the 0.05 s abort poll;
- library equivalences (urllib's lowercased proxy keys, POST implied by a request body);
- dataclass defaults;
- a PreToolUse deny `blocking_error` that `hookSpecificOutput` always overwrites;
- the Phase 0 pool `phase_ledger` class.

Triage also ported a missed upstream behaviour (MCP output replacement) and removed dead
code in `ssrf_guard.py` and `tool_hooks.py`.

**Verifying a survivor inside `mutants/`:** the baseline is `MUTANT_UNDER_TEST=""`. Left
unset, mutmut's trampoline raises `KeyError` in every test, which makes every mutant look
killed.

### What Phase 4b changed (2026-10-22)

The auto-mode ("YOLO") classifier, added to the plan by the user and ported whole.

- **`auto` is user-addressable** (`PERMISSION_MODES` = external modes + `auto`, as the
  `TRANSCRIPT_CLASSIFIER` build has it) but **opt-in**: the shipped default stays
  `acceptEdits`.
- **The gate's auto branch is upstream's order** (`permissions/auto_mode.py`). It runs on an
  'ask' after `dontAsk` and before the headless deny:
  1. a safety check that is not `classifierApprovable` stays an ask (headless: deny);
  2. a tool needing a person keeps its ask;
  3. the **acceptEdits fast path**;
  4. the **allowlist**;
  5. otherwise the classifier. An unavailable classifier **fails closed**. A block records
     a denial, and at 3 consecutive / 20 total denials a headless run aborts.

  An allow by any route resets the streak. Workers keep a local denial box.
- **TDD:** the phase tier runs in the inner gate, before the auto branch. A phase-forbidden
  write is already denied, so neither the classifier nor the fast path can open it, and a
  test pins this.
- `classifierApprovable` now rides on the filesystem safety check: sensitive files `true`,
  suspicious Windows paths `false`.
- PermissionDenied hooks fire after an auto-mode denial; a hook's `retry` reaches the model
  as a meta message.
- **The classifier** (`permissions/yolo_classifier.py`) follows upstream:
  - the external defaults: tool-call path, text transcript, main-loop model;
  - the two-stage XML path is ported but switchable only through `ClassifierOptions`,
    because upstream gates it on GrowthBook, which this build lacks;
  - the transcript holds user text and assistant tool calls only;
  - `TDDAGENTS.md` comes from the context-assembly cache;
  - a tool with no classifier projection (e.g. `RunTests`) is never classified.

*Divergences, all recorded:*
- **The system prompt and default rules are TDDAgents-authored**
  (`app/prompts/permissions/`), reconstructed from the code's markers because upstream's
  `.txt` files were compiled out. The rules ship as reconstructed, at the user's direction.
- *(Resolved 2026-10-22.)* A headless abort raises `AutoModeAbortError`, upstream's
  `AbortError`. `run_tool_use`'s outer handler reports it as the call's error result and the
  turn goes on, exactly as upstream; the stop hook still guards termination.
- Per-tool classifier projections live in a table keyed by our tool names.
- The production caller does not forward temperature or max_tokens.
- Not ported: the PowerShell guard, `plan` mode with auto active, debug dumps, and the Bash
  classifier (all off in the external build).

**Pre-push gate, Phases 4 + 4b (2026-10-22).** Measured under `paths_to_mutate` =
`hooks/`, `tools/execution.py`, `permissions/{permission_request,auto_mode,denial_tracking,
classifier_decision,yolo_classifier}.py`, `runner.py`, at 5132 tests:

| | |
|---|---|
| Mutants | 7436 (21 timeout) |
| Killed (gate run) | 6807 — **91.8%** (6807 / 7415) |
| Killed after triage | 7048 — **95.1%** (7048 / 7414; one dead-code mutant deleted) |
| flake8 / ruff / mypy | 0 findings, 0 findings, 0 errors (strict) |

Triage killed 241 more mutants, each verified inside `mutants/` against the
`MUTANT_UNDER_TEST=""` baseline. 108 of them sat in `build_production_classifier_caller`,
which is now tested offline through a fake `get_chat_model` and is no longer exempt.

The remaining survivors are the recorded equivalent classes:
- log wording and arguments;
- `getattr` defaults on attributes that always exist;
- default arguments;
- codec aliases;
- `False`→`None`;
- dataclass defaults;
- case flips under `re.IGNORECASE`;
- `_with_accept_edits`' `permission_context` variants, which fall back to the same
  overridden app state.

**Ruff** joins the lint step with its 0.17 defaults. `[tool.ruff.lint] external` lists
flake8's codes, so ruff does not strip flake8's `# noqa` markers. Deliberate catch-alls
carry `# noqa: BLE001` with their reason.

### What Phase 5 changed (2026-10-22)

Context assembly, attachments and compaction, ported test-first. The user's direction:
"follow claude-code's source and architecture".

- **The pre-model pipeline is upstream's, on stored history** (`engine.py`, `query.ts`):
  1. messages after the last compact boundary;
  2. the tool-result budget (off: upstream gates it on GrowthBook `tengu_hawthorn_steeple`,
     which the external build leaves off);
  3. microcompact (time-based, disabled by its external default config);
  4. autocompact (`LoopDeps.autocompact`);
  5. the blocking-limit precheck, which defers to reactive compaction exactly as
     `query.ts` does.

  What these return is what the iteration sends *and* what the next one starts from.
  `model.py` no longer compacts or attaches per request; its prefix is byte-stable.
- **Attachments are persisted** (`app/loop/attachments/`): user-input attachments at the
  runner's first turn (`processUserInput`), and turn attachments after the tools
  (`getAttachmentMessages`). This fixes the TDD state being re-sent on every request: it is
  announced once per ledger change. Todos go through `todo_reminders` (upstream cadence).
  Every source whose subsystem exists here is ported; each skipped source is recorded in
  `attachments/__init__.py`.
- **Compaction follows upstream** (`app/loop/compact/`):
  - the whole history is summarized, with no kept tail;
  - the 5 most recent files are re-read (50k / 5k token budgets);
  - SessionStart(`compact`), PreCompact and PostCompact hooks run;
  - the boundary record (never sent to the model) is written;
  - autocompact fires at 167k for an unknown model;
  - `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3`, so `CompactionTracking.consecutive_failures`
    is back;
  - the reactive path (`production_compact`) uses the same `compact_conversation`.
- **TDD state survives compaction from authoritative state** (the one forced divergence).
  The summary prompt gains TDD sections (ledger, latest failing evidence, test paths,
  sub-requirement). After the boundary, `factory.post_compact_attachments` re-attaches the
  ledger and the last `RunTests` record (`AppState.last_test_run`, written by `RunTests`
  with the ledger) — never the summary. A test drives a compaction whose summary *lies*
  ("all tests pass") and pins that the run still cannot terminate `completed`.
- **Prompt-cache breakpoints** for Anthropic: one `org`-scoped system block plus the
  last-message marker (langchain-anthropic's `cache_control` kwarg). The global scope is a
  first-party beta this transport does not send. OpenAI caches prefixes unaided.
- **Memory:**
  - `extract_session_memory` / `update_auto_memory` (keyword-grep inventions) are deleted.
  - **Run memory is per sub-requirement** (Part I5): the runner owns one store rooted
    outside the workspace and discards it at the end.
  - A run-scoped dir needs a run id; there is no shared `default_run`.
  - The Agent tool no longer purges per spawn.
- `nested_memory` triggers: `ReadFile` records what it reads (`nestedMemoryAttachmentTriggers`).

*Divergences, all recorded:*
- `changed_files` compares content, not mtime (the workspace has no mtime).
- Restore order is `read_file_state` insertion order (no read timestamps).
- The summarizer sends no tools.
- Sources run sequentially.
- Env names are `TDDAGENTS_*`.
- Not ported:
  - session-memory and partial compaction;
  - cached microcompact;
  - the forked prompt-cache-sharing path *(ported 2026-10-29; see "Compaction shares the main
    thread's request")*;
  - plan, skill and async-agent post-compact attachments (no subsystems);
  - the global cache scope;
  - the attachment sources whose subsystems this build lacks.

**Pre-push gate, Phase 5 (2026-10-22).** Measured under `paths_to_mutate` =
`attachments/`, `compact/`, `context/{tokens,cache}.py`, `tools/result_storage.py`,
`agents/memory.py`, `engine.py`, `factory.py`, at 5448 tests:

| | |
|---|---|
| Mutants | 4934 (9 timeout) |
| Killed (gate run) | 3978 — **80.8%** |
| Killed after triage | 4693 — **95.4%** (4693 / 4917; 8 dead-code mutants deleted) |
| flake8 / ruff / mypy | 0 / 0 / 0 (strict, 284 files) |

Triage killed 715 mutants, each verified inside `mutants/` against the
`MUTANT_UNDER_TEST=""` baseline.
- **One real bug**, in `attachments/changed_files.py`: the snippet cutoff searched
  `rfind("\n", 0, 8192)`, excluding index 8192, where upstream's `lastIndexOf(..., 8192)`
  includes it.
- **Dead code deleted:** `attachments_in`, and `load_memory_prompt`'s unreachable fallback.
- **Remaining survivors** are the recorded equivalent classes: log wording, defaults,
  codec aliases, `False`→`None`, `getattr` defaults on attributes that always exist,
  memory-type labels only the `AutoMem` check reads, model-table branches equal to the
  default, and the run loop's mutually covering prompt-too-long / max-output-tokens
  detection.

**Gotcha — mutmut and `/tmp`:** under mutmut each forked test child rebuilds the session
venv in a fresh pytest temp dir, which filled the 9.8G `/tmp` tmpfs mid-run ("No space left
on device", every remaining mutant "not checked"). Run mutmut with
`TMPDIR=<a directory on disk>` and clear it afterwards.

### What Phase 6 changed (2026-10-22)

Session wrapper, transcripts and the evidence layer, ported test-first. The user's direction:
"follow claude-code's source and architecture, while enforcing the TDD cycle".

- **One `QueryEngine` per sub-requirement** (`query_engine.py`, `QueryEngine.ts`). The runner
  no longer drains `run_loop` itself: the engine records the starting messages **before**
  the first model call, records every whole message as it arrives, and yields a
  `ResultMessage` last. The result is yielded to the caller and logged, never persisted, as
  upstream's SDK result. Success follows `isResultSuccessful`; the TDD outcome is still the
  terminal plus the ledger (`PlanItemResult` is unchanged).
- **Transcript at upstream's location**: `<config home>/projects/<sanitized cwd>/<run id>.jsonl`
  (`TDDAGENTS_CONFIG_DIR`, default `~/.tddagents`), workers at
  `<run id>/subagents/agent-<id>.jsonl`. The cwd is the workspace root (the project root when
  the workspace has none). One whole LangChain message per line under `message`, with
  upstream's chain fields and stamp; the uuid lives in `additional_kwargs["uuid"]`.
  Append-only. **Attachments are not logged** (external build's `isLoggableMessage`), so the
  TDD-state reminders are not in the transcript — the events file carries the ledger.
- **`emit_event` is real** (`events.py`): each transition and the terminal, with the ledger,
  the last `RunTests` exit code/path and the plan index, appended to
  `<run id>/events.jsonl` (inside the session directory so it is never taken for a
  transcript). Sidechains do not get this sink.
- **Cost**: `CostTracker` is a LangChain callback on every model call (framed messages lose
  usage, so they are not read). LangChain's `input_tokens` includes cache reads/creation;
  the tracker subtracts them. Unknown model: cost 0 and `has_unknown_model_cost` (deliberate
  divergence from upstream's default-model fallback). The OpenAI table: only o4-mini was
  confirmed on OpenAI's own page; the other 9 entries are kept at the user's direction and
  must be re-verified before a figure is relied on.
- **Resume is a library** (`storage/loader.py`, `storage/recovery.py`), wired by the entry
  point in Phase 9 together with SessionStart(`resume`). **The ledger is never restored from
  a transcript**: a resumed run starts from whatever ledger its caller seeds — fail-safe,
  since RED must be re-observed.
- Compaction summaries name the transcript path (`ToolContext.transcript_path`), as
  `compact.ts` does; workers share it.
- Deleted: `app/loop/transcript.py` (`TranscriptLogger`, `state_checkpoint` records — an
  invention).
- Tests: an autouse fixture points `TDDAGENTS_CONFIG_DIR` at a temp dir, so no test writes
  to the real home.

*Divergences, all recorded:* writes are synchronous (upstream batches); sidechain writes are
not deduplicated (as upstream); `version` is this checkout's short git sha; no
`permission_denials`, `structured_output`, `fast_mode_state` in the result; resume skips the
>5 MB pre-boundary scan, legacy progress bridging, preserved-segment relinks, snip removals,
metadata entries and skill-state restore; brief-mode terminal tool results have no TDDAgents
tool (hook point kept).

**Pre-push gate, Phase 6 (2026-10-22).** Measured under `paths_to_mutate` =
`storage/`, `cost/`, `events.py`, `query_engine.py`, at 5664 tests:

| | |
|---|---|
| Mutants | 1690 first run (7 timeout); 1569 after triage removed dead code (3 timeout) |
| Killed (gate run) | 1496 — **88.9%** |
| Killed after triage (confirmed by a second full run) | 1532 — **97.8%** (1532 / 1566) |
| flake8 / ruff / mypy | 0 / 0 / 0 (strict, 302 files) |

Triage killed ~110 mutants and deleted ~46 by simplifying code, each kill verified inside
`mutants/` against the `MUTANT_UNDER_TEST=""` baseline.
- **Two real bugs in `storage/loader.py`:**
  - a non-message entry whose `parentUuid` pointed at a missing entry crashed the leaf walk;
  - the main-leaf search started from `0.0` instead of upstream's `-Infinity`
    (`findLatestMessage`), so an entry stamped before the epoch could never be the tip.
- **Dead code removed:**
  - `_last_content_type`'s unreachable `"none"` branch;
  - `recoverOrphanedParallelToolResults`' redundant `processed` set;
  - `_assistant_blocks`' dedup;
  - the `getattr` defaults on LangChain fields that always exist;
  - `_insert_message_chain`'s duplicate uuid assignment and its `last` variable.
- **Remaining 34 are the recorded equivalent classes:**
  - codec aliases;
  - log wording;
  - `False`→`None`;
  - default arguments;
  - `getattr`/env defaults that miss the same way;
  - `_git`'s `check` (the raised error is caught to the same `None`);
  - `rsplit` maxsplit;
  - `>= 0` → `> 0` on an index whose message the loop skips anyway;
  - the arguments to the brief-mode hook point `_is_terminal_tool_result`, which always
    returns `False`.

### What Phase 7 changed (2026-10-22)

Agents and the TDD coordinator, ported test-first. The user's direction: "follow claude-code's
source and architecture, while enforcing the TDD cycle".

- **Workers really run.** The `default_subagent_runner` stub (which fabricated a "completed
  successfully" report) and `create_subagent` are deleted. `run_agent` ports `runAgent`:
  - a second `run_loop` with the agent's system prompt (+ `enhanceSystemPromptWithEnvDetails`)
    and its own identity;
  - a pool from `resolveAgentTools`, filtered by the live phase and refreshed between turns;
  - a fresh `readFileState`, SubagentStart context and a sidechain transcript.
- **The ledger is shared, as upstream shares app state.** A sync worker writes through the
  parent's `set_app_state` (`shareSetAppState: !isAsync`), so a tester's `RunTests` confirms
  RED on the coordinator's ledger. Workers' transitions never reach the events file.
- **Phase-on-spawn** (`agents/tool.py::phase_on_spawn`) moves the shared ledger to the agent's
  `phase` through `PhaseLedger.transition_to`:
  - GREEN now needs `red_confirmed`;
  - REFACTOR needs a completed cycle;
  - **RED restarts the cycle** (user decision: a tester re-spawned to fix a wrong test).

  A refused spawn raises before any worker runs. The Agent tool is now the ledger's second
  writer: it can move the phase or erase evidence, never create an observation, and
  `test_run_tests_is_the_only_ledger_writer` pins exactly that.
- **Coordinator mode is upstream's switch**, off by default (`TDDAGENTS_COORDINATOR_MODE`,
  frozen as `Gates.coordinator_mode`). In it the main thread's pool is
  `applyCoordinatorToolFilter`'d to `Agent`, its system prompt is the coordinator's, and the
  user context names the worker tools. The `tdd_phase_incomplete` stop hook guards it. Outside
  it the main thread also holds the Agent tool, as upstream's does.
- **Frontmatter is upstream's.**
  - Only upstream permission modes parse (`workspace_write`/`read_only` are gone).
  - Explore, Plan and verification are read-only through `disallowedTools`.
  - Explore and Plan are named as upstream and omit the memory files and `gitStatus`.
  - `description` is required.
  - Agent lookup no longer touches the prompt registry.
- **Model aliases:** the `model` input keeps upstream's Claude aliases and adds an OpenAI table
  (default `o4-mini`). The provider follows the model id (`provider_for_model`).
- The Agent tool stays **concurrency-safe**, as upstream; the gate re-checks the live ledger on
  every write. Fork is off (`is_fork_subagent_enabled`, headless).

*Divergences, all recorded:*
- `subagent_type` is required (no general-purpose agent; fork off).
- Coordinator workers run **synchronously**. Upstream forces them async, but an async worker's
  `RunTests` could not move the ledger. Background work is Phase 8.
- The result trailer keeps upstream's `SendMessage` hint verbatim (user decision), though that
  tool arrives in Phase 8.
- `ASYNC_AGENT_ALLOWED_TOOLS` includes `RunTests`.
- The coordinator prompt is TDDAgents-authored (upstream's `workerAgent.ts` is absent from the
  snapshot).
- Not ported: per-agent MCP servers, skill preloading and frontmatter hooks (no session-hook
  store), effort, worktree/remote isolation, `classifyHandoffIfNeeded`, agent metadata files,
  progress UI and auto-backgrounding.
- `app/loop/prompts/loader.py` still holds an older duplicate agent parser, left for Phase 10.

**Pre-push gate, Phase 7 (2026-10-22).** Measured under `paths_to_mutate` = `agents/{run_agent,
tool,resolution,loader,prompt,fork}.py`, `coordinator.py`, `model_aliases.py`, `ledger.py`,
`context/assembly.py`, `model.py`, `runner.py`, at 5770 tests:

| | |
|---|---|
| Mutants | 2387 first run (113 timeout); 2330 after triage removed dead code (33 timeout) |
| Killed (gate run) | 1954 — **85.9%** |
| Killed after triage (confirmed by a second full run, plus 5 verified in `mutants/`) | 2177 — **94.8%** (2177 / 2297) |
| flake8 / ruff / mypy | 0 / 0 / 0 (strict, 310 files) |

- **One real bug:** the Agent tool prompt had two newlines at two seams where upstream's
  `getPrompt` has three. The prompt is now pinned verbatim against `prompt.ts`.
- **Dead code removed:**
  - the `<!--` frontmatter branch (upstream's `FRONTMATTER_REGEX` takes `---` only);
  - a redundant `"*"` normalization;
  - the Agent tool's custom `check_permissions` (identical to the default allow);
  - `finalize_agent_tool`'s duplicate first scan, `run_agent`'s unread pool-cache seed and
    redundant `Terminated` check, and the runner's unreachable `or ""`.
- **Fixed: counting survivors by line.** `mutmut results` prints every *non-killed* mutant,
  timeouts included, each with its status. Grouping those lines without filtering on the
  status turned 107 runner timeouts into "survivors" (126 shown, 19 real). The score now comes
  from `scripts/mutmut_score.py`, which reads `mutmut results --all true`, counts by status,
  computes `killed / (total − timeout)`, refuses a run with `not checked` mutants, and lists the
  not-killed mutants by function.
- **Fixed: source-reading tests under mutmut.** A test that reads a module's source sees every
  mutant copy inside `mutants/`. The ledger-writer pin broke stats collection twice that way,
  so two runs measured nothing. Source scans now go through `tests/source_scan.py`
  (`original_nodes`), which keeps unmutated code and the `__mutmut_orig` copies only.
- **Fixed: `run_agent` under mutmut.** mutmut wraps a top-level async generator as
  `async for i in trampoline(...): yield i`, which never forwards `aclose()`, so the worker's
  cleanup could not be verified there. `run_agent` is now a plain function returning its
  (nested) async generator: the trampoline hands back the real generator, and
  `test_closing_the_worker_closes_its_loop` runs under mutmut too. **Any new top-level async
  generator whose cleanup matters needs the same shape.**
- **Follow-up gate (2026-10-22)** over `agents/run_agent.py` and `agents/tool.py` after these
  fixes, scored by `scripts/mutmut_score.py`: 497 mutants, 0 timeouts, 480 killed —
  **96.6%**. The former `aclose` group is now killed under mutmut. The 17 survivors are the
  recorded equivalent classes: log wording (9), default arguments (2), engine-overwritten
  messages (2), `continue`→`break` on the always-last `Terminated`, the `"settings"`
  deny-rule fallback (2), and `getattr(events, "aclose")` losing its default on a generator
  that always has it.
- **Remaining survivors are the recorded equivalent classes:**
  - log wording and arguments (runner 32, `run_agent` 9, loader 17);
  - default arguments;
  - codec aliases;
  - the Phase 0 pool `phase_ledger`/`messages` classes;
  - `rmtree(ignore_errors=…)` on a directory that always exists;
  - `user_input` (no ported source reads it);
  - the unreachable CUSTOM-disallowed check (equal to ALL, as upstream);
  - the `"settings"` deny-rule fallback, which upstream keeps too;
  - messages the engine overwrites before every model call.

### What Phase 8 changed (2026-10-25)

Background work, skills and MCP, ported test-first. The user's direction: "follow claude-code's
source and architecture, while enforcing the TDD cycle"; MCP: "port the main MCP
functionalities available for Claude Code".

- **Background agents, only without a phase.** `run_in_background` (in the Agent schema unless
  `TDDAGENTS_DISABLE_BACKGROUND_TASKS`), a definition's `background: true` and coordinator-forced
  async apply to agents **without** a TDD `phase`; a phase agent asked to go async is refused
  before any worker runs. An async worker's app-state writes are discarded upstream
  (`shareSetAppState: !isAsync`), so a background tester could never confirm RED. A background
  worker gets its own cancel token, writes only its task state through the root setter
  (`setAppStateForTasks`), and ends in one upstream `<task-notification>` at `later` priority.
- **The runner waits as `print.ts` does.** After a turn, while a background task runs or a
  main-thread command is queued, it feeds each notification back as a new turn of the same
  conversation (`QueryEngine.mutable_messages`); the outcome is the last turn's terminal, and
  the stop hook guards every turn. No ceiling, as upstream.
- **One command queue per run** (the hook registry's), shared with every worker: `asyncRewake`
  notes and task notifications wait at `later` for the runner; `queued_commands` drains only
  `next` and above mid-turn. Workers receive `SendMessage` text through the
  `agent_pending_messages` attachment.
- **Task tools:** `TaskOutput`, `TaskStop` on the main thread (workers are denied them), and
  `SendMessage` (local agents only) behind `TDDAGENTS_EXPERIMENTAL_AGENT_TEAMS`, upstream's
  `isAgentSwarmsEnabled` opt-in. A stopped agent is resumed in the background from its sidechain
  and `.meta.json` sidecar — so a phase agent cannot be resumed.
- **Skills replace the pre-refactor package.** Upstream's loader and frontmatter; the three TDD
  skills are the bundled roster. The `Skill` tool returns `Launching skill: …` with the body as a
  meta message (`ToolResult.new_messages`); `allowed-tools` become `command`-source allow rules —
  ordinary rules, so the TDD tier still refuses a phase-forbidden write (a test drives one
  through the real gate). A skill's `model` sets `ToolContext.main_loop_model` (upstream's
  `mainLoopModel`; workers reset it). **A forked skill on a phase agent goes through
  phase-on-spawn** (user decision), so `skills/tool.py` is the ledger's third writer, held to the
  same `spawn_ledger = phase_on_spawn(...)` shape as the Agent tool by
  `test_run_tests_is_the_only_ledger_writer`. The `skill_listing` attachment, agent `skills`
  preload and conditional `paths` activation in ReadFile/WriteFile/Edit are wired.
- **MCP is ported** (`mcp==2.3.0`). Servers connect once per run from the project root's
  `.mcp.json` and the user/local/policy scopes (headless: project servers load without a
  prompt) and close when the run ends. Their tools join the main pool through
  `assemble_tool_pool(mcp_tools=…)` and the workers' roster; in coordinator mode the
  coordinator filter removes them from the main thread (`mergeAndFilterTools`). **An MCP call
  needs an allow rule** (`MCPTool.checkPermissions` is passthrough, so headless denies it); the
  TDD tier runs first. An allowed MCP tool that writes files is outside barrier 2's
  classification; barriers 1 and 3 still hold. MCP input is not schema-validated locally
  (upstream's passthrough schema). Server instructions are the uncached `mcp_instructions`
  system-prompt section unless `TDDAGENTS_MCP_INSTR_DELTA` moves them to the delta attachment.
- Deleted: `app/loop/tools/mcp.py` and `skills/activation.py` (pre-refactor inventions), with
  `tests/test_skills.py`, `test_skill_mutation_booster.py` and `test_plan_b_extensibility.py`.

*Divergences, all recorded:*
- background is refused for phase agents, and a resumed agent always runs in the background;
- `SendMessage`: local agents only (no teammates, mailbox, broadcast, shutdown/plan protocol,
  UDS/bridge peers); `to` must be an agent id; plain-text messages; trimmed prompt;
- no general-purpose agent: a resume with no recorded type fails, and a forked skill without
  `agent:` falls back to the first defined agent (upstream's last fallback);
- the task output dir is `<config>/projects/<cwd>/<run id>/tasks/`, not the temp dir, and resume
  re-creates the output symlink;
- headless queue: each command is its own turn (no prompt batching — nothing enqueues a
  main-thread prompt);
- MCP: ws transport is 1.x's client ported onto `websockets` (2.x has none); stdio servers get
  the full environment as `subprocessEnv()`; content blocks ride in `ToolResult.metadata`, with
  a string form for hooks and result governance;
- skills: listing widths use `len()`; result messages are not tagged with the tool-use id.
- Not ported: progress UI, OAuth/claude.ai connectors/IDE transports, session-expiry reconnect,
  `list_changed`, URL elicitation (answered `cancel`), MCP prompts/skills as commands, remote
  skills, skill hooks, `addInvokedSkill`, skill search, effort, worktrees, fork resume.

`ToolContext.tool_use_id` is upstream's `toolUseContext.toolUseId`: the pipeline hands each
`tool.call` a copy carrying its own call id, so a background agent's `<task-notification>`
names the `Agent`/`SendMessage` call that started it.

**Pre-push gate, Phase 8 (2026-10-25).** Measured under `paths_to_mutate` = `command_queue.py`,
`tasks/`, `tools/{task_output,task_stop,send_message}.py`, `agents/{resume,run_agent,tool}.py`,
`attachments/{agent_messages,skills,queued}.py`, `skills/`, `mcp/`, `runner.py`,
`hooks/async_registry.py`, `query_engine.py`, at 6248 tests:

| | |
|---|---|
| Mutants | 6758 first run (132 timeout); 6679 after triage removed dead code (151 timeout) |
| Killed (gate run) | 5733 — **86.5%** |
| Killed after triage (confirmed by a second full run) | 6239 — **95.6%** (6239 / 6528) |
| flake8 / ruff / mypy | 0 / 0 / 0 (strict, 357 files) |

Triage killed ≈550 mutants with tests, each verified inside `mutants/` against the
`MUTANT_UNDER_TEST=""` baseline (MCP ≈400, tasks/agents/runner 122, skills 50).
- **Real gap:** `<tool-use-id>` never reached a background notification, because the context
  carried no call id (fixed above).
- **Dead code removed:**
  - `McpServerConfig.extra` and the server-schema reason strings (the caller only asked
    pass/fail; the error is always upstream's `SCHEMA_ERROR`);
  - `TaskOutput`'s unread `description`, a redundant `replace` in the wait loop;
  - `getattr` reads of `ToolContext` fields that always exist, `parse_arguments`' blank guard
    (`shlex.split` already returns `[]`), and `args.get("skill")` behind the required schema.
- **Remaining survivors are the recorded equivalent classes:**
  - log wording and arguments (the bulk, ≈180);
  - default arguments, codec aliases, `False`→`None` (incl. `ensure_ascii`), dataclass and
    `getattr`/env defaults that miss the same way;
  - schema- or validation-guaranteed inputs, messages the engine overwrites, values nothing
    reads (`finalize_agent_tool`'s ids in the background lifecycle);
  - the connection task's `gather` variants (it catches everything and has finished when
    `ready` fires), `subprocess.run` `check`/`text` flags on a caught error, timing granularity;
  - the `.mcp.json` walk's `parents[:-2]` (needs a file in a top-level directory);
  - the Phase 0/7 runner classes (pool `phase_ledger`, `user_input`, the `"settings"` fallback).

### What Phase 9 changed (2026-10-26)

The session shell and the entry point, ported test-first. The user's direction: follow
claude-code (`print.ts` → `runHeadless`) where it has an answer, and decide the rest by
interview (recorded in the plan's Phase 9 outcome).

- **One graph owns the session** (`app/session/shell.py`):
  `analyst ⇄ analyst_review → engineer → planner → execute_plan_item → evaluator ⟲`.
  - The old blocking `RequirementsOrchestrator`, `graph/nodes/requirements_*`,
    `RequirementsState` and `graph/orchestrator.py` (`TDDOrchestrator`, which ran the analyst
    a second time and auto-answered `/yes`) are deleted.
  - The analyst is split in two nodes because LangGraph re-runs an interrupted node from its
    first line: with the model call and the `interrupt()` together, every resume asked the
    model again and the approved checklist was not the one kept.
  - The **engineer** is back: the planner plans from its specification only.
  - **Approval is `/yes` and only while a checklist is pending** (user decision); "yes",
    "ok"… are feedback. The fifth-round forced checklist is removed (no-ceilings rule).
  - LangGraph's `recursion_limit` is `sys.maxsize` (no-ceilings rule).
- **Prompts are Markdown** (`app/prompts/session/{analyst,engineer,planner}/`, built by
  `app/session/prompts.py`); their Jinja2 templates are deleted. The planner prompt's stale
  "Section 6" now names the engineer's "Core Dependencies" section.
- **The shell's model calls go through `with_retry`** (`app/session/llm.py`), the loop's one
  retry policy; the seams raise provider errors raw. The old 3 × 3 s retry is gone.
- **Headless** (`python -m app.main -p`, upstream `claude -p`): the request comes from
  `--spec`, the prompt argument or stdin; a checklist is approved, a clarifying question
  fails the session (`HEADLESS_CLARIFICATION_ERROR`); exit 0 only when the session
  completed. Interactive: interrupts are answered on the terminal, `/exit`/`/quit` leave
  the thread paused at its checkpoint.
- **Startup flags reach every item's `RunConfig`**: `--permission-mode`, `--settings`,
  `--dangerously-skip-permissions`; `--coordinator` sets `TDDAGENTS_COORDINATOR_MODE`
  (still off by default, upstream's default).
- **Checkpointing**: `Config.POSTGRES_URL` by default, the logged `MemorySaver` fallback,
  `--checkpoint memory` to opt out. `Gates.postgres_checkpointing` now records whether the
  connection held (`is_persistent_checkpointer`). `POSTGRES_URL` is no longer required at
  import.
- **Resume** (`app/session/driver.py`, `app/loop/runner.py::load_initial_messages`): a thread
  with a checkpoint continues from it, never restarts. Each item's run id is
  `item_run_id(thread, index)` (uuid5), so an item the previous process left unfinished finds
  its transcript and resumes it: `loadConversationForResume`, SessionStart(`resume`), the
  interrupted prompt re-submitted once (`removeInterruptedMessage`). **The ledger is never
  restored**; RED must be observed again.
- **Per-item reset**: fresh `AppStateStore` (as before), run memory discarded, and now
  `reset_session_context(run_id)` after every item.

*Divergences, all recorded:*
- ~~the interrupted-turn auto-resume is always on~~ — since 2026-11-01 it is gated as upstream
  gates it (`TDDAGENTS_RESUME_INTERRUPTED_TURN` for `CLAUDE_CODE_RESUME_INTERRUPTED_TURN`): with
  upstream's `[Request interrupted by user]` message now written on every abort, an always-on
  resume would submit that text as the prompt (user decision);
- a resumed item with no interrupted turn submits its sub-requirement again as the new
  prompt (the shell is the SDK caller that supplies the prompt);
- run ids keep the `loop-` prefix before the uuid hex;
- resume skips coordinator-mode matching, agent-setting restore and `--resume-session-at`.

**Pre-push gate, Phase 9 (2026-10-26).** Measured under `paths_to_mutate` = `app/session/`,
`app/main.py`, `loop/runner.py`, `loop/storage/recovery.py`, at 6363 tests:

| | |
|---|---|
| Mutants | 2961 first run (166 timeout); 2951 after triage removed dead code (168 timeout) |
| Killed (gate run) | 2388 — **85.4%** |
| Killed after triage (confirmed by a third full run) | 2708 — **97.3%** (2708 / 2783) |
| flake8 / ruff / mypy | 0 / 0 / 0 (strict; `app/main.py` joins `[tool.mypy] files`) |

- **Untested entry point.** The menu, the dialogue formatter, the artifacts and the report had
  no exact-output tests; they now pin the user-facing text and the wrapping (width,
  hyphens, `(empty)`), and `build_parser` pins every help string.
- **Dead code removed:** `node_execute_plan_item`'s `iscoroutinefunction` branch (an
  awaitable result already covers a coroutine function).
- **Test isolation bug found on the way:** `--coordinator` writes `os.environ`, and
  `monkeypatch.delenv` on an absent variable records nothing, so coordinator mode leaked into
  later tests. The fixture now `setenv`s first.
- **Remaining 75 are the recorded equivalent classes:**
  - log wording;
  - codec aliases;
  - default arguments;
  - `False`→`None` on textwrap flags;
  - argparse's own `default=None`;
  - `FileHandler`'s default `mode="a"`;
  - the memory-URI tuple's `""` (already caught by `not checkpoint_uri`);
  - `hasattr(saver, "setup")` on the real `PostgresSaver`, which always has it;
  - `run_sync`'s `max_workers`;
  - `RetryOptions.model` (read only for the fallback, unset here);
  - `route_after_planner`'s `.get` default;
  - the Phase 6 recovery bounds and the Phase 0 pool `phase_ledger` class.

### What Phase 10 changed (2026-10-26)

Cleanup and reorganization. The user's direction: delete every piece of dead and legacy code,
and lay `app/` out like `reference/claude-code/src/` while keeping the TDD customization. Each
choice below was put to the user.

- **`app/` mirrors upstream's tree** (see [The architecture](#the-architecture--claude-codes-loop-laid-out-like-its-source)).
  - Paths are upstream's in snake_case. A `.ts` file and a directory with the same name
    become `<name>/<name>.py`.
  - Modules that ported one upstream file are merged. Examples: `utils/attachments.py`,
    `utils/hooks/hooks.py`, `utils/permissions/permissions.py`, `utils/session_storage.py`,
    `services/api/claude.py`.
  - Modules that held several upstream files are split. `fs.py` became the five file tool
    directories plus `tools/utils.py`; `context/__init__` became `tool.py` and
    `state/app_state_store.py`; the permission messages went to `permission_rule_parser.py`,
    `utils/settings/constants.py`, `types/permissions.py` and `utils/messages.py`.
  - The import cycles the merges exposed are broken along upstream's own boundaries:
    `StopHookResult` in `query/stop_hooks.py`, `CapturedProcess` in `utils/shell_command.py`,
    `CompactionResult` (the loop's) in `query/state.py`. Two cycles remain, each broken by one
    function-level import with a comment: the TDD stop hook and the TDD state source.
- **TDD customization is one package, `app/tdd/`.** It holds the ledger, `RunTests`
  (`run_tests_tool/`), both phase-permission modules, the stop hook, phase-on-spawn and the
  background refusal, the TDD state attachment, and the event log (`events.jsonl`). The
  session shell's LLM seams are `app/session/agents/`.
- **Each tool prompt is `prompt.md` beside its tool.** `app/prompts/tools/` is gone.
  `RunTests` now reads its live text from `tdd/run_tests_tool/prompt.md`; the stale
  `run_tests.md` and the unused `agent.md` are deleted.
- **Package hubs are gone.** Every import names the defining module, and no `__init__.py`
  re-exports anything.
- **Deleted as legacy:**
  - the pre-loop pipeline: `app/graph/`, `app/errors/`, `app/schema/`, the tester, developer,
    reviewer and runner agents and their Jinja2 templates;
  - `app/sync/`;
  - `utils/{prompt_loader,sandbox_utils,spec_loader,workspace}.py`;
  - the runtime prompt registry and ~830 copied upstream prompt files, both in
    `app/loop/prompts/` and in `docs/`;
  - `Config.AgentState` and every constant nothing read;
  - `jinja2` and `markupsafe` from `requirements.txt`.
- **Deleted as dead code:**
  - Inventions superseded by the ports, with no production caller: `permissions/capability.py`,
    `context/slicing.py`, `workspace/router.py` (`DualWorkspace`), `SessionLatches` and the
    cache-breakpoint budget, `load_instruction_file`, permission-mode cycling,
    `WorkspaceError.retryable`, `Gates.web_tools_available`, `ModelCallError`.
  - `agents/fork.py`: forking is off headless, and nothing called it.
- **Metrics (user decision):**
  - F1/F2 and the resilience metrics are dropped. Under the ledger, every completed item is a
    confirmed Red-then-Green cycle, the reviewer tags the resilience ratios came from no
    longer exist, and `main.py` was writing zeros.
  - `subreq_results.txt` lists each `PlanItemResult`: status, terminal reason and the final
    ledger.
- **Kept:** the `Workspace` protocol, `LocalWorkspace` and `E2BWorkspace` with its adapter,
  for the plan's E2B end-to-end run.
- **The gate covers the whole tree.**
  - flake8 has zero findings across `app/` and `tests/`, and mypy is strict on all of `app/`.
  - mutmut mutates `app/` minus the seams (`do_not_mutate`).
  - A subprocess test now gives its child `MUTANT_UNDER_TEST=""`. Otherwise import-time
    prompt loading hits mutmut's trampoline in the child, and stats collection fails.

*Divergences, all recorded:*
- Two function-level imports where upstream's single-file TypeScript has no cycle;
- `app/tdd/` and `app/session/` have no upstream counterpart;
- the `tools/utils.py` the five file tools share is ours (upstream spreads those helpers over
  `utils/file.ts` and the tools);
- every package is a namespace package, so there is no `index.ts`-style re-export.

**Pre-push gate, Phase 10 (2026-10-26).** The full baseline, `paths_to_mutate = ["app/"]`
minus the seams:

| | |
|---|---|
| Mutants | 39 706 first run (1 002 timeout); 39 644 after triage removed dead code (463 timeout) |
| Killed (gate run) | 37 249 — **96.2%** (37 249 / 38 704) |
| Killed after triage (confirmed by a second full run) | 37 829 — **96.5%** (37 829 / 39 181) |
| flake8 / ruff / mypy | 0 / 0 / 0 (strict, 313 files, all of `app/` and `tests/`) |

Triage covered the code Phase 10 changed; the moved code keeps the classes its own phase
recorded above.
- **Dead code deleted:** the tool builders' `vars` (no caller), `get_prompt`'s rendering
  branch, `parse_markdown_frontmatter`'s `<!--` branch, the identity label map in
  `write_subreq_results`.
- **Real gaps killed:** `${VAR}`/`{var}` substitution (its test went with the registry),
  Bash's 30 000-char result cap, `write_subreq_results`' missing-text and mid-plan NOT_RUN
  cases.
- **Remaining survivors in changed code** are the recorded equivalent classes:
  - codec aliases;
  - log wording;
  - `getattr` defaults on attributes that always exist;
  - `build_tool` defaults (`False` → `None`, dropped);
  - the `strip_html_comments` loop sentinel;
  - the TDD stop hook's unread `config` argument.

### What Phase 11 changed (2026-10-27)

The interactive TDD REPL: after the planner, the tool behaves like Claude Code. The user's
direction: follow claude-code's source and architecture while enforcing the TDD cycle, with
the open choices decided by interview (recorded in the plan's Phase 11 outcome).

- **One conversation per session.** The shell ends at the planner (`PLANNED`).
  `execute_plan_item`, `evaluator`, `PlanItemResult`, `tdd_loop_runner` and `item_run_id`
  are deleted. `open_session_run` assembles the run once; every user turn is a
  `QueryEngine.submit` over the same history (`REPL.tsx` `onQuery`). The session's run id
  is derived from the thread (`session_run_id`).
- **The plan is state, and only the ledger moves it.**
  - `TddPlan` lives on `AppState` and is saved to `<session dir>/tdd-plan.json`. It is
    restored on resume; the ledger is not.
  - `plan_advance` runs **first** in `build_stop_hooks_runner`. A complete cycle marks the
    item done and blocks the stop with the next item (`next_item_message`). All done →
    `completed`. `/pause` → `stop_hook_prevented`.
  - The TDD hook's `stop_hook_active` latch is per item.
  - `update_tdd_plan` is the one place the plan changes. **Any change of the current item
    resets the ledger and `last_test_run`**: reset-only, held by
    `test_run_tests_is_the_only_ledger_writer`.
  - Editing a done item reopens it. `/requirements` keeps done items and replaces the
    pending ones (`replace_pending`).
- **`UpdatePlan`** (`app/tdd/tdd_plan_tool/`) has no "done" operation. Its permission check
  is an ask carrying the diff (`ExitPlanModeV2Tool`'s pattern). It is main thread only, and
  denied headless.
- **Interactive permissions** follow upstream's `useCanUseTool` → `PermissionRequest`.
  - `PermissionResult.suggestions` come from upstream's Bash (exact command / prefix,
    compound capped at `MAX_SUGGESTED_RULES_FOR_COMPOUND = 5`, none for a security ask) and
    filesystem generators.
  - `ToolContext.can_use_tool` is called on an ask. "Don't ask again" persists the
    suggestions through `persist_permissions`, and a rejection sends upstream's
    `REJECT_MESSAGE`.
  - The TDD tier and deny rules are decided before any question.
  - Workers and the agent-hook verifier have `can_use_tool=None` (headless deny).
- **The REPL** (`app/screens/repl.py`):
  - a line typed mid-turn is queued at `next` and reaches the model as `queued_command`;
  - Esc cancels the turn's token, which `_submit` resets at the next turn, and answers a
    pending question No;
  - permission questions are answered by the next line;
  - slash commands wait for the turn, except the `immediate` ones;
  - a crashed turn is reported as `model_error` and the session goes on.
- **Headless** (`develop_headless` → `run_headless`) is the same engine with nobody to ask.
  A turn that ends before the plan is done stops the session; the rest is NOT_RUN and the
  exit code is 1. `subreq_results.txt` lists DONE / FAILED (with the terminal) / NOT_RUN.
- `prompt-toolkit==3.0.52` joins `requirements.txt`. `app/screens/terminal_io.py` is a
  seam (`do_not_mutate`, static surface check `tests/test_terminal_io_surface.py`).
- **Fixed: the session workspace was unwritable.** Its path, `.tddagents/runs/<thread>/workspace/`,
  holds the protected `.tddagents` directory (Phase 3), so every write was a safety ask, and
  a headless run denied it. The first live run could not create a test file. Upstream's
  `.claude/worktrees/` carve-out is ported as `STRUCTURAL_SUBDIRECTORIES` with
  `(".tddagents", "runs")` (user decision). A config directory inside a workspace is still
  protected, and a test pins `Config.LOCAL_WORKSPACE_ROOT`.
- **Fixed: RunTests ran generated projects under TDDAgents' own `pyproject.toml`.** A local
  workspace sits inside this repository, so a project with no pytest config of its own picked
  up ours by pytest's upward search (`rootdir` = the repo). `pytest_command` now pins
  `--rootdir=.` and adds `-c /dev/null` when `PYTEST_CONFIG_PROBE` finds no workspace config
  (user decision). Real-pytest tests run under a poisoned parent `pyproject.toml`.
- **Fixed: agents could run git on TDDAgents' own repository.** A local workspace sits inside
  it, so the live CPF run's `git status` listed this repo. `LocalWorkspace.execute` now sets
  `GIT_CEILING_DIRECTORIES` to the workspace's parent, last, so no caller lifts it (user
  decision). A repository of the workspace's own still works.
- **Planner and development prompts, from the live runs** (user decisions):
  - one behaviour per plan item, its tests and its implementation in the same item;
  - every item adds behaviour its tests can fail on (no TEST/DEV split, no
    verification-only hardening items);
  - the model implements only what the current item's tests demand.
- **Live runs (2026-10-27/28; palindrome and CPF specs).** The three barriers held in every
  run. No item was marked done without an observed Red -> Green, and faked REDs and
  production writes in RED were refused. How far a run got depended on the plan and the
  model:
  - gpt-5-mini reached 6/12 palindrome items, but kept implementing later items early;
  - gpt-5.4 completed CPF items 1-3 cleanly, then met an item an earlier one already
    satisfied. It proposed an `UpdatePlan` rewording, and headless denied it, **kept at the
    user's direction**: headless never approves anything, and the REPL is where a plan is
    repaired.
- **The default model is `gpt-5-mini`** (user decision): OpenAI answers 404 for `o4-mini`.
  *(Superseded 2026-10-29: Claude Haiku 5.5, below.)*
- **Ruff 0.17.1** reported 122 findings on the pushed Phase 10 tree (newer default rules).
  They are fixed, so ruff is back to 0. Every deliberate catch-all now carries
  `# noqa: BLE001 - <reason>`.

*Divergences, all recorded:*
- PermissionRequest hooks run before the question instead of racing it;
- no editable Bash prefix, and no accept-with-feedback input;
- `getDirectoryForPath` is not stat-based (a path with a suffix is taken for a file);
- coordinator mode keeps upstream's filter verbatim, so the coordinator has no `UpdatePlan`
  (upstream drops its own `TodoWrite` and `ExitPlanMode` there too); in coordinator mode the
  user edits the plan;
- the REPL is line-based (`prompt_toolkit`), not Ink.

<!-- PHASE11_GATE -->

### What Phase 12 changed (2026-10-28)

The TDD cycle itself, at the user's direction (decisions recorded in the plan's Phase 12
outcome): RED builds robust tests instead of tests that must fail, and REFACTOR becomes
mandatory and coverage-checked. The three barriers stay; the rules they enforce change.

- **The ledger** (`app/tdd/ledger.py`) is `phase, tests_written, green_passed,
  refactor_passed, coverage`. The old `red_confirmed` is renamed, and `events.jsonl`
  records the new fields, so old and new runs are distinguishable.
  - **RED** ends with a RunTests run, pass or fail, in which the item's tests changed.
  - **GREEN** ends with a passing run.
  - **REFACTOR** ends with a passing run whose line + branch coverage of the production code
    reaches `COVERAGE_THRESHOLD = 90.0` (`app/tdd/coverage_gate.py`). A failing run
    un-passes it. No production code to measure counts as met.
  - `is_cycle_complete` needs all three.
- **RED's evidence is observed, not claimed** (`app/tdd/suite_fingerprint.py`).
  - RunTests hashes the test files (as `is_test_path` classifies them, without caches and
    `.tddagents/`) through the `Workspace` protocol.
  - It compares that hash with `AppState.tdd_item_test_baseline`, taken when an item starts:
    session start and resume, a plan change of the current item, or a tester re-spawned to
    RED.
  - Writes by any tool count. RunTests stays the only evidence writer, and the baseline
    writers stay reset-only (`test_run_tests_is_the_only_ledger_writer`).
- **Coverage on every run.** `pytest_command` adds pytest-cov with a config that omits the
  test paths. The report goes to `term-missing`, so the model sees the uncovered lines, and
  to JSON in the workspace's protected `.tddagents/coverage/`, which is read back and deleted.
  - The session venv gains `pytest-cov==7.0.0` and `coverage==7.11.3`.
  - Each RunTests result says what the run did to the ledger and what the phase needs next.
- **Write rules:**
  - RED: tests only;
  - GREEN: production only, tests locked;
  - **REFACTOR: both**. The deny rules, the runtime check, the Bash redirect check and the
    pool's own copy of the rule (`assemble_tool_pool`) all changed.
- **Prompts:** the contract, the session prompts, the planner (relaxed: one behaviour per
  item, no fail-first rule, hardening items allowed), the agents and the refactoring
  catalogue, the skills, the coordinator, the compaction sections, the classifier context
  and the RunTests/UpdatePlan prompts.
  - The threshold reaches prompts as `{{COVERAGE_THRESHOLD}}` (`TDD_PROMPT_VARS`), so the
    figure is written in one place.
  - `tests/test_tdd_phase12_prompts.py` scans every prompt for the old fail-first rule.
- **The TDD stop hook never gives up** (user decision, as upstream): it has no latch. Every stop
  of an incomplete cycle, in any phase, is sent back with what is missing. A turn ends early
  only on `/pause`, Esc, a configured hook's `continue: false`, or a model/API error. A
  headless run therefore has no limit of its own, as upstream (the retry layer keeps its
  ceiling).
  - It got there in two steps, both found by live palindrome runs with gpt-5-mini.
  - The first run completed 6 of 13 items with full cycles at 100% coverage. On item 7 the model
    *said* it had run REFACTOR's tests without running them, and the one-nudge-per-item latch,
    already spent at GREEN, ended the session.
  - A per-item-and-phase latch then let a run complete 10 of 11 items. It still ended on item
    11 when the model stopped twice in REFACTOR, although the project's 30 tests all passed.
    The user then removed the latch: REFACTOR and GREEN must happen.
  - The third run then looped on item 10 (9 of 13 done): a test written in RED contradicted the
    spec, GREEN locks the tests, and the model did not know the way back. The GREEN refusals,
    the stop hook's GREEN message, the contract and the start prompt now name it
    (`phase_permissions.BACK_TO_RED`, user decision): spawn the tester agent, whose
    phase-on-spawn restarts the item at RED. No new mechanism.
  - Tests that drive a model which never finishes now end the turn as a user's Esc would
    (`tests/session_run_helpers.with_a_patient_user`, the scripted models' "script exhausted").
- **The gate** counts timeouts: each is re-run alone, and one that still hangs counts as
  detected (`scripts/mutmut_score.py --timeouts-reverified`). The integration tests' venv is
  built once (`tests/conftest.py::shared_test_venv`).

*Divergences and limits, recorded:*
- the fingerprint reads every test file on each RED run (cheap for TDD-sized projects);
- coverage measures what pytest-cov sees under `--cov=.`, so production code outside the
  workspace root is not counted;
- the TodoWrite example "Write the failing test" is kept as a sample task name.

### What Phase 13 changed (2026-10-28)

Each project gets its own folder, venv and git tree, and the agents' own test run is final.
The user's direction: follow claude-code's style (a project folder is the working directory,
told to the model with its git context) and keep the Phase 12 cycle. Decisions are recorded
in the plan's Phase 13 outcome.

- **Layout** (`app/workspace/`):
  - `workspaces_root()` is `$TDDAGENTS_WORKSPACES_DIR` or `<config home>/workspaces`.
  - `LocalWorkspace.for_run(thread)` is `<root>/<thread>/project`, and `run_dir` is the
    thread dir.
  - `session_workspace` → `prepare_project` (`.tddagents/settings.json` seeded from
    `SHIPPED_SETTINGS` only if absent) → `ensure_session_python(project)`, which builds
    `project/.venv` with pip.
  - **No `git init`, no `.gitignore`** (user decision: follow claude-code, which never creates
    a repository or commits on its own). Whether the project is a git repository is the
    model's and the user's business, and the environment section says which it is.
  - `Config.LOCAL_WORKSPACE_ROOT` is gone.
- **The project is the root of every lookup** (`project_root_of`, upstream's
  `getOriginalCwd()`): settings, skills, agents, MCP (`.mcp.json`) and hooks are read from the
  workspace root, never from `Path.cwd()`. Only a workspace with no root falls back to the cwd.
  A project with no settings therefore runs in `default` mode (`tests/conftest.py::python_workspace`
  prepares one).
- **The venv is active, never announced** (user decision: follow claude-code, which gives no
  venv or dependency guidance and lets the model judge how the project installs). It reaches
  commands the way upstream's does.
  - `app/utils/session_environment.py` ports `getSessionEnvironmentScript` and
    `bashProvider.ts`'s command assembly.
  - `TDDAGENTS_ENV_FILE` (upstream's `CLAUDE_ENV_FILE`) names a script.
  - `LocalWorkspace.execute` runs `<script> && eval '<command>'` after the login profile, as
    upstream injects it after the shell snapshot.
  - Each session writes `<thread>/session-env.sh` (`SessionPython.write_env_file`: the
    `VIRTUAL_ENV` / `PATH` exports `bin/activate` makes), so `~/.profile`'s `~/.local/bin` can
    no longer shadow the venv.
- **What the model is told** is upstream's: `Primary working directory`, `Is a git repository`
  (true or false, as the folder is), and `gitStatus` when it is one. The Bash prompt gains upstream's `# Committing changes with git`
  section (`getCommitAndPRInstructions`, commit part, without attribution): never commit unless
  the user asks. TDDAgents itself never commits.
- **A skipped test is not a pass.** `pytest_command` adds `-rs`, and `skipped_count` reads the
  summary line. In GREEN and REFACTOR an exit-0 run with skips is observed as failing, and the
  header names why. RED is unchanged. Every test must run in a plain RunTests run (user
  decision): the message says not to gate tests behind environment variables, markers or
  options, and to size an expensive check so it runs normally. A live run looped on a
  `RUN_PERF=1`-gated performance test.
- **A question from the model is normal behaviour** (user decision). A headless run has nobody
  to answer it, and the stop hook keeps sending the model back. *(Superseded the same day by
  `AskUserQuestion`, below: a question now has its own path.)*
- **The final run is the project's own suite.** `run_suite` (RunTests' own runner) runs once
  more at the end, in the project's venv, into `metrics_and_logging/final_test_run.txt`. A
  skip reads "NOT passing". This answers the live run that "passed" while a flake8 test
  silently skipped and failed elsewhere.
- **No export.** `app/session/export.py` and `workspace_output_<thread>/` are deleted; the
  reports go to `<thread>/metrics_and_logging/`. pytest-cov's data file goes to
  `.tddagents/coverage/.coverage` (gitignored), not the project root.
- **Fixed: parallel tool calls failed every request after them.** The loop stores one message
  per content block (Phase 1), so two tool calls in one response were sent as two assistant
  messages followed by their results, and OpenAI answered 400 ("An assistant message with
  'tool_calls' must be followed by tool messages…"). That was the Phase 13 live run's
  `model_error`, reproduced against the API. As upstream:
  - every block of one response is stamped with one response id (`frame_content_blocks`,
    upstream's `message.id`);
  - the request builder merges blocks of the same id back into one assistant message
    (`merge_split_assistant_messages`, `normalizeMessagesForAPI`'s `mergeAssistantMessages`),
    walking back over tool results.

  A failed model call is now logged (`Model call failed: …`, upstream's `logError`).
- **Workarounds:**
  - the write carve-out is now `(".tddagents", "workspaces")`;
  - `GIT_CEILING_DIRECTORIES` (at the thread dir) and RunTests' `-c /dev/null` probe stay,
    since a home `pyproject.toml` or repo could still be above the project.
- The fingerprint and the coverage omit leave out `.venv`.

*Divergences, recorded:*
- `GIT_CEILING_DIRECTORIES` is TDDAgents' (upstream lets git find a parent repository); it keeps
  each project isolated;
- the session env file is written by TDDAgents, and `bin/activate` is replaced by its two
  exports, because a moved venv's `activate` points at its creation path. Upstream's hook env
  files are not ported (the hook layer has no env-file protocol);
- the Bash prompt's commit section is upstream's verbatim (checked line by line against
  `tools/BashTool/prompt.ts`), with tool names substituted and no attribution line. Its HEREDOC
  note becomes "pass the message with `-m`", because `$(cat <<'EOF' …)` is refused by the
  command-injection check. The PR section is not ported (no `gh` workflow here);
- the login shell (`bash -l`) stands in for upstream's shell snapshot;
- sessions created before Phase 13 under `.tddagents/runs/` are not migrated.

### What the AskUserQuestion port changed (2026-10-28)

A live run looped because the model asked the user in plain text, with nobody to answer, and the
TDD stop hook sent every stop back. The user's direction: follow claude-code's source, where a
question is a tool call. TDDAgents is mostly interactive (user decision), so the REPL is the main
path and headless keeps upstream's `-p` behaviour.

- **`AskUserQuestion`** (`app/tools/ask_user_question_tool/`) is upstream's tool, ported whole:
  - the schema (1–4 questions, 2–4 options, `multiSelect`, `answers`, `annotations`, `metadata`)
    and the uniqueness refine;
  - `checkPermissions` always asks, and `requiresUserInteraction` keeps that ask in every mode;
  - the result is `mapToolResultToToolResultBlockParam`'s text.

  It sits on the main thread beside `UpdatePlan`. Workers never get it
  (`ALL_AGENT_DISALLOWED_TOOLS`), and neither does the coordinator (upstream's filter).
- **In the REPL** the question is its own prompt, as upstream's `AskUserQuestionPermissionRequest`.
  `build_interactive_can_use_tool(ask, ask_questions)` hands the questions to
  `Repl.ask_questions`, which asks one at a time. A number picks an option, several numbers pick
  for a multiSelect question, and any other text is the user's own answer ("Other"). The call is
  allowed with the answers in its input. Esc declines and aborts the turn (`onReject`).
- **With nobody to ask** (headless, `dontAsk`) the call is denied with upstream's
  `AUTO_REJECT_MESSAGE`, as `-p` denies it. Upstream's `-p` then ends when the model stops; here
  `awaiting_user_answer` (a denied `AskUserQuestion` since the user last spoke) makes the stop
  hooks end the turn `stop_hook_prevented` before the TDD hook can send it back. The outcome
  carries the questions (`DevelopmentOutcome.question`) and `app/main.py` prints them with the
  resume command. `--thread-id <id> "<answer>"` continues the conversation with the answer as
  its next prompt (upstream's `--resume <id> "<prompt>"`), in the REPL or headless. A memory
  checkpoint cannot be resumed, and the report says so.
- **The TDD stop hook's refusal** ends with `ASK_THE_USER` when the pool holds the tool (user
  decision): "If you need a decision from the user, call AskUserQuestion instead of asking in
  text."

*Divergences, recorded:* the prompt has no preview guidance (upstream adds it only for an SDK
caller with a preview format), and its plan-mode note becomes a TDD plan note (user decision, after a
live REPL run asked "Proceed?" twice): no "Should I proceed?" between sub-requirements, so the HTML preview
check is not ported; the uniqueness check runs in `validate_input`; the REPL is line-based and
takes no per-option notes; the headless stop on a denied question is TDDAgents' stand-in for
upstream's `-p` ending at the model's stop, which the TDD stop hook would otherwise refuse. The
headless analyst's clarifying question still fails the session (user decision: TDDAgents is
mostly interactive).

### Absolute paths follow upstream (2026-10-28)

The interactive live run found the workspace's Phase 1A path rule, "a leading `/` means from the
workspace root", disagreeing with the permission gate. The tester wrote
`<project>/test_palindrome.py` by its absolute path, as the env info invites. The gate
(`expand_path`, upstream's `expandPath`) approved that real file, and the workspace wrote
`<project>/<project path>/test_palindrome.py`. At the user's direction the path contract is now
upstream's:

- `normalize_path(path, root)` (`app/workspace/base.py`) reads an absolute path as a real one.
  Inside `root` it is that file; outside it, or on a workspace with no host root, it raises
  `WorkspacePathError` ("outside the project"), because the workspace is bounded.
  `workspace_path(ws, path)` passes the workspace's own root.
- `LocalWorkspace.resolve`, the E2B adapter (`Config.SANDBOX_WORKSPACE_ROOT`), and the file tools'
  `readFileState` keys, `path_kind`, Glob and Grep all use it. An absolute Glob pattern names its
  own base directory, as upstream's `extractGlobBaseDirectory` does.
- A persisted tool result is keyed by its host path (`read_state_key`), since it lives outside the
  workspace on purpose.

*Limit, recorded:* `E2BWorkspace` exposes no host root, so the file tools refuse an absolute
path there; its adapter maps the sandbox root itself.

### An uncollected test file is not a pass (2026-10-28)

The third interactive palindrome run completed 11/11 items, but its own `pytest.ini`
(`python_files = tests/test_*.py`) never collected `tests/tests_trivial.py`. RED had passed on it,
because RED's evidence was "the test files changed and RunTests ran". At the user's direction an
uncollected test file is treated like a skip (`app/tdd/collection_gate.py`):

- `pytest_command` writes a JUnit report (`xunit1`, which names each case's `file`) to
  `.tddagents/coverage/junit.xml`. `run_suite` reads it back and deletes it, into
  `SuiteExecutionResult.collected_files`. The report is independent of the project's verbosity.
- The project's test modules are the `.py` files `is_test_path` classifies as tests that define a
  pytest test (`def test…`, `class Test…`), without `__init__.py` and `conftest.py`, and within
  the run's `test_path`.
- A module pytest did not collect means RED gets no evidence from the run, and a GREEN or
  REFACTOR pass does not count. The message names the files and the `python_files` / `testpaths`
  way out. The final run reports it ("NOT passing … not collected: …"). An injected runner with
  no report (`collected_files=None`) checks nothing.

The same run also showed `events.jsonl` stamping `plan_index: 0` on every event: since Phase 11
one session spans the plan, so the recorder now reads the current item from the plan at each
event.

### Scope rules from upstream (2026-10-28)

The same run's plan added a README, a CI workflow and a "release preparation" step, although the
specification said documentation must not be implemented. At the user's direction:

- `app/prompts/system/doing-tasks.md` is now upstream's `getSimpleDoingTasksSection` verbatim (it
  was a loose paraphrase): the external build's items, with "Don't add features … beyond what was
  asked", "Do not create files unless they're absolutely necessary" and "Don't use feature flags".
  `tests/test_doing_tasks_prompt.py` parses `prompts.ts` and compares the whole section when the
  reference snapshot is present. *Divergence:* the `/help` and feedback bullets are dropped (this
  build has neither).
- The planner and engineer prompts (TDDAgents-authored) carry the same rule: specify and plan only
  what was asked, with no documentation, README, CI, packaging or release steps unless asked, and
  nothing the requirements say must not be implemented. *(Superseded 2026-10-29: the prompts follow
  upstream's Plan agent and the user reviews the specification and the plan; see "The user reviews
  the specification and the plan".)*
- Env-gated tests (`SKIP_HEAVY_TESTS`) are left to the prompts, as upstream steers behaviour:
  the ported "no feature flags" rule and RunTests' "never gated behind environment variables".
  There is no heuristic scanner (user decision).

### Agent memory reachable; coverage is a rule of thumb (2026-10-28)

The fourth interactive palindrome run (9/9 items, 36 tests, 100% coverage, the collection gate
firing on `tests/tests_basic.py` until it was renamed) found two more problems. Each was decided
by the user:

- **Agent memory never worked.** The run-scoped store lives outside the project (I5) and the
  file tools are bounded to it, so the tester's `MEMORY.md` read was an ask (denied headless), its
  write was refused, and it wrote the notes into the project root.
  - Upstream's carve-out is ported: `AgentMemoryStore.is_memory_path` (`isAgentMemoryPath`:
    this run's `run-memory/<run>/` and the session, local, project and user roots, never another
    run's) and `agent_memory_target(context, path)`.
  - `check_write_permission_for_tool` allows it right after the deny rules (upstream's step 1.5,
    `checkEditableInternalPath`), and `check_readable_internal_path` allows reads.
  - The TDD phase tier lets a file tool write it in every phase: it is neither a test nor
    production code.
  - The file tools read and write it on the host (`host_path`, `write_text`), as they already
    read a persisted tool result.
  - The same review fixed `workspace_absolute_path`: like `normalize_path`, it now keeps an
    absolute path (upstream `expandPath`) instead of re-rooting it in the project.
- **REFACTOR's coverage gate is dropped.** With "no TypeError tests" and a 90% gate, the agents
  wrote a test that only called `is_palindrome(42)` to cover the raise branch.
  - REFACTOR is still mandatory, but it now ends on a passing run (`PhaseLedger.with_test_result`).
  - Every run still measures and reports coverage. `COVERAGE_RULE_OF_THUMB = 90` reaches the
    prompts as `{{COVERAGE_RULE_OF_THUMB}}`, described as a good rule of thumb, not a gate.
  - The refactorer, the contract and the skill say a test asserts behaviour and never only
    executes code to cover it.
  - This supersedes the Phase 12 coverage threshold described above.

### The plan advances at the tool boundary (2026-10-28)

A coordinator-mode live run confirmed agent memory works: the tester, developer and refactorer
each wrote `<thread>/.tddagents/run-memory/<run>/<agent>/MEMORY.md`, and nothing landed in the
project. It also showed a flaw in how the plan advances.
- The plan advanced only at a **stop**, but the coordinator never stops between items: it spawned
  the next phase's tester in the same turn.
- Phase-on-spawn then restarted RED on the still-current item and erased its completed cycle, so
  after 11 spawns no item was done.

At the user's direction, the advance now also happens where upstream delivers attachments, between
tool rounds:
- After every tool batch on the main thread, `advance_between_tools` (`app/tdd/plan_advance.py`,
  called from `run_loop`) records a completed item through `update_tdd_plan` and restarts the
  ledger. The next item's announcement is added as a meta message.
- A worker's batch never advances the plan: the main thread does once the worker's Agent call
  returns.
- The stop-time advance stays, for a cycle completed by the turn's last call.
- A done item keeps its completing ledger in memory (`PlanItem.completed_by`; not in the
  sidecar), and the event recorder writes one `plan_item` event for it. That event is the cycle's
  record in `events.jsonl`, since the transitions after an advance show the restarted ledger.

### Every static system section and the Bash prompt are upstream's (2026-10-28)

A live CPF run's agent assumed Docker was not available and planned a skipped smoke test. Later it
wrote a 30-line, newline-separated verification script with `|| true` after most steps, calling
`docker-compose` (v1) where the machine has `docker compose`. Upstream has no "detect versions"
mechanism: it tells the model its platform and shell (our env info already matches), and its
prompts make the model check before acting and never hide a failure. Ours were paraphrases. At
the user's direction they are now upstream's text verbatim (external build, our tool names):

- `app/tools/bash_tool/prompt.md` is `getSimplePrompt`:
  - the dedicated-tool preferences;
  - `# Instructions`: `ls` before creating, quoting, absolute paths, the timeouts, chaining with
    `&&`, "DO NOT use newlines to separate commands", the git items, and the sleep items ("Do not
    retry failing commands in a sleep loop — diagnose the root cause").

  The timeouts are filled in code (`get_bash_prompt`, upstream's interpolation). Our
  command-injection note and the commit section stay.
- `system.md` (`getSimpleSystemSection` with `getHooksSection`), `actions-with-care.md`
  (`getActionsSection`), `tools.md` (`getUsingYourToolsSection`, with TodoWrite as the task tool)
  and `tone.md` (`getSimpleToneAndStyleSection`) are upstream's.
- `output-efficiency.md` (`getOutputEfficiencySection`) joins the static order before the TDD
  contract.
- `tests/test_system_prompt_sections.py` parses `prompts.ts` and `BashTool/prompt.ts` and
  compares every section when the reference snapshot is present.

*Divergences, recorded:*
- The Bash prompt's sandbox section is empty, as upstream's is without a sandbox. (Its
  `run_in_background` note and background sleep items arrived with the background shells,
  2026-11-01.)
- `RunTests` and `Agent` guidance left `tools.md`. The TDD contract covers `RunTests`, and
  upstream puts the Agent guidance in a session-specific section, outside this one.

### Every prompt with an upstream source, and the tools they describe (2026-10-28)

At the user's direction the port went on to **every prompt with an upstream source**, and to the
tool features those prompts promise, so no prompt describes something a tool cannot do. Each text
is upstream's, verbatim, with our tool names, and is pinned by `tests/test_system_prompt_sections.py`
against the reference snapshot.

**Product substitutions** (user decision), applied wherever upstream names itself:
"Claude Code" → "TDDAgents", "Anthropic's official CLI for Claude" → "UFF Software Testing Team's
official CLI tool for TDDAgents", and `CLAUDE.md` → `TDDAGENTS.md`.

- **Prompts:**
  - `identity.md` is `getSimpleIntroSection` (no output style) with `CYBER_RISK_INSTRUCTION` and
    the URL rule. TDDAgents' TDD identity stays in `tdd-contract.md`.
  - WriteFile, Edit, Glob and Grep prompts are upstream's; our extra lines (parents created
    automatically, Glob truncation, Grep pagination notes) are gone.
  - `explore/` and `plan/` are `exploreAgent.ts` / `planAgent.ts`. Their frontmatter keeps
    `model: inherit` (upstream's external `haiku` would cross providers).
- **ReadFile reads images, PDFs and notebooks** (`FileReadTool.ts` `callInner`), and its prompt is
  upstream's full `renderPromptTemplate`:
  - images are downscaled with Pillow (`app/utils/image_resizer.py`, upstream's limits in
    `app/constants/api_limits.py`) and returned as `ToolResult.content_blocks`;
  - PDFs: whole files up to `PDF_AT_MENTION_INLINE_THRESHOLD` pages as a document block, more
    need `pages` (at most `PDF_MAX_PAGES_PER_READ`), which `pdftoppm` renders to page images
    (`app/utils/pdf.py`; poppler is a system dependency);
  - notebooks are `notebook.ts`'s cell mapping (`app/utils/notebook.py`);
  - *provider delivery (user decision):* Anthropic gets the media inline in the tool result; for
    any other provider, `move_tool_result_media_to_user` (`app/utils/messages.py`) moves it to a
    meta user message after the result, because OpenAI's tool messages cannot carry images.
- **WebFetch** (`app/tools/web_fetch_tool/`) ports `WebFetchTool`: URL validation and the
  HTTP→HTTPS upgrade, same-host redirects followed and cross-host ones reported, the 15-minute LRU
  cache, HTML→markdown (markdownify for turndown), `PREAPPROVED_HOSTS`, and `WebFetch(domain:…)`
  permission rules with upstream's suggestions. Pages are processed by `TDDAGENTS_SMALL_FAST_MODEL`,
  else the run's model, through `with_retry`. It is in the built-in roster and the classifier table.
- **The verification agent** is `verificationAgent.ts`: the system prompt verbatim, upstream's
  `whenToUse`, `background: true`, `model: inherit`, no `tools` list (every tool its denylist
  leaves) and `disallowedTools [Agent, WriteFile, Edit]`.
  - `criticalSystemReminder_EXPERIMENTAL` is ported: a built-in agent's `criticalSystemReminder`
    frontmatter reaches `ToolContext.critical_system_reminder`, and the
    `critical_system_reminder` attachment re-sends it at every worker turn.
  - `ToolContext.shares_app_state` is upstream's `shareSetAppState` (`False` for a background
    worker). RunTests in such a worker reports what the tests did, writes nothing and claims no
    phase move (`BACKGROUND_RUN_NOTE`), since its ledger write would be discarded anyway.

*Divergences, recorded:*
- `CLAUDE.md` in upstream's prompts reads `TDDAGENTS.md`, this build's memory file;
- built-in agents are Markdown here, so `criticalSystemReminder` is a frontmatter field honoured for
  built-ins only (upstream sets it in TypeScript);
- WebFetch: no domain-blocklist preflight to Anthropic's `domain_info` service (user decision), and
  the user agent is TDDAgents';
- ReadFile's media reach a non-Anthropic model as a meta user message, not inside the tool result.

### RunTests reports what it observed (2026-10-28)

The CPF live run's agents wrote `fastapi.py` and `pydantic.py` instead of installing the packages,
and the tests passed against them. **RunTests caused it**: pytest failed on `ModuleNotFoundError:
No module named 'fastapi'`, RunTests called that "the code under test does not exist yet", and in
GREEN the model wrote the "missing code". A first fix, a TDDAgents-own guard (dependency
directories, stand-in names, the specification's Core Dependencies), was **removed at the user's
direction**: follow claude-code, and redesign RunTests so a misinterpretation cannot happen again.

- **Claude-code's way of reporting a command.** Upstream's Bash never reads a command's output
  for meaning:
  - `tools/BashTool/commandSemantics.ts` maps exit codes to `isError` and a label, ignoring
    stdout and stderr;
  - the model sees `Exit code N` plus the raw output (`utils/toolErrors.ts::getErrorParts`).

  RunTests now says only:
  - the exit code with pytest's documented meaning (`PYTEST_EXIT_SEMANTICS`, upstream's
    `COMMAND_SEMANTICS` shape, with a `DEFAULT_SEMANTIC`-like fallback). It is the seam where
    another runner brings its own table;
  - what pytest's reports record: collection errors and uncollected files from the JUnit report,
    the skipped count from its summary, the measured coverage;
  - a `TDD ledger:` line with the change, the phase's rule (`PHASE_RULES`) and whether the run
    met it;
  - the raw output, last, rendered as upstream renders it.

  It never states a cause and never gives advice. `test_runtests_states_no_cause_and_gives_no_advice`
  pins that.
- **Exit 2 in RED** counts only when pytest's JUnit report records collection errors
  (`collection_error_files`, the `<error message="collection failure">` test cases). It is read
  structurally, never from the error text.
  - That keeps RED possible for a test importing code not written yet, without guessing why the
    import failed.
  - *Accepted consequence:* any test file pytest cannot import, a SyntaxError included, counts
    for RED, and GREEN then needs it fixed.
- **`is_error` is table-driven**, as upstream:
  - exit 0 and 1 are not errors (failing tests are TDD data, like grep's exit 1);
  - nor is an exit 2 with recorded collection errors;
  - every other code, a timeout or no runner is an error, and the ledger is untouched.
- **Kept from the first fix** (claude-code's own text): the two internal-build Doing-tasks items
  (`constants/prompts.ts:211`, `:240`), and `get_session_specific_guidance_section`
  (`app/context.py`) with the verification contract, prompt-only (on only with `TDDAGENTS_VERIFICATION_AGENT`, below).
- The session's final run (`app/main.py::write_final_test_run`) names the table's meaning too.
- **Which files ran is observable only for a run pytest finished** (`files_that_ran`: exit 0 or 1).
  When pytest stops at collection errors its JUnit report lists only the failing files. Reading
  "not collected" into that report kept the live CPF run in RED, and the agent replaced its test
  with a placeholder. The skipped count is read from the report too (`skipped_tests`); the
  summary line is only a fallback.

- **The TDD stop hook's refusal states the rule and the fact, too** (`incomplete_cycle_message`,
  the same `PHASE_RULES`). A live CPF plan held two items for one behaviour (the adapter's tests,
  then its implementation), so the second item's behaviour was already built. The old refusal said
  only "RED is not done (tests_written=False)", and the agent took its earlier tests as this item's.
  It then asked the user the same question three times, because every refusal ended with "If you need
  a decision from the user, call AskUserQuestion…". Now (user decisions):
  - the RED refusal says the test files "have not changed since this sub-requirement started";
  - it names both ways forward: extend this item's tests, or, when an earlier item already built its
    behaviour, propose an UpdatePlan change for the user to approve;
  - GREEN and REFACTOR name the last run recorded *in that phase* (`TestRunRecord.phase`), or say
    "no RunTests run since REFACTOR started". Naming the passing run that ended GREEN let an agent
    read REFACTOR as met; it declared REFACTOR done three times without running RunTests;
  - every refusal ends with upstream's own escalation rule (`ASK_THE_USER`, Doing tasks: "only when
    you're genuinely stuck after investigation").

- **The verification agent asks through the user's terminal** (user decision): it carries upstream's
  internal `bubble` permission mode (`runAgent.ts`: "bubble mode: always show prompts (bubbles to
  parent terminal)").
  - A background verifier used to be auto-denied every command no rule allowed, so it could not
    start or probe the app it was checking.
  - `bubble` is accepted only from a built-in definition.
  - A `bubble` worker keeps the parent's `canUseTool` and does not avoid prompts.
  - The REPL shows one question at a time (`Repl._asking`, upstream's `toolUseConfirmQueue`), and
    the prompt names the asking agent.
  - Headless runs still deny.

- **The verification agent is opt-in, as in public Claude Code** (user decision).
  - Upstream registers it, its contract and TodoWrite's verification nudge only behind the
    `VERIFICATION_AGENT` build flag and GrowthBook's `tengu_hive_evidence`. That flag defaults to
    false outside Anthropic ("verification agent is ant-only A/B", `builtInAgents.ts`,
    `constants/prompts.ts`), so it is neither standard nor periodic: when on, the model is asked to
    spawn it after non-trivial work.
  - Here one switch, `TDDAGENTS_VERIFICATION_AGENT` (`is_verification_agent_enabled`), governs the
    built-in agent, the session guidance's contract and the coordinator prompt's verifier lines.
  - A user's or project's own `verification` agent still loads.
  - The nudge is not ported.
  - The gpt-5.4 CPF live run (23/23 items, real dependencies, 100% coverage, the app answering
    correctly on a real server) skipped the verifier although the contract was on, and its flake8
    config failed its own lint.

- **Bundled skill reference files are read without a prompt, as upstream** (user decision,
  2026-10-29).
  - The motivating case: the CPF live run asked the user twice to approve reading TDDAgents' own
    `app/prompts/skills/*/references/*.md`.
  - Upstream's `registerBundledSkill` extracts a skill's files on first invocation under
    `getBundledSkillsRoot()`, `<temp>/bundled-skills/<version>/<per-process nonce>`, and
    `checkReadableInternalPath` allows that subtree.
  - Ported as `get_tddagents_temp_dir` / `get_bundled_skills_root`
    (`app/utils/permissions/filesystem.py`, `TDDAGENTS_TMPDIR` or `/tmp`, `tddagents-<uid>`) and
    `app/skills/bundled_skills.py`, which copies everything beside `SKILL.md`. Writes are
    owner-only, `O_EXCL | O_NOFOLLOW`, with traversal refused; extraction happens once per
    process, and a failure leaves the skill without its base directory.
  - A bundled `SkillCommand.skill_root` is the extraction dir.
  - The test suite gets its own temp root (`tests/conftest.py`).

*Divergences, recorded:*
- upstream's verification agent has no permission mode (it uses `bubble` for forks);
- RunTests has no upstream counterpart (upstream runs tests through Bash). Its table marks exit 1
  informational and exit 2 with collection errors as RED evidence, which are TDDAgents' rules;
- the Doing-tasks items are not in upstream's external build;
- the session guidance has no `! <command>` item (no `!` prefix here) and no DiscoverSkills item.

### Claude Sonnet 5.5 is the default model (2026-11-10)

At the user's direction `Config.MODEL` is now **`claude-sonnet-5-5`** (Haiku 5.5 before, below); the
small fast model stays Haiku 4.5. Nothing else in the request changes:

- **Thinking.** Thinking off sends no `thinking` parameter (`anthropic_thinking_param`), and an omitted
  parameter runs adaptive on Sonnet 5.5, as it did on Haiku 5.5. The `{"type": "disabled"}` that Sonnet 5.5
  rejects is never sent.
- **Temperature.** The `temperature` sent while thinking is off is `Config.TEMPERATURE = 1.0`, the API
  default, which Sonnet 5.5 accepts.
- **Output tokens.** `max_tokens` falls to upstream's default branch of `get_model_max_output_tokens`,
  as Haiku 5.5's did.
- **Price.** `COST_SONNET_55` prices it at $2 / $10 per Mtok with Anthropic's own cache-read price of
  $0.10 (not upstream's 0.1x) and upstream's 1.25x cache write. It is canonicalised before `claude-sonnet-5`
  so it is not priced as Sonnet 5.

### Claude Haiku 5.5 is the default model (2026-10-29)

At the user's direction the run's model is **Claude Haiku 5.5** (`Config.CHAT_MODEL = "anthropic"`,
`Config.MODEL = "claude-haiku-5-5"`), and the small fast model is **Claude Haiku 4.5**. The
first choice, Claude Sonnet 5, was replaced the same day by the cheaper Haiku 5.5. A headless
`is_even` run on Sonnet 5 completed 4/4 items with full cycles (about $0.46, 95% cache reads). The key is
`ANTHROPIC_API_KEY` in `.env`. The switch exposed two gaps in the Anthropic path, both closed the
way upstream does it:

- **`max_tokens` is upstream's** (`services/api/claude.ts`: `retryContext?.maxTokensOverride ||
  getMaxOutputTokensForModel(model)`).
  - Nothing sent it, so LangChain capped Sonnet 5 at its own 4096 for a model it does not know.
  - `get_chat_model` now gives every Anthropic client `get_max_output_tokens_for_model` unless the
    caller passes one.
  - `open_model_stream` passes the retry layer's `max_tokens_override`, which `with_retry`
    computed after a context overflow and nothing then read.
  - OpenAI requests are unchanged.
- **The small fast model is upstream's default Haiku** (`getSmallFastModel` →
  `getDefaultHaikuModel`, `app/utils/model/model.py::get_default_haiku_model`,
  `TDDAGENTS_DEFAULT_HAIKU_MODEL` overrides) on a Claude run.
  - A run on another provider still falls back to its own model, so it never reaches Anthropic.
  - `TDDAGENTS_SMALL_FAST_MODEL` wins either way.
- `claude-sonnet-5` and `claude-haiku-5-5` are priced beside upstream's table, which predates
  them: `COST_SONNET_5` ($2 / $10 per Mtok) and `COST_HAIKU_55` ($0.10 / $0.50), from Anthropic's
  model table (2026-10-06), with the cache prices at upstream's 1.25x / 0.1x. Haiku 5.5's
  price for a prompt over 100K tokens ($0.50 / $2.50) is not modelled.
- **Anthropic's list content is framed by index** (`split_chunk_into_blocks`). The first Sonnet 5
  run failed its second model call with a 400: the streamed `input_json_delta` fragments, which
  the framer (written for OpenAI's string content) took for text, were sent back as content.
  - The entries at a tool call's index are dropped, since `tool_call_chunks` carry the call.
  - Text and thinking entries stay.
  - A metadata-only frame merges with the open block's empty shape, so a tool block's `""` no
    longer becomes `[""]`.

*Limits, recorded:*
- the context window stays upstream's external default (200k without `[1m]`), so autocompaction
  fires earlier than the model's 1M window requires;
- `Config` still requires `OPENAI_API_KEY` at import;
- `DEFAULT_MODEL_ALIAS` (unused) still names `gpt-5-mini`.

### Compaction shares the main thread's request, as upstream (2026-10-29)

The live CPF run on Claude Haiku 5.5 (30/30 items, final run passing at 100% coverage) never
compacted. Haiku 5.5 binds each thinking block to its conversation: the system prompt, the tools
and the earlier messages. Our summarizer sent the history under its own system prompt with no
tools, and the API refused it ("Invalid `signature` in `thinking` block. The block is bound to a
different conversation"). Three failures tripped the circuit breaker. The run went on in Haiku
5.5's 1M window, with prompts up to 865K tokens, 153 of 177 calls above the 100K price tier.

At the user's direction the fix is upstream's own: `streamCompactSummary`'s **cache-sharing path**
(`tengu_compact_cache_prefix`, default true for third-party builds), which Phase 5 had recorded as
not ported.
- `build_forked_compact_summarizer` (`app/services/compact/summarizer.py`) is `runForkedAgent` with
  the main loop's `cacheSafeParams`: one main-loop model call (`stream_call_model`) over the
  messages the loop was about to send plus the summary request. The system prompt, tools, model
  and prefix are the main thread's, so thinking blocks stay valid and the prompt cache is shared.
  Its tool calls are never run (`maxTurns: 1`, `createCompactCanUseTool` refusing them), and only
  its text is the summary.
- `compact_conversation` tries it first on the raw messages (`forkContextMessages`). An exception,
  no text or an API-error reply falls back to the regular summary request (`build_compact_summarizer`)
  on the prepared messages, as upstream does after logging. A prompt-too-long retry threads the
  truncated set through both.
- Both production compactions (`production_autocompact`, `production_compact`) pass it.

*Limit, recorded:* the regular fallback still sends thinking blocks under its own prompt, as
upstream's does; on a model that binds them it fails too, so a failed fork still means no summary.

### The user reviews the specification and the plan (2026-10-29)

The engineer and planner prompts carried TDDAgents-written scope rules ("no documentation, README,
CI, packaging … unless the requirements ask", "the gold standard of the Python ecosystem", "The
first step … MUST ALWAYS be environment setup", sample items). They misfired both ways: a
2026-10-28 plan added a README and CI the spec excluded, and the 2026-10-29 Haiku 5.5 CPF plan
dropped the Dockerfile, docker-compose, CI and README the spec asked for. Upstream steers planning
differently: its Plan agent (`tools/AgentTool/built-in/planAgent.ts`) has a role line, a process
and a required output, and no scope rule, and the user holds scope at the plan approval
(`ExitPlanModeV2Tool`: approve, "No, keep planning" with feedback, or edit the plan file in the
editor). At the user's direction TDDAgents does the same:

- **Prompts** (`app/prompts/session/{engineer,planner}/`) have `planAgent.ts`'s shape:
  - a role line, `## Your Process` and `## Required Output`;
  - the planner adds `## How TDDAgents runs the plan`, which states facts, not commands: one item
    is one Red→Green→Refactor cycle RunTests observes, RunTests reports uncollected test files
    (pytest's default discovery), and the dependencies come from Core Dependencies;
  - every scope rule and sample item is gone, and no generic scope line replaces them (the Plan
    agent has none). `tests/test_doing_tasks_prompt.py` pins their absence.
- **Two reviews join the shell graph** (`app/session/review.py`), each its own node, as the
  analyst's is:
  - `engineer_review` and `planner_review` interrupt with the specification or the numbered plan.
  - `/yes` approves.
  - `/edit` opens the text in the user's editor: `app/utils/prompt_editor.py` ports
    `editFileInEditor`, `getExternalEditor` and `EDITOR_OVERRIDES`. The draft lives in
    `get_tddagents_temp_dir()` and is deleted afterwards; the plan is edited one item per line.
  - For the plan, `/plan add|edit|remove|move` reuses `apply_plan_command`, and a command that
    cannot apply is shown with the plan.
  - Any other text is feedback. The engineer or planner writes its output again from its previous
    output and the feedback (`feedback.md`, worded after upstream's "To tell you how to proceed,
    the user said:").
  - A headless session approves both, as it approves the checklist.
  - The terminal side is `review_text` and `read_review_answer` (`app/session/driver.py`), shared
    by `app/main.py` and the REPL's `/requirements`.
- **Fixed on the way:** the engineer seam returned `str(response.content)`. On Claude the content
  is a list holding thinking blocks, so the CPF run's stored specification was a Python list's
  `repr`, thinking signature included. It now returns LangChain's `.text`.

*Divergences, recorded:*
- `/yes` and `/edit` are typed commands, not a selection menu;
- the review is between the session shell's agents, not a tool call;
- feedback re-runs the agent instead of continuing a conversation;
- `getExternalEditor` is not memoized and has no Windows fallback.

### The Analyst is upstream's Plan Mode (2026-10-30)

The analyst, engineer and planner were three model calls in a LangGraph shell, each with its own
review. At the user's direction one Analyst now owns planning end to end, and it is Claude Code's
Plan Mode, ported into the loop: the main thread in permission mode `plan`, with a plan file and
`EnterPlanMode` / `ExitPlanMode`. The same conversation then develops the approved plan. This
supersedes the Phase 9 shell notes and "The user reviews the specification and the plan" above.
Decisions are recorded in `~/.claude/plans/make-the-fixes-i-imperative-wreath.md`.

- **The plan file** (`app/utils/plans.py`, `plans.ts`):
  - `<plans dir>/<slug>.md`, workers' `<slug>-agent-<id>.md`;
  - the directory is the `plansDirectory` setting when it stays inside the project, else
    `<config home>/plans`;
  - the slug is `generateWordSlug` (`app/utils/words.py`, upstream's lists verbatim), saved in the
    `plan-slug` sidecar beside `tdd-plan.json` so a resumed session reuses it;
  - `ToolContext.session_plans` carries it, and workers inherit it.
- **The mode** (`permission_setup.py`): `ToolPermissionContext.pre_plan_mode`,
  `prepare_context_for_plan_mode`, `transition_permission_mode`, and `handle_plan_mode_transition`'s
  `AppState` flags (`has_exited_plan_mode`, `needs_plan_mode_exit_attachment`). `set_permission_mode`
  is the one way a mode changes.
- **The gate:** the session plan file is writable and readable in every mode (step 1.5,
  `checkEditableInternalPath` / `checkReadableInternalPath`), after the deny rules. The TDD phase
  tier exempts it, as it exempts agent memory. Every other write in `plan` takes upstream's default
  path: an ask, denied headless.
- **The tools:** `EnterPlanMode` and `ExitPlanMode` are upstream's, main thread only, with their
  external prompts verbatim.
  - `ExitPlanMode` always asks "Exit plan mode?" and needs a person.
  - Its call reads the plan (an edited `input["plan"]` is written back and labelled "(edited by
    user)"), restores `prePlanMode`, and returns upstream's approved-plan text.
  - *TDD addition:* the plan's `## Implementation Plan` numbered items (`parse_implementation_plan`:
    any heading level, wrapped and nested lines join their item, a sub-heading closes it) become the
    sub-requirements through `update_tdd_plan(replace_pending)`, so done items stay done and the
    ledger restarts in RED. The result ends with the development prompt. A plan without such items
    fails `validate_input`, so the model keeps planning.
- **The approval** (`approve_plan`, `exit_plan_mode_options`): upstream's keep-context choices.
  "Yes, auto-accept edits" (or "Yes, and bypass permissions" when bypass is available) and "Yes,
  manually approve edits" set the mode; "No, keep planning" with feedback reaches the model, and a
  plain No ends the turn. `/edit` opens the plan in the editor and asks again.
- **Attachments:** `plan_mode` (full on the first turn and every fifth reminder, sparse in between,
  every 5 human turns; the sub-agent text for a worker), `plan_mode_reentry` and `plan_mode_exit`,
  with `messages.ts`' texts verbatim. Both workflows are ported: the 5-phase default and the
  interview workflow (`TDDAGENTS_PLAN_MODE_INTERVIEW_PHASE`). `TDDAGENTS_PLAN_V2_AGENT_COUNT` /
  `..._EXPLORE_AGENT_COUNT` keep upstream's defaults (1 / 3). After a compaction the plan file
  (`plan_file_reference`) and, in plan mode, the full reminder are re-attached
  (`createPlanAttachmentIfNeeded`, `createPlanModeAttachmentIfNeeded`).
- **The barriers while planning:** in mode `plan` the stop hooks skip the plan advance and the TDD
  hook, so a planning turn ends as upstream's does. The TDD contract states how a plan runs (the
  Implementation Plan items, one observed cycle each, RunTests' collection report). The pool is
  cached per (phase, plan mode); in coordinator mode the Analyst keeps the whole roster, since the
  coordinator filter would leave it neither the read-only tools nor ExitPlanMode.
- **The session** (`app/cli/print.py`, `app/session/driver.py`, `app/main.py`):
  - a session whose plan has no items opens in `plan`, its configured mode kept as `prePlanMode`;
    the request is the first prompt;
  - resume is upstream's: the transcript, the `tdd-plan.json` sidecar and the plan slug. A session
    with nothing new to say is loaded and waits for the user (`restore_session`);
  - `/requirements <request>` is upstream's `/plan <description>`: plan mode, then the request;
  - **headless follows `-p`:** `ExitPlanMode` is denied, so the session plans, ends unplanned, and
    exits 1;
  - the reports are `approved_plan.md` and `planning_dialogue.txt` (the planning phase's user and
    assistant text, from the transcript).
- **Removed:** the LangGraph shell (`app/session/{shell,analyst,engineer,planning,review,state,
  checkpointer,llm}.py`, `app/session/agents/`), the analyst/engineer/planner prompts, Postgres
  (`Config.POSTGRES_URL`, `Gates.postgres_checkpointing`, `docker-compose.yaml`, `--checkpoint`),
  and the `langchain`, `langgraph*`, `ormsgpack` and `psycopg*` pins. `run_sync` moved to
  `app/utils/run_sync.py`. `AskUserQuestion`'s prompt carries upstream's plan-mode note again,
  before the TDD plan note.

*Divergences, recorded:*
- no teammates, team-lead approval, `allowedPrompts` rules, Ultraplan or clear-context options;
- the approval is typed (`/edit`, an option number), not a selection dialog;
- the `Implementation Plan` parse is TDDAgents', as is refusing a plan without items;
- `EnterPlanMode` has no KAIROS-channels gate;
- the plan-mode text names our tools (`ReadFile`, `WriteFile`) and `getReadOnlyToolNames` is fixed
  to `ReadFile, Glob, Grep` (no project `allowedTools`).

### Live-run fixes and the upstream ports (2026-11-01)

The md→pdf FastAPI live run (thread `tdd-e88f2d3314364346`, 2026-10-31) finished its 9 items but
surfaced four problems. Each was traced to claude-code v2.1.88 and fixed its way, and the user
asked for a set of upstream files to be ported alongside. Plan:
`~/.claude/plans/plan-this-thorugly-zany-sloth.md`; branch `feat/live-run-fixes`, one commit per part.

- **`cd X && nohup … &` hung Bash for its whole timeout** (part 1). `LocalWorkspace.execute` used
  `subprocess.run(capture_output=True)`, which waits for pipe EOF that a backgrounded grandchild
  never sends. Upstream's exec model is ported:
  - `app/utils/shell/shell.py` (`Shell.ts` `exec`) and `app/utils/shell_command.py`
    (`ShellCommand`, `ExecResult`, the timeout path, exit codes 137/143/144/145): output goes to
    `<task dir>/<id>.output` and completion is the shell's **exit**, never pipe EOF.
    `LocalWorkspace.execute` is rebuilt on the same file + wait model and kills the process group.
  - The workspace seam gains `spawn` (`LocalShellProcess`; `E2BShellProcess` over the adapter's
    `start_background`).
  - `run_in_background` and `LocalShellTask` (`app/tasks/local_shell_task/`): `local_bash` tasks
    addressable by `TaskOutput` / `TaskStop`, the `<task-notification>` summaries, the 45 s stall
    watchdog, a foreground command moved to the background on timeout (unless it starts with
    `sleep`), `PROGRESS_THRESHOLD_MS`, `MAX_TASK_OUTPUT_BYTES`. A worker's shells are killed when it
    ends, and the session's at teardown. `TDDAGENTS_DISABLE_BACKGROUND_TASKS` removes the parameter.
  - **Cwd tracking** (`app/bootstrap/state.py`, `app/utils/cwd.py`): `pwd -P >| <cwd file>` after
    each command, read back into the tracked cwd, which Bash, its path checks, the phase tier's
    redirect resolution and the file tools' `expand_path` now use. A cwd that leaves the project is
    reset with upstream's "Shell cwd was reset to …" (`TDDAGENTS_BASH_MAINTAIN_PROJECT_WORKING_DIR`).
    RunTests keeps the project root.
  - The Bash prompt gains upstream's background note and the two sleep items; the divergence in
    "Every static system section and the Bash prompt are upstream's" is gone.
- **`pkill -f "port 8765"` killed its own shell** (part 1). Nothing beyond upstream: the
  actions-with-care prompt, and stopping one's own processes by task id (`TaskStop`).
- **RED persisted after the last item** (part 2). The TDD phase tier now applies only while
  `tdd_plan.current is not None`: `tdd_phase_denial` returns `None` otherwise, the pools carry no
  phase rules, and `phase_on_spawn` does nothing. RunTests says the phase rules are off
  (`NO_CURRENT_ITEM_LEDGER`), and `/plan add` brings them back. RunTests is still the only ledger
  writer.
- **Approval prompts showed `2 >& 1` and doubled quotes** (part 3). Upstream builds the same text
  but never shows it. The REPL now renders upstream's dialogs (`app/components/permissions/`): the
  full command verbatim, the file diff for Edit/Write, `name(key: value)` plus " (MCP)" for the
  fallback, WebFetch's url, and `PermissionRuleExplanation` (nothing for subcommand results). Tools
  gain `user_facing_name` and `render_tool_use_message`. UpdatePlan keeps its diff.
- **Settings deny rules hide tools** (part 4): `filter_tools_by_deny_rules` (`tools.ts`) runs on
  the main thread's and every worker's pool; the cache keys include the deny rules.
- **Auto memory** (part 5, `app/memdir/`, all eight files): the memory prompt section, MEMORY.md as
  `AutoMem` (and `TeamMem`), the filesystem carve-outs and TDD exemption, ReadFile's freshness
  note, and the relevant-memories prefetch.
- **Prompt history** (part 6, `app/history.py`): `<config home>/history.jsonl`, the REPL's ↑ over
  the current project with this session's entries first (`TDDAGENTS_SKIP_PROMPT_HISTORY`).
- **The command registry** (part 7, `app/commands.py`): `get_commands` lists the skills, then the
  local built-ins, with `find_command`, `has_command`, `get_command` and `help_text`. A typed
  `/<skill> args` is a prompt command (`getMessagesForPromptSlashCommand`), and an unknown `/name`
  gets "Unknown skill: …".

*Divergences, recorded (user decisions 2026-10-31 / 11-01 unless noted):*
- **Auto memory is opt-in** (Part I5): on only with `TDDAGENTS_ENABLE_AUTO_MEMORY`,
  `TDDAGENTS_DISABLE_AUTO_MEMORY` defined falsy, or `autoMemoryEnabled: true`. The relevance
  search (`TDDAGENTS_MEMORY_RELEVANCE`, upstream `tengu_moth_copse`), the past-context search
  (`TDDAGENTS_MEMORY_SEARCH_PAST_CONTEXT`) and team memory (`TDDAGENTS_TEAM_MEMORY`, upstream
  `TEAMMEM` + `tengu_herring_clock`) sit behind env opt-ins in place of their remote flags.
- **Agent memory follows upstream's gate for every scope**: an agent gets its memory prompt only
  when auto memory is on, so by default the built-in agents (`memory: run`) get none. This
  supersedes "Run memory is per sub-requirement" (Phase 5) and the run-memory notes of "Agent
  memory reachable" where they assume memory is always on; the `run` and `session` scopes stay
  TDDAgents' additions.
- Not ported: the nine `context/*.tsx` providers (React/Ink UI), `state/selectors.ts`,
  `onChangeAppState` and the `AppState.tsx` hooks and live settings reload (only the `AppState`
  fields this work needed were added), history's pasted-content references,
  `getTimestampedHistory` and `removeLastFromHistory`, the destructive-command warning (behind the
  default-off `tengu_destructive_command_warning`), the sed-edit dialog, the `/permissions` and
  `/hooks` hints, Ctrl+B `backgroundAll`, KAIROS and the Monitor task kind, and the memdir
  KAIROS/Cowork/remote paths.
- E2B shells have no cwd tracking (the sandbox's cwd file is never read back); a shell's stdin is
  `/dev/null`; a history entry is appended at once under a file lock (upstream buffers and
  flushes); the relevant-memories prefetch is discarded, not aborted, when the turn ends early.
- The REPL runs its own local commands (`/plan`, `/pause`, …); the slash path that reaches the
  engine knows only the skills. A typed skill that is not user-invocable gets upstream's
  refusal with "TDDAgents" for "Claude".

#### Part 8f — `-p` as the SDK protocol (2026-11-01, branch `feat/live-run-fixes-8f`)

Every `-p` run goes through `StructuredIO` (`app/cli/structured_io.py`, `cli/structuredIO.ts`):
`run_headless` is `runHeadless` + `runHeadlessStreaming` (`HeadlessStreaming` in `app/cli/print.py`),
the wire is Anthropic's SDK JSON translated from LangChain whatever the provider
(`app/utils/sdk_messages.py`), and every control request upstream's external build answers is
handled. Merged subsystems: file history (`app/utils/file_history.py`, `--rewind-files`,
`rewind_files`) and context usage (`app/utils/analyze_context.py`, `/context`, `get_context_usage`).

*User decisions (2026-11-01):*
- stdout and the exit code are upstream's in **every** `-p` mode: the exit code is the last result's
  `is_error`, never the plan's completion; TDDAgents' banners and reports go to stderr.
- Under `--input-format stream-json` (or `--permission-prompt-tool stdio`) every 'ask', ExitPlanMode
  and AskUserQuestion included, goes to the host as `can_use_tool`; plain `-p` keeps the headless deny.
  **A host's (or a permission prompt tool's) changed input is re-checked against the rule-based gate,
  the TDD phase tier among it**, as a PermissionRequest hook's is.
- `session_id` is the thread id; `--resume`/`-r` aliases `--thread-id` (a bare `--resume` opens a
  picker in the REPL), `-c/--continue` takes the thread whose transcript changed last,
  `--session-id <uuid>` names a new thread, and `--fork-session` is upstream's: a new thread over the
  **same project folder** (the conversation re-recorded under the new id, the TDD plan sidecar copied,
  the plan file copied under a new slug); `LocalWorkspace.for_run` gives the fork its own `run_dir`.
- TDD state streams as extra `system` subtypes (`tdd_ledger`, `tdd_plan`, `tdd_transition`) and the
  result carries the loop's `terminal_reason`.
- `--max-turns`, `--max-budget-usd` and `--json-schema` are opt-in flags; nothing is bounded by
  default. `max_turns` is `run_loop`'s entry argument (`Terminal.MAX_TURNS`), the budget and the
  structured-output retry limit (`MAX_STRUCTURED_OUTPUT_RETRIES`, default 5) are `QueryEngine`'s.
- `--system-prompt` / `initialize.systemPrompt` replace upstream's default sections, but
  **`tdd-contract.md` stays after a custom prompt**; `--append-system-prompt` appends as upstream.
- Extended thinking is **off unless asked** (`--thinking`, `--max-thinking-tokens`,
  `MAX_THINKING_TOKENS`, `alwaysThinkingEnabled: true`, `set_max_thinking_tokens`); upstream defaults
  it on. On OpenAI reasoning models it maps to `reasoning_effort` (disabled → minimal, adaptive →
  medium, a budget < 4096 → low, < 16384 → medium, else high).
- `/context` and `get_context_usage` count with the provider's endpoint on Claude **and** OpenAI runs
  (`messages.count_tokens`, `responses.input_tokens.count`), the rough estimate otherwise; the context
  report labels bundled skills `bundled` and has no ant-only breakdowns.
- File history: checkpointing in `-p` follows `TDDAGENTS_ENABLE_SDK_FILE_CHECKPOINTING`, as upstream's
  SDK env; **a successful rewind restarts the ledger in RED and re-takes the test baseline** (only
  erases evidence; done items stay done); **synchronous workers' edits are checkpointed** (they keep
  the parent's updater; upstream stubs every worker).
- `claude_authenticate` / `claude_oauth_*` answer errors (no Anthropic account login is impersonated);
  `remote_control` enabling fails as upstream's bridge does without a bridge; `channel_enable` and
  `set_proactive` answer as the external build does.
- The official plugin marketplace: constants only (no startup auto-install, no CDN download).

*Ported alongside:* `Stream`, `controlMessageCompat`, `ndjsonSafeStringify`,
`streamJsonStdoutGuard`, `PermissionPromptToolResultSchema`, the `callback` hook type
(`registerHookCallbacks`, `initialize.hooks`) and `function` hooks (`sessionHooks.ts`),
`SyntheticOutputTool` (`StructuredOutput`) with `registerStructuredOutputEnforcement`, `--agents` /
`initialize.agents` (`parseAgentsFromJson`), `sessionTitle.ts`, `sideQuestion.ts`, `hookEvents.ts`
(started/response), `sdkEventQueue.ts` / `sdkProgress.ts` / `sessionState.ts`'s event,
`createCanUseToolWithPermissionPrompt`, `services/tokenEstimation.ts`' API count, `utils/thinking.ts`.

*Divergences, recorded:*
- The SDK translation happens at the print boundary (`sdk_messages_for_event`), since the REPL
  consumes the engine's internal events; `system/init` is emitted by the runner before each `ask`.
- Hook lifecycle events, the SDK event queue and the hook-callback registry are per run (the hook
  dispatcher, `AppState.sdk_events`), not per process. The periodic `hook_progress` event is not
  ported: a command hook's output is read when it exits.
- Signal attachments (`structured_output`, `max_turns_reached`) have no text and are dropped from
  every request (`normalizeMessagesForAPI`); `max_turns_reached` is never stored in the history.
- `apply_flag_settings` keeps the inline flag settings and applies `model` at once; other keys need
  the live settings reload, which is not ported (Part 9). `seed_read_state` fills only gaps in the read
  cache (`FileState` has no timestamp).
- The session title is parsed from the small fast model's text (no `json_schema` output format); a
  side question is one forked model call over the run's state, not `runForkedAgent`.
- A `--resume` without an id in `-p` is refused ("--resume requires a session ID when used with
  --print"); upstream's picker is replaced by a numbered list in the REPL.
- Not ported: `--sdk-url` / `RemoteIO`, the bridge, streamlined output, `auth_status`,
  `rate_limit_event`, `prompt_suggestion`, `files_persisted`, `--workload`, KAIROS / proactive / cron,
  the teammates' inbox.

*Merged 2026-11-02: MCP-server OAuth, the MCP control plane, elicitation, the auth tool, plugins
stages 1–2.*

- **MCP-server OAuth** (`app/services/mcp/auth.py`, the TS SDK's `auth()` in `sdk_auth.py`,
  `app/cli/mcp_auth_control.py`, `app/utils/secure_storage/`): `mcp_authenticate`,
  `mcp_oauth_callback_url`, `mcp_clear_auth`, `needs-auth` and its 15-minute cache.
  *User decisions:* dynamic client registration (a client metadata URL only through
  `TDDAGENTS_MCP_*` env); XAA, the macOS keychain and issuer stamps are left out.
  *Divergences:* no default client-metadata URL; the plaintext credential store on every platform;
  discovery uses its own fetch; env names are `TDDAGENTS_MCP_*`.
- **The MCP control plane** (`app/cli/session_mcp.py`, `app/services/mcp/sdk_control_transport.py`):
  upstream keeps the configured, SDK and dynamic servers in `runHeadlessStreaming`'s closure; here
  they are one `SessionMcp` on `DevelopmentRun.mcp`, which the rosters and the tool pool read at each
  refresh (`on_change` rebuilds them, upstream's `buildAllTools`). `mcp_reconnect` / `mcp_toggle`
  (config gate `getMcpConfigByName`, then every client list), `mcp_set_servers`
  (`applyMcpServerChanges`, serialized; `filterMcpServersByPolicy` with the run's own settings
  locations), `mcp_message`, `initialize.sdkMcpServers` (`setupSdkMcpClients` over
  `SdkControlClientTransport`), and `buildMcpServerStatuses` in upstream's shape (`serverInfo`,
  `scope`, the per-transport `config`, tools with annotations).
  *Divergences:* a failed host `mcp_message` answers the pending request with a JSON-RPC error (the
  Python `ClientSession` does not fail pending requests on a stream exception; upstream's `send()`
  rejects). Tool annotations carry no `openWorld` (the MCP tool has no `isOpenWorld`).
- **Elicitation** (`app/services/mcp/elicitation_handler.py`, `execute_elicitation_hooks` /
  `execute_elicitation_result_hooks` / `execute_notification_hooks` in `app/utils/hooks/hooks.py`):
  Elicitation hooks, then the host's `handleElicitation`, then the ElicitationResult hooks;
  `notifications/elicitation/complete` fires the Notification hook and streams
  `system/elicitation_complete`. *Divergence:* upstream registers the handlers on each connected
  client before every turn (`registerElicitationHandlers`, a tracking set); the Python SDK fixes the
  callback when a session is created, so every connection gets `SessionMcp.elicit` from the start and
  it reads the hooks and the host when an elicitation arrives (cancel while no host is attached). No
  abort signal reaches the hooks; a hook's content that does not validate as an `ElicitResult` is a
  hook error (the host is asked). SDK servers answer through their transport, as upstream.
- **The MCP auth tool** (`app/tools/mcp_auth_tool/`, `createMcpAuthTool`): offered for every
  `needs-auth` server; a completed OAuth flow reconnects through `McpConnectionManager.reconnect` /
  `apply_reconnect` (it holds the cwd, transport and elicitation settings), then `on_reconnect`
  rebuilds the pools. Not ported: `callMCPToolWithUrlElicitationRetry` (-32042).
- **Plugins stages 1–2** (`app/utils/plugins/`, `app/services/plugins/`, `app/plugins/`,
  `app/utils/dxt/`): schemas, marketplaces, installation state, the reconciler, the loader,
  `refreshActivePlugins`, the zip cache, MCPB bundles (`@anthropic-ai/mcpb` 1.2.0, manifest 0.3,
  ported from npm since it is not in the snapshot), background installs, startup checks,
  autoupdate, `--plugin-dir`. *User decisions:* the plugin package folder is **`.tddagents-plugin/`**
  (`PLUGIN_PACKAGE_DIR`); the official marketplace is constants only.
  *Divergences:* every loader takes the run's `SettingsLocations` (cached per locations), not global
  settings; YAML frontmatter is PyYAML (YAML 1.1); Zod's error reporting is approximated;
  `save_plugin_options` writes the user scope; a timed-out git/npm child is SIGKILLed; plugin
  settings are the lowest settings layer; autoupdate skips on `DISABLE_AUTOUPDATER` or
  `TDDAGENTS_DISABLE_NONESSENTIAL_TRAFFIC`; the headless zip cache is removed through `atexit`;
  telemetry is not ported.
- **Plugins stage 3** (`app/utils/plugins/load_plugin_{commands,agents,hooks}.py`,
  `mcp_plugin_integration.py`, `app/services/plugins/plugin_operations.py`,
  `app/cli/handlers/plugins.py`, `app/utils/background_housekeeping.py`): plugin commands and
  skills join the skill registry (`<plugin>:<namespace>:<name>`), plugin agents sit between the
  built-in agents and the user's (their `permissionMode`, `hooks` and `mcpServers` ignored, as
  upstream), plugin hooks register on the run's dispatcher (with `CLAUDE_PLUGIN_ROOT`,
  `CLAUDE_PLUGIN_DATA`, `CLAUDE_PLUGIN_OPTION_*`; skipped under `allowManagedHooksOnly`), plugin MCP
  servers (`plugin:<plugin>:<server>`, scope `dynamic`) merge below the user's. `pluginOperations`
  and `python -m app.main plugin …` (validate, list, install, uninstall, enable, disable, update,
  `marketplace add|list|remove|update`); `/reload-plugins` and the `reload_plugins` control request
  (the `--agents` agents kept); `-p`'s background plugin install (waited for under
  `TDDAGENTS_SYNC_PLUGIN_INSTALL`); the autoupdate once per session, its REPL notice
  (`usePluginAutoupdateNotification`, `app/hooks/notifs/`); `/context` counts the run's agents.
  *User decisions (2026-11-02):* a plugin agent's TDD `phase` is honoured (phase moves stay
  ledger-guarded); `plugin … --thread-id <id>` targets that session's project folder (the cwd
  otherwise, as upstream); hints name real commands ("/doctor" → `python -m app.main plugin list`,
  "/plugin" and "/plugins" → `/reload-plugins`); plugin content uses the `TDDAGENTS_*` skill
  placeholders only (`${CLAUDE_PLUGIN_ROOT}` kept). *Divergences:* plugin MCP servers are refreshed
  on the configured connection list for the REPL and `-p` alike (connections are not memoized, so
  upstream's headless `applyPluginMcpDiff` sharing does not apply); plugin hooks are per run
  (a generation counter for the load memo); plugin agents get TDDAgents' extra memory scopes; the
  CLI returns exit codes; plugin MCP config errors are logged; the REPL's background install
  refreshes through the run's `refresh_plugins`. Not ported: LSP (no LSP manager), output styles
  (none), the `/plugin` React dialog (the CLI covers it), plugin-hook hot reload, `--available`'s
  install counts, analytics.
- **The REPL dialogs** (`app/components/`, line-based: choices typed by number, Esc closes):
  - *`/mcp`* (`app/components/mcp/`): the server list by scope, a server's menu (reconnect,
    authenticate and clear auth for sse/http, enable/disable, tools), a tool's detail, and
    `/mcp enable|disable [name|all]` / `/mcp reconnect <name>` (an `enable` toggles whatever the
    state, as upstream; user decision). `SessionMcp.reconnect` closes the old connection first
    (`clearServerCache`), which the `mcp_reconnect` / `mcp_toggle` control requests now share.
  - *Elicitation* (`elicitation_dialog.py`, `app/utils/mcp/elicitation_validation.py`,
    `date_time_parser.py`): form fields one by one in schema order (an empty line keeps the value;
    an invalid value is asked again; Accept with a field missing asks it again), URL mode with the
    link; the REPL is `SessionMcp.ask_host`. zod's checks are rewritten by hand (messages checked
    against zod 3.25); no unset of a set value, no scroll window or locale date display.
  - *Trust* (`app/components/trust_dialog/`, `app/utils/config.py`): upstream's
    `checkHasTrustDialogAccepted` (session trust, the project key, every parent folder), shown before
    the session opens; plugin startup checks run only with the folder trusted. *User decisions:*
    trust is stored as upstream (the project's key, so each new session folder asks once); `-p`
    treats trust as implicit, as `claude -p` (this supersedes the earlier "headless untrusted").
  - *Texts:* where upstream points at Claude Code, the TDDAgents route is named (user decision):
    the empty `/mcp` list names the project's `.mcp.json` and the global config's `mcpServers`; with
    a failed server the list names the session's `metrics_and_logging/execution_logs.txt` (upstream's
    `claude --debug` hint); the docs and "Security guide" links are dropped (no TDDAgents page).
  - *`.mcp.json` approval* (`app/services/mcp_server_approval.py`, `app/interactive_helpers.py`,
    `app/components/mcp_server_{approval,multiselect}_dialog.py`; user decision 2026-11-02): right
    after the trust dialog, before the session connects anything, the REPL asks about each pending
    project server (one server: the single dialog; several: the multi-select, numbers typed, an
    empty line enabling all as upstream's all-selected start, Esc rejecting all). The answers go to
    `<project>/.tddagents/settings.local.json`. `getProjectMcpServerStatus`: rejected by
    `disabledMcpjsonServers`, approved by `enabledMcpjsonServers`, `enableAllProjectMcpServers`, a
    trusted source's `skipDangerousModePermissionPrompt`, or a non-interactive session (`-p`
    approves, as `claude -p`); the rest stay pending and do not connect. *Divergences:* the docs link
    is dropped; the settings-error gate sees only ignored settings files and MCP config errors (the
    settings are read leniently); project settings are always on (no `--setting-sources`); Ctrl-D
    exits at once.
  - *`headersHelper` trust* (user decision 2026-11-02): an interactive session runs a project or
    local server's `headersHelper` only once the folder is trusted (`getMcpHeadersFromHelper`'s
    check, `headers_helper_trusted`); untrusted, the transport opens without it (logged) and the
    connection keeps its full config. `-p` runs it as before.
  - Not ported: claude.ai connector menus, agent-only MCP servers, `McpParsingWarnings`.

#### Part 9 — TDDAgents works with every language (2026-11-03, branch `feat/language-generic`)

The CPF live run `tdd-b842b60803c04632` passed every barrier, but its CI failed in a fresh environment.
The model had installed `types-PyYAML` ad hoc into the session's `.venv` without declaring it. Two
texts pointed that way:
- the contract said dependencies "are installed by the plan";
- every RunTests run, and the final run, used that one persistent `.venv`.

The user's direction, "TDDAgents tem que trabalhar com todas as linguagens", made the whole TDD cycle
language-agnostic, the way claude-code is: it runs the project's own commands, ships no venv and no
allow rules, and takes language knowledge from skills (`skills/bundled/claudeApi.ts`).

*User decisions (2026-11-03):*

**The project declares its configuration**, in `.tddagents/project.json` (`app/tdd/project_config.py`).
- **Keys:**

  | Key | Meaning |
  |---|---|
  | `version` | the schema version |
  | `setup` | optional: builds the environment from the manifests |
  | `test` | the whole suite; writes a JUnit report to `$TDDAGENTS_JUNIT_REPORT` |
  | `junitReport` | optional path override |
  | `coverageReport` | `{format: cobertura \| lcov, path?}`; the path defaults to `$TDDAGENTS_COVERAGE_REPORT` |
  | `testFiles` | gitignore globs |
  | `testSupportFiles` | |
  | `manifestFiles` | manifests and lockfiles |
  | `exclude` | |

- **Where it comes from:** the Analyst writes it as a `## Project Configuration` fenced-json section of
  the plan.
  - `ExitPlanMode.validate_input` refuses a missing or invalid block. It lists every issue and names
    the `tdd-project-config` skill.
  - On approval it writes the file into the protected `.tddagents/` folder.
  - `open_session_run` loads it (a resume reads it back).
- **No built-in adapters per ecosystem.**
- **No declared configuration while an item is current:** fail closed. Every write is denied, and
  RunTests says the project declares no test command.

**RunTests runs the declared test command and reads one JUnit report** (`app/tdd/junit_report.py`,
`app/tdd/run_judgement.py`).
- Shell-generic exit semantics:
  - `0` is a pass only if the report agrees: more than 0 tests, and no failures, errors or skips;
  - `126`/`127`, `≥128` and a timeout are errors, not evidence;
  - any other non-zero is a failed run, so load and compile errors count for RED.
- It always runs the whole suite; `test_path` is gone.
- A test file the report does not name did not run. When the report names no files, that check is
  "unknown", and RunTests says so.
- **A report split across many files** (Surefire, Gradle, Minitest) is joined into one by the
  project's command. Following claude-code, the core reads one file.
- Coverage readers: cobertura and lcov only, no JaCoCo. The contract tells the model coverage is
  measured on every run, with `COVERAGE_RULE_OF_THUMB` as a rule of thumb, not a gate.

**The phase tier and the fingerprint read the declared globs.**
- Declared manifests and lockfiles are writable in every phase. claude-code treats them as ordinary
  files, and the first RED of a non-Python project needs one.
- `TDDAGENTS.md` stays production code in RED.

**No pre-built environment.**
- `app/workspace/pyenv.py` is deleted: the project's declared `setup` builds the environment, and the
  runner is a dev dependency in the project's own manifest.
- `LocalWorkspace` strips TDDAgents' own interpreter prefix and `VIRTUAL_ENV` from commands.
- The shipped `.tddagents/settings.json` loses every pytest/pip/python allow rule. claude-code ships
  none, and its plan-approved `allowedPrompts` is ant-only.

**The contract** (`tdd-contract.md`) is language-neutral:
- "Declare every dependency you install in the project's own manifest. The session ends by running the
  declared setup and test commands in a clean copy of the project, in an environment built from its
  manifests alone."
- The auto-mode classifier's environment and allow lines are neutral too.

**The final run** (`app/tdd/final_run.py`):
- It copies the project into `<thread>/final-run/project`, leaving out `.git`, `.tddagents/` (except
  `project.json`) and the declared excludes.
- It runs `setup` then `test` with a scrubbed environment, judges the result with the same `judge`,
  writes `final_test_run.txt` and deletes the copy.
- A dependency installed but not declared reads "NOT passing". The report shows the clean run only.

**`tdd-project-config` skill** (`app/tdd/project_config_skill.py`), a port of the `claude-api` pattern:
- It is a code-built bundled skill (`registerBundledSkill` + `getPromptForCommand`, in
  `app/skills/bundled_skills.py`).
- It takes the language from its argument, or detects it from the project's top-level files
  (`LANGUAGE_INDICATORS`).
- It injects that ecosystem's reference plus `shared.md`. References: python, javascript-typescript,
  go, rust, java, csharp, ruby, php.

**The TDD skills and agents** (`tdd-mutation-defense`, `tdd-refactor-clean`, `tdd-test-design`, the
refactorer catalogue, tester, developer) are worded without Python-only idioms, in claude-code's
prompt style.

**`/init`** is a port of `commands/init.ts` (`app/commands/init.py`).
- `app/commands.py` moved to `app/commands/commands.py`, following the convention for a file and
  directory that share a name.
- It ports both prompts: `OLD_INIT_PROMPT` by default, and `NEW_INIT_PROMPT` with `TDDAGENTS_NEW_INIT`.
- Built-in prompt commands are found after the skills, as upstream orders them. They work in the REPL,
  `-p` and stream-json, and are not listed to the model.
- `projectOnboardingState.ts` is ported: the REPL header shows "Tips for getting started" until
  `TDDAGENTS.md` exists or the seen count reaches 4.

**NEW_INIT and `update-config`:**
- The `update-config` bundled skill (`skills/bundled/updateConfig.ts`) is ported.
- NEW_INIT's hooks step calls it, verbatim.
- The REPL renders AskUserQuestion option `preview` boxes (the upstream dialog's preview), and sends a
  chosen option's preview back as `annotations`.
- NEW_INIT's official-plugin suggestions stay out: they come from a marketplace packaged in
  `.claude-plugin/`.

*Divergences, recorded:*

- **Prompt substitutions.** "Claude Code"/"Claude" → "TDDAgents", `CLAUDE.md`/`CLAUDE.local.md` →
  `TDDAGENTS.md`/`TDDAGENTS.local.md`, `.claude/` → `.tddagents/`. The `/init` prefix drops the
  `claude.ai/code` URL. In NEW_INIT:
  - the `Write|Edit` matcher → `WriteFile|Edit`;
  - the `/verify` example and the official-plugin lines are dropped;
  - "Browse official plugins with /plugin" → the `plugin list --available` CLI.
- **Onboarding.** The seen count goes up once per REPL session that shows the tips; upstream counts
  once per release.
- **`tdd-project-config`.**
  - `allowedTools` is empty, so a headless Analyst is not asked.
  - With no language detected it lists the ecosystems and includes only `shared.md`; upstream inlines
    every document.
  - The argument can name the language.
  - JavaScript and Rust were added to `LANGUAGE_INDICATORS`.
- **`update-config`.**
  - Tool names: `Write`/`Read` → `WriteFile`/`ReadFile`.
  - PostToolUse hooks get the tool result as text, so the `jq` examples read `.tool_input.file_path`.
  - The settings TDDAgents lacks are dropped: `env`, `model`, `agent`, `attribution`, and the
    Config-tool section.
  - The "prove the hook fires" step, the `--debug` step and the generated "Full Settings JSON Schema"
    are verbatim again, now that live settings, `--debug`, the schema and `/hooks` are ported (below),
    and so are its `/hooks` mentions.
- **Test-file classes.**
  - Declared test-support files count as test code for the tier and the fingerprint, so `conftest.py`
    and helpers can be written in RED.
  - The "did not run" check uses `testFiles` only.
  - A path outside the project is production code.
  - Coverage leaves out declared test, support and excluded files when the report has per-file data.
- **pytest exit codes.** Exit 2 (collection errors) and 5 (no tests) are now plain failed runs. Exit 2
  still counts for RED once test files changed.
- **Test fixture.** The fixture is `py_project`, because pytest reserves the `pytest_` prefix;
  `python_workspace` stays as an alias.
- **Final-run timeout.** The final run uses upstream's maximum Bash timeout for `test` as well as
  `setup`.
- **Stop hook, no configuration.** While an item is current and nothing is declared, the refusal in
  every phase is "TDD cycle incomplete: <PHASE>. <rule>: <NO_PROJECT_CONFIG>", the tier's own reason
  from one source, without the usual next steps (they are blocked while the tier is closed; user
  decisions).
- **Final-run copy.** Built from upstream's ripgrep listing (`rg --files --hidden`, the VCS folders of
  `hooks/fileSuggestions.ts` and `.tddagents` excluded, then the declared excludes), so every
  `.gitignore` / `.ignore` applies (user decision: follow claude-code). Divergences:
  `--no-require-git` (TDDAgents never runs `git init`, but a checkout honours `.gitignore`) and
  `--no-ignore-parent` (the project is the root of every lookup). No ripgrep → "clean final run
  unsupported".
- **Symbolic links in the final-run copy** (user decisions 2026-11-03). The listing follows links
  (`--files --follow --hidden`, upstream's order in `hooks/fileSuggestions.ts`), and `copy_project`
  recreates each link once as a link with its own target (`os.readlink`); files listed through a linked
  folder are not copied again. On exit 2 with output, `rip_grep` returns the partial listing, as
  upstream's `ripGrep` does. Divergences from a git checkout, recorded:
  - a link rg lists nothing through (an empty folder, or one whose files are all ignored or excluded) is
    left out;
  - broken and looping links are left out (user decision: rg lists nothing through them, and a broken
    link cannot make a test pass);
  - a link pointing outside the project keeps its exact target, so a relative one may be broken in the
    copy, as in a checkout elsewhere.
  - A link into a huge outside tree can make the listing hit rg's timeout, and the run reports "could not
    copy the project": upstream's behaviour, kept (user decision, no extra bound).

*Live settings, `--debug`, the settings schema (user decisions 2026-11-03, to restore `update-config`
verbatim):*

- **`--debug [filter]`** (`app/utils/debug.py`, `debug_filter.py`; `utils/debug.ts`): `-d/--debug`,
  `--debug-file`, hidden `-d2e/--debug-to-stderr`; the log is `<config home>/debug/<thread id>.txt`
  with a `latest` link; the hook code writes upstream's debug lines. Divergences: `--debug api,hooks`
  (space) also filters; TDDAgents' own warnings and errors reach the debug log.
- **Settings change detection** (`app/utils/settings/change_detector.py`, `apply_settings_change.py`;
  `changeDetector.ts`, `applySettingsChange`): a changed settings file runs the ConfigChange hooks, then
  reloads the permission rules and the hooks snapshot, in the REPL, `-p` and stream-json;
  `apply_flag_settings` uses the same path (the 8f "live reload not ported" divergence is closed).
  Dangerous allows are stripped again on every reload and the TDD tier still holds. Divergences: one
  watcher per session (500 ms mtime polling with upstream's constants, not chokidar), no MDM polling or
  settings cache reset, `transitionPlanAutoMode` not applied, stripping on reload for everyone (upstream:
  ant-only); upstream's quirk kept (an emptied inline flag rule list keeps the old rules until restart).
- **Settings JSON Schema** (`app/utils/settings/schema_builder.py`, `schema_output.py`;
  `utils/settings/types.ts` `SettingsSchema`): the 25 keys this build reads, upstream's descriptions with
  the product renames (`.claude-plugin/` → `.tddagents-plugin/`); a small helper reproduces Zod's JSON
  Schema shapes (not compared byte for byte with Zod). Settings files are validated against it like
  upstream (below).
- `app/constants/product.py` holds `CLI_COMMAND`; `PLUGIN_CLI` derives from it.

*`/hooks`, the stop-hook summary, settings validation (user decisions 2026-11-03):*

- **`/hooks`** (`app/commands/`, upstream's `HooksConfigMenu`): the REPL's read-only hooks menu;
  `update-config` and NEW_INIT point at it verbatim.
- **"Ran N stop hooks"** (upstream's stop-hook summary): the REPL shows the summary of the stop hooks
  that ran, as upstream's UI does.
- **Settings files are validated like upstream** (`app/utils/settings/validation.py`,
  `schema_validator.py`, `permission_validation.py`, `tool_validation_config.py`, `validation_tips.py`,
  `app/utils/treeify.py`): a file that fails the schema is ignored whole and its errors are collected;
  invalid permission rules are filtered; the interactive session shows the "Settings Error" dialog
  before it opens (choosing exit ends with code 1); the REPL writes "Found N settings issue(s)" when it
  opens and after each change (`app/hooks/notifs/use_settings_errors.py`); the Edit tool refuses an
  edit that would make a valid settings file invalid (`validateInputForSettingsFileEdit`, error code
  10). Inline flag settings are checked unfiltered, as upstream. Divergences: only the supported keys
  are checked (upstream keys this build doesn't read pass unchecked); an ignored file's errors go to the
  `--debug` log; the tips carry no documentation links; error ordering is plain string order and
  invalid values print in Python form; a valid-JSON non-object file is replaced by a permission write.
- **The strict edit check knows upstream's top-level keys** (`STRICT_SETTINGS_KEYS`, user decision
  2026-11-04): only real typos are refused. **`autoMode`** joins the schema with upstream's public-build
  shape; the ant-only `deny` alias is stripped before the classifier reads it.
- **`/doctor`** (`app/commands/doctor/doctor.py`, `app/screens/doctor.py`,
  `app/utils/doctor_diagnostic.py`; user decision): a local REPL command (not immediate,
  `DISABLE_DOCTOR_COMMAND` turns it off) with upstream's sections in order — diagnostics (running from
  source, install path, ripgrep, the managed `strictPluginOnlyCustomization` warning), invalid
  settings, MCP config diagnostics, capped or invalid output-length environment variables, agent parse
  errors, plugin errors, unreachable permission rules, context-usage warnings. Upstream's "/doctor"
  pointers are restored: the settings-issues notice, `/reload-plugins`' error line, and `/mcp` with no
  servers ("Please run /doctor if this is unexpected."). Not ported (no counterpart): updates, sandbox,
  keybinding warnings, version locks, package-manager and multiple-install checks. Divergences: "Path"
  is TDDAgents' own folder; the MCP section has no docs links; failed agent files come from a separate
  rescan; a failed ripgrep probe is remembered so `/doctor` can say "Not working".
- **Every upstream `SettingsSchema` key is declared** (`app/utils/settings/schema_builder.py`, user
  decision 2026-11-04): all 82 top-level keys with upstream's types and descriptions, the printed schema
  compared byte for byte with the real Zod output. A wrongly typed key TDDAgents never reads (`model`,
  `env`, ...) now makes the file invalid, as upstream; the keys stay unread. The descriptions keep
  upstream's values verbatim (the schemastore URL, `claude-cli://`, "Claude Pro/Max", `claude/channel`;
  user decision), with only the product renames. Ported with them: `SandboxSettingsSchema`,
  `permissions.disableAutoMode`, the `$schema` literal, Zod's `.int()` / min / max / `minItems`
  messages, `z.coerce.string()` for `env`, `effortLevel`'s `.catch`, `.url()`, and upstream's
  `apiKeyHelper`, `cleanupPeriodDays` and `env` tips. The Edit refusal prints Zod's output mode,
  `update-config` the input mode.
- **Build-gated keys** (user decision: accept the ones that exist): `xaaIdp`,
  `disableDeepLinkRegistration`, `voiceEnabled` and `defaultView`, whose code is in the snapshot, are
  declared; the rest stay undeclared (`GATED_OFF_SETTINGS_KEYS`), so the strict edit check refuses them.
- Divergences: `defaultMode`'s enum lists TDDAgents' modes in its own order; `enabledPlugins` has no
  `{}` branch; `cleanupPeriodDays` states its minimum (upstream's hidden `.nonnegative()`) in the
  validator only.
- **One version constant** (`VERSION = "0.1.0"` in `app/constants/product.py`, user decision), for
  upstream's `MACRO.VERSION`: it replaces the git sha and the "unknown" fallback in `/doctor`, the SDK
  init message's `claude_code_version`, the MCP `clientInfo` version, the User-Agent
  (`tddagents/<version>`, `app/utils/user_agent.py` ports `userAgent.ts`), WebFetch's User-Agent, the
  transcript stamp and the bundled-skills folder. **`-v` / `--version`** prints `0.1.0 (TDDAgents)`, as
  upstream's `.version(...)` (user decision).
- **`python -m app.main doctor`** (`run_doctor_command`, `app/cli/handlers/util.py`), upstream's
  `claude doctor`: the `/doctor` screen without a session, with the configured MCP servers connected
  (no approval prompt) and the plugins loaded, then exit 0. It diagnoses the directory it runs in, or with
  **`--thread-id <id>`** that session's project folder (user decision 2026-11-04, as the `plugin`
  subcommand; an unknown id exits 1). Divergence: it doesn't pass the plugins' agents to the screen.

*Live verification (2026-11-04, the language-generic code):*

- **TypeScript** (`tdd-d0ba0b49755944f9`, a CPF validator with vitest): the Analyst declared npm /
  `npx vitest run --coverage` / lcov / `tests/**/*.test.ts`; 6 items through the cycle on the declared
  command (222 tests, 100% coverage); the clean final run (`npm install` from the manifests) passed.
- **Python** (`tdd-a20dd790db6640a3`, the CPF API with gate, Docker and CI): uv / `uv run pytest` /
  cobertura; 11 items, 296 tests, 100% coverage; the agent itself rebuilt a clean copy from the
  manifests, ran the real gate and a `docker build`; the final run passed.
- **The original defect is closed**: a package installed into that project's `.venv` but not declared
  passes in place and reads "NOT passing" in TDDAgents' clean final run (`ImportError` at collection).
- Both Analysts left out the reporter option that names files (vitest `addFileAttribute`, pytest
  `junit_family=xunit1`), so the "uncollected test files" check read "unknown" — stated, as decided.

*Live md→pdf runs (2026-11-08, the same request in TypeScript and Python; Claude Code answered for the
user, the recommended option every time):*

- **Python** (`tdd-d3113aede6344616`, FastAPI + markdown + WeasyPrint): 8 items, 271 tests, 100%
  coverage; the agent's own end-to-end check (clean copy, server, `curl`, a real PDF read back with
  `pdftotext`) passed, and so did the clean final run. The Analyst planned no `.env`, only `.env.example`
  (the `.env` is gitignored), although the request asked for both.
- **TypeScript** (`tdd-fc1d1e6f87654344`, Fastify + markdown-it + Puppeteer): 12 items, 411 tests, 96.5%
  coverage, a real PDF served; the clean final run read **NOT passing, 1 test failed**: a scaffold test
  asserts that `.env` exists, and the project's own `.gitignore` (and its declared `exclude`) leave
  `.env` out of the copy, as of any fresh clone. The final run did its job. Both outcomes come from the
  same tension in the request (a `.env` file that good practice keeps out of version control); user
  decision: record it, change nothing in the generated projects.
- The final-run report gave only "1 failed": vitest's `--reporter=junit` replaces its console output, so
  the raw output named no test. **RunTests and the final run now name each failed or errored test**
  from the JUnit report, below its counts (`FailedCase`, `failed_case_lines`; user decision
  2026-11-08): `Failed test: <classname> > <name> (<file>): <first message line>`, verbatim, in document
  order, with no cap (the tool-result budget already bounds the output).
- npm 11 skips dependencies' install scripts it hasn't been told to allow (`allowScripts`), so
  Puppeteer's postinstall (the Chrome download) did not run in the clean copy; the tests still passed
  because the Chrome the agent had installed lives in the user cache, outside the project.
- The "uncollected test files" check read "unknown" in both runs again (no reporter named files).

*The second md→pdf run and its three fixes (2026-11-08, user decisions):*

- **Both runs passed the clean final run** (TypeScript `tdd-964ff74bd964444f`: 11 items, 365 tests, 99.8%;
  Python `tdd-a3337ae051744609`: 15 items, 322 tests, 100%). The TypeScript run stuck once, on three
  TDDAgents faults, fixed the way claude-code resolves each:
- **Test support is never expected in a report** (`TestFileRules.is_test_file`): a file both
  `testFiles` and `testSupportFiles` match is support. The skill's own contract says so
  (`references/shared.md`: "the subset of `testFiles` that never appears in a report") and its
  TypeScript reference declares `vitest.config.ts` in both lists, yet the gate expected it in the JUnit
  report, so GREEN (23/23) never counted. The suite's `TEST_PROJECT_CONFIG` now follows the contract.
- **An approved edit of `project.json` is read at once** (`app/tdd/project_config_watcher.py`):
  - the port of `utils/cronScheduler.ts`'s file watch: one tool-written, user-editable file, an
    idempotent reload, `FILE_STABILITY_MS = 300`;
  - an invalid or deleted file is no configuration, as an invalid settings file contributes nothing, so
    the tier and RunTests fail closed, their refusals naming the file's issues
    (`invalid_project_config_note`, `AppState.project_config_issues`);
  - new test patterns while a sub-requirement is current take RED's baseline again, the ledger kept
    (`apply_project_config`, also used by ExitPlanMode);
  - the Edit tool refuses an edit that makes a valid file invalid (`app/tdd/validate_project_config_edit.py`,
    the port of `validateInputForSettingsFileEdit`, error code 10); Write has no check, as upstream.
  - Divergences: no internal-write marking (cron's idempotent reload instead); an E2B workspace is not
    watched (no host file).
- **The user's queued input ends a blocked TDD turn** (`user_input_waiting` in `app/tdd/stop_hook.py`):
  upstream never drains the queue on `stop_hook_blocking` (`query.ts`); it ends the turn when its hook
  lets it, and `useQueueProcessor` runs the input next (`wait_for_background_work` here). So the TDD hook
  keeps blocking with no latch, except while the user's own input (not a `task-notification`, a worker's
  or a meta prompt) waits for the main thread. The first exception to "no latch", recorded as such.
  - Fidelity: the TDD block is a meta `Stop hook feedback:` user message, and `query.py` yields every
    blocking error before the summary, as `handleStopHooks` does (`stopHooks.ts`). A `-p` run that ends
    right after a block therefore reports that message as its last, as upstream's `findLast`.
  - TDDAgents text: the refusal adds that a plain-text question gets no answer while the cycle is
    incomplete (`PLAIN_TEXT_QUESTION`); the run's model had written "reply 1 or 2" four times.
- **Live check of the three fixes** (2026-11-08):
  - **TypeScript md→pdf** (`tdd-be39091580f049cd`): `vitest.config.ts` was declared in both lists again,
    and GREEN counted; 10 items, 283 tests, 98.2%, the clean final run passed.
  - **CPF validator, Python** (`tdd-81a286e96c044713`, `--debug`), 15 items, 419 tests, 100%, the clean
    final run passed without `.env`:
    - the declared bare `pytest` was missing, so the user asked for a project venv. The agent changed
      `setup`/`test` in `project.json` with an approved Edit; the debug log reads "changed during the
      session: applied", and the next RunTests ran `.venv/bin/pytest`;
    - an Edit adding an unknown key was refused ("validation failed after edit") and the file left as it
      was;
    - asked to stop and answer only "aguardando", the model was blocked once. A message typed during the
      second "aguardando" ended the turn ("the user's queued input ends the turn; the cycle stays
      incomplete") and became the next turn: one block where the second md→pdf run had four;
    - `.env` was created as its own sub-requirement, with tests that load the settings from a
      temporary copy of `.env.example`, none of them skipped.

*The sandbox, strict collection and agents that do what was asked (2026-11-08, user decisions):*

The autonomous CPF runs passed a "clean" final run that proved nothing: commands ran as `bash -l`
(the host's `.bashrc` put conda on `PATH`), `pip install` went into the global miniconda, Puppeteer's
Chrome came from `~/.cache`; agents dropped a requested `.env` and gitignored or excluded it; the
uncollected-files check read "unknown" and passed.

- **The sandbox** is upstream's, ported (`app/utils/sandbox/sandbox_adapter.py` ← `sandbox-adapter.ts`;
  `app/utils/sandbox/runtime/` ← `@anthropic-ai/sandbox-runtime` **0.0.46**, its Linux path: bwrap
  argv, mandatory denies, the HTTP and SOCKS5 proxies with the domain allowlist and the ask callback,
  the socat bridge, the vendored seccomp binaries for x64 and arm64). It covers the agent's Bash,
  every RunTests and the final run, ships on with a private HOME and writes only in the project and
  `/tmp/tddagents-<uid>`, and fails closed (`failIfUnavailable`). `autoAllowBashIfSandboxed` stays true
  (below the TDD tier); `/tmp` is as upstream (only TMPDIR writable). The network ask is the REPL's
  dialog (Yes / Yes for this host, saved as `WebFetch(domain:…)` / No), the SDK host headless, a
  closed `-p` denies, and the final run (no one to ask) denies. The process and socket modules are
  mutation seams.
- **Divergences:**
  - runtime 0.0.46 rather than the 0.0.42 v2.1.88 bundled: in 0.0.42 the `denyRead ["~"]` tmpfs makes a
    project under `~` read-only and drops its `.git/hooks` deny; 0.0.46 is the last release of the
    snapshot's time and adds the deferred mount-point cleanup RunTests and background Bash need;
  - the runtime re-allows reading its own seccomp helper's directory when a read deny covers it
    (upstream assumes it readable; TDDAgents' vendored copy lives under `~`);
  - a one-off bwrap probe at session open: a broken bwrap exits 1, which RunTests would read as a
    failed run, i.e. RED evidence;
  - macOS seatbelt and the global-npm seccomp fallback are not ported (Linux and WSL2 only);
  - one adapter and manager per run (the final run builds its own over the clean copy, with the
    project's settings copied in) instead of module state; `initialize` / `wrap` / `reset` are
    synchronous, the proxies have their own thread and loop;
  - TMPDIR unified on `/tmp/tddagents-<uid>` (upstream's runtime and adapter disagree on it);
  - the HTTP proxy forwards with `Connection: close`, so each request is filtered;
  - sandboxed commands run with a plain `bash -c` (no host login profile), unsandboxed keep `bash -l`;
  - RunTests, `listTests` and the final run ignore `excludedCommands` and `dangerouslyDisableSandbox`;
  - the Bash prompt's sandbox section is computed when the tools are built; the auto-allow and the
    git gate read their state through ContextVars; `customConfig` / `abortSignal` of `wrapWithSandbox`
    are not ported; product renames (`.claude/*` → `.tddagents/*`, `TDDAGENTS_HOST_*` proxy ports).
  - The suite stays unsandboxed (upstream's default is off): sessions in tests pass flag settings
    `{"sandbox":{"enabled":false}}`, no environment switch. Projects seeded before this change keep
    their old settings.
- **Strict collection** (`collection_gate.resolve_ran`): in GREEN, REFACTOR and the final run every
  declared test file must be named as run, by the JUnit report (a `file` attribute, or a `classname`
  or suite name equal to a project path) or by the project's new optional `listTests` command (the
  test files the runner collects, one per line; the user's "follow claude code": upstream embeds no
  per-language adapter, it runs the project's own commands). A report naming no files is not a pass;
  in RED it is stated and not held. The skill references declare `listTests` for Go, Rust, Java and .NET.
- **Gaps closed:**
  - (a) RED checks collection on every run with a report;
  - (b) a file that ran but `testFiles` does not cover blocks every phase;
  - (c) the test floor, per test file and carried across items: fewer tests than the floor is not a pass
    in GREEN; REFACTOR accepts a decrease only with changed test files; RED lowers it only for the files
    the item changed (a run whose report names no files never moves it, so runners without file names
    have no floor);
  - (d) `exclude` accepts directories only (`…/`) whose literal prefix matches no test pattern
    (ExitPlanMode, the Edit check and the watcher).
- **`.env` and `.gitignore`:** the final run's copy keeps upstream's listing (every `.gitignore`
  applies). The contract now says to create every file the request names and never to add
  `.gitignore` or `exclude` entries on the agent's own (the user's decision); the skill defines
  `exclude` as generated directories only and describes the sandbox (dependencies and tool caches
  inside the project: `PUPPETEER_CACHE_DIR`, the system Python's `venv` module). Upstream texts stay
  verbatim.
- **Host prerequisites:** `sudo apt install bubblewrap socat python3.14-venv`.

*Live check in the sandbox (2026-11-09, the CPF prompt, TDDAgents alone, Claude Code only answering):*

- With `socat` missing, `-p` refused to open ("sandbox required but unavailable … socat not installed",
  exit 1). With it installed, the real-bwrap tests ran for the first time; the proxy test connected
  directly because `NO_PROXY` names the loopback addresses (as upstream sets it) and curl honours it even
  with `-x`, so it now passes `--noproxy ''` (the proxy itself allowed, denied with 403, and blocked
  unlisted hosts as designed).
- **Python** (`tdd-d4186a28deb749e8`): the plan declared a venv inside the project (the sandbox hides the
  user's miniconda); strict collection refused GREEN while pytest's xunit2 report named no files, and the
  agent declared `listTests` (`pytest --collect-only`) through an approved `project.json` edit, read live.
  7 items, 288 tests, 100%; the final run passed in the sandbox (a fresh `.venv` in the copy).
- **RED and load errors (user decision 2026-11-09):** gap (a) blocked the classic RED, a new test that
  imports code that does not exist yet: pytest stops collecting at the load error, so the files the item
  did not touch never ran. In RED only the test files the item created or changed must have run
  (`judge(red_scope=…)`, `item_test_files` from `tdd_item_test_hashes`); GREEN and REFACTOR still hold every
  declared file; the skill says `listTests` must list a file that fails to load.
- The Python agent again left out the requested `.env`, citing the contract's own `.gitignore` sentence;
  the contract now names `.env` explicitly and says version control is never a reason to leave a requested
  file out.
- **Eight autonomous runs (2026-11-10: CPF and MD→PDF, TypeScript and Python, outside and inside the
  sandbox):** seven passed the final run, every one with the requested `.env`. MD→PDF TypeScript in the
  sandbox read NOT passing, correctly: its `setup` did not install the Chrome its e2e tests need (outside
  the sandbox the user's `~/.cache` hid that). Two TDDAgents faults, fixed by user decision 2026-11-10:
  - a model that could only wait answered the stop hook's block with text 273 times (39.9 M tokens). The
    hook now ends the turn as `stop_hook_prevented` once the model has answered
    `TEXT_ONLY_STOPS_BEFORE_YIELD` (2) blocks in a row with text and no tool call
    (`blocked_text_only_stops`); the cycle stays incomplete and no evidence is created. The second
    exception to "no latch", and a TDDAgents ceiling upstream does not have;
  - the cause was a declared command that could not run, in RED, where the tier treated
    `.tddagents/project.json` as production. The file tools' writes to it are now judged by no phase,
    with or without a valid declaration; the protected folder still asks, the Edit check and the live
    reload apply.
- **Six CPF runs (2026-11-10: TypeScript, Python and Java/Spring Boot, inside then outside the sandbox),
  TDDAgents alone:** all six final runs passed, every one with `.env`. The Java agent fetched a JDK and
  Maven itself (none on the host). Two findings, fixed by user decision 2026-11-10:
  - **Java's "JUnit report: none was written" was TDDAgents' own recipe.** The skill's Maven/Gradle join
    (`cat …/TEST-*.xml | sed '/<?xml/d'`) deleted a class's closing tag with the next file's declaration,
    because the per-class reports end without a newline: any suite with two or more classes became
    invalid XML, read as no report. The recipes now strip only each declaration and end each file with a
    newline (a test runs them against a fake build); `junit_report_problem` makes RunTests, `judge` and the
    final run say "written, but not readable as a JUnit report (not valid XML: …)" instead of "none was
    written". The sandbox's write rules were not involved and match upstream.
  - **"MCP config errors for …/.mcp.json" printed over the REPL's prompts.** bwrap's empty mount points
    (as upstream's) made the settings-errors reader parse an empty `.mcp.json`; the port logged it with a
    stdlib `logger.error`, which `main.py`'s `basicConfig` handler printed. Upstream logs it with
    `logForDebugging(…, {level: 'error'})`. An audit of `app/`'s 283 stdlib warning/error calls found
    upstream sends almost all of them to the debug log (`logForDebugging`, or `logError`, which does not
    reach the terminal either), so `TerminalLogFilter` keeps TDDAgents' own warnings and errors off the
    terminal handler and `DebugLogHandler` carries them to the debug log; the MCP config line uses
    `log_for_debugging` with upstream's text. Kept visible (`extra=TERMINAL`), as upstream shows them: the
    SessionEnd hook failure at exit, startup settings warnings and notifications, `-p`'s "no messages"
    error, a Stop hook failure, and TDDAgents' own invalid `project.json` and E2B re-provisioning notices.
    The transient "Found 1 settings issue" notice during a sandboxed command stays, as upstream's (same
    race).
- bwrap's mount points (empty read-only `.mcp.json`, `.tddagents/settings.local.json`, `.bashrc`, …) exist
  in the project while a sandboxed command runs (a backgrounded `npm install` kept them through a
  RunTests); the cleanup removes them once no sandbox is active, as upstream. Meanwhile the empty `.mcp.json`
  read as "MCP config is not a valid JSON" ("1 settings issue"), as it would upstream; an empty settings
  file reads as `{}` in both.

### The three things that changed most

1. **Routing by `status` string literals was replaced** by a closed vocabulary of seven
   continue reasons and ten terminal reasons, ported from `src/query.ts`. No more router
   functions comparing strings.
2. **The TDD cycle is no longer enforced by graph topology.** It is a phase ledger plus
   phase-derived deny rules plus a Stop hook that refuses to let the loop terminate — three
   independent barriers, none reachable by a model decision. This is §3.3 of the plan and it
   is the part a thesis reviewer will press on. **Since Phase 12 the guarantee is not
   "observed failure first"**: it is that each sub-requirement's tests were written and run
   before any production code (RED), passed with the tests locked (GREEN), and passed again
   in a mandatory REFACTOR with line + branch coverage at the threshold.
3. **Prompts are composed Markdown, not templates**, following claude-code's own
   file conventions.

### Prompt conventions

Detailed in §4 of the plan. The short version, because it governs every prompt:

- **No templating engine.** Markdown in English, with `{{PLACEHOLDER}}` substitution. An
  unknown placeholder is left visible rather than rendered empty — the opposite of the Jinja2
  failure mode that retired the old templates (a misspelled kwarg rendered as an empty string).
- **Directory per unit**: `AGENT.md` / `SKILL.md` entry file plus `references/` loaded on
  demand, mirroring `reference/claude-code/src/skills/bundled/`. A tool's prompt is
  `prompt.md` beside the tool (`app/tools/<name>_tool/prompt.md`), as upstream's
  `tools/<Name>Tool/prompt.ts`.
- **No values in prose.** No model id, path, threshold or count is written into a prompt file.
  Each has one resolution point in code and reaches the prompt as a placeholder.
- **No invented defaults in frontmatter.** Omission means unset, not a number someone picked.
  `max_turns: 8` is exactly the anti-pattern: claude-code's `maxTurns` is `number | undefined`
  and falsy means *unbounded*. Where a ceiling is genuinely needed it is one named policy
  constant with a recorded rationale, not a literal copied into every definition.
- **Invalid frontmatter is logged and ignored, never silently defaulted** — with one
  deliberate exception: an invalid `phase` must fail the load loudly, because silently
  dropping it silently disables the TDD invariant.

## Quality gate (mandatory)

Every piece of code created or refactored in `app/` passes this gate before it is done.
Run the steps **in this order** — each one is cheap only because the previous one passed.

```bash
P=.venv/bin      # the repo-local, gitignored venv; see Commands

# 1. Unit tests — written alongside the change, never bolted on afterwards
$P/pytest tests/ -q

# 2. Lint — zero findings
$P/flake8 app tests

# 3. Types — zero errors (strict, all of app/ and tests/)
$P/mypy

# 4. Mutation testing — do the tests actually assert anything?
TMPDIR=<a dir on disk> $P/mutmut run --max-children 8    # NOT `python -m mutmut`; see the notes
$P/python scripts/mutmut_score.py --timeouts             # the timed-out mutants
$P/mutmut run --max-children 1 <those names>             # re-run each alone, on an idle host
$P/python scripts/mutmut_score.py --timeouts-reverified  # the gate score, and the triage list
```

**Step 1 — write the tests with the code.** A module that sits behind a protocol seam is
tested against a fake, never against a live LLM or sandbox. `tests/conftest.py` already
provides `FakeWorkspace` and the `make_fake_workspace` factory; reuse them instead of
writing new doubles.

**Steps 2 and 3 — the whole tree.** Since Phase 10, `app/` and `tests/` carry zero flake8
findings and mypy checks all of `app/` strictly (only the seam modules below are
`ignore_errors`). Keep it that way. Config lives in `setup.cfg` (flake8,
`max-line-length = 120`) and `pyproject.toml` (mypy `strict = true`, pytest, mutmut).

**Step 4 — the gate is ≥ 90% detected**, computed as `(killed + timeout) / total`, and a
timeout counts only once it is re-verified (since Phase 11, user decision; until then
timeouts were dropped from the denominator). mutmut times a mutant out by wall clock, so a
loaded host turns slow-but-passing runs into timeouts. Re-run every timeout alone on an idle
host: one that now passes is a survivor, one that fails is killed, and one that still hangs
is a confirmed hang, counted as detected. `scripts/mutmut_score.py` gives no score until
`--timeouts-reverified` says this was done. Then **triage every survivor**: kill it with a
test, or write down why it is equivalent or unreachable. A survivor left unexamined is the
one case this whole gate exists to catch.

**Watch the host during a run.** Report an overloaded host (load far above the cores,
available memory under ~4 GB) instead of reading its numbers, and lower `--max-children`.
The 12-core / 19 GB workstation reached 17 GB with 12 children; 8 is the agreed setting.

### Exempt: the seam modules

Three places genuinely cannot be unit tested offline, because they *are* the boundary to
an external service rather than something sitting behind one:

| Module | Boundary |
|---|---|
| `app/sandbox/adapter.py` | the E2B SDK |
| `app/utils/chat_model_factory.py` | the LLM provider |
| `app/screens/terminal_io.py` | the terminal (`prompt_toolkit` reading a real TTY) |

`app/utils/token_metrics.py` (the LangChain usage callback) and `app/config/` (environment
constants) are also outside the mutation set. All of them are listed in `[tool.mutmut]
do_not_mutate`, and every other module under `app/` is mutated (`paths_to_mutate = ["app/"]`).

Each names its exemption in its own docstring. In place of unit and mutation testing they
require **a static surface check** — assert every SDK method and kwarg they call still
exists on the installed package — **and an end-to-end pipeline run**. Everything layered
*above* them is fully testable and is not exempt: `app/workspace/e2b.py` delegates to the
adapter and is tested against a fake adapter, which is the pattern to copy.

### Recorded baseline

Measured against `app/workspace/`, `app/sync/`, `app/errors/`, `app/tools/` and
`app/hooks/`, 942 tests (historical: `app/tools/` and `app/hooks/` were deleted in Phase 4):

| | |
|---|---|
| Mutants | 2868 (6 timeout) |
| Killed | 2524 |
| Survived | 338 |
| **Mutation score** | **88.2%** (2524 / 2862) |
| flake8 / mypy | 0 findings, 0 errors (mypy `--strict`) |

**This is below the 90% bar, and the shortfall is recorded rather than rounded away.**
Every *behavioral* survivor found across five triage rounds has been killed. The 338 that
remain classify as: 93 log-call wordings, 63 case-flips of string literals inside messages
no contract reads, 17 codec aliases, 3 default-argument mutations (equivalent by
construction — see Gotchas), and the rest verified individually, several with
`MUTANT_UNDER_TEST=…` directly. Exactly 88 are the pre-existing
`workspace`/`sync`/`errors` survivors, unchanged, so Phase 1B regressed nothing.

The gap is concentrated where the code is most defensive. `app/hooks/` alone accounts for
109 survivors: both modules parse untrusted configuration and untrusted subprocess output,
so nearly every branch logs and continues, and each `logger.warning(msg, a, b)` generates
five or six mutants no assertion can reach without asserting on log text — which this
repository has always treated as an accepted equivalent class rather than something to
pin.

Triage was worth far more than the number suggests, and the number understates it because
two rounds *removed* mutants by deleting redundancy rather than by adding tests:

- `app/tools/base.py` went from 87 survivors to 4.
- 56 hand-copied `tool_name="X"` literals were deleted: `execute_tool` now stamps the name,
  so a tool physically cannot mislabel its own output. That removed 387 mutants and a real
  class of copy-paste bug.
- Mutation testing caught a **false-positive assertion in this suite itself** — see the
  Gotcha below — and a real defect in `WebSearch`, where skipping a malformed result left
  a hole in the numbering the model would then have to puzzle over.

The prior baseline, before Phase 1B, was 983 mutants / 895 killed / 88 survived / 91.0%.

**`app/loop/` (Parts A1–A7, B1–B8, and C1–C5) is measured under `paths_to_mutate = ["app/loop/"]` and clears the bar**, at 1311 tests:

| | |
|---|---|
| Mutants | 1707 (51 timeout) |
| Killed | 1528 |
| Survived | 128 |
| **Mutation score** | **92.27%** (1528 / 1656) |

All 128 survivors across `app/loop/` are triaged and documented:
- **3 in `app/loop/engine.py`**: in `run_loop`, flags initialized to `False` (`needs_follow_up`, `blocking_limit_raised`, `prompt_too_long_raised`) mutated to `None` — equivalent since both `False` and `None` evaluate as falsy in `if` checks.
- **2 in `app/loop/context.py`**: default argument mutations for empty sequences (`tools: tuple = ()`, `messages: tuple = ()`).
- **3 in `app/loop/tools/orchestration.py` (`partition_tool_calls`)**:
  - `mutmut_12`: `call.get("name") or "XXXX"` produces an unknown tool lookup, identical fail-closed behavior.
  - `mutmut_23`: `except Exception: is_safe = None` is equivalent to `False` in boolean conditions.
  - `mutmut_24`: `is_safe = True` on exception in safety predicate.
- **1 in `app/loop/tools/orchestration.py` (`apply_context_modifier`)**:
  - `mutmut_9`: `and` changed to `or` when checking `hasattr(..., "tools")`.
- **26 in `app/loop/tools/orchestration.py` (`run_tools`)**:
  - `mutmut_6, 9`: `getattr(context, "tools", None)` vs `()` — context always has `tools` attribute.
  - `mutmut_12, 16`: `assistant_message` fallback to `None` or `AIMessage(content="XXXX")` — test tools do not read message content.
  - `mutmut_23, 111, 112`: `is_generator_exit` flag mutated to `None/False`.
  - `mutmut_25, 86`: `if context.cancel.cancelled: break` → `return` — equivalent mutant: in an async generator both `break` and `return` execute the `finally` block to repair history via `yield_missing_tool_results`.
  - `mutmut_32, 41, 63, 77, 93`: `cid = str(call.get("id") or "XXXX")` — empty id fallback string.
  - `mutmut_43, 97`: `run_tool_use(call, None, ...)` — tools in tests do not read the assistant message.
  - `mutmut_121, 122`: `completed_tool_ids.add(None / str(None))` in `finally` loop — runs after yielding the missing message; `completed_tool_ids` is local and never read afterwards (equivalent mutant).
- **2 in `app/loop/tools/pool.py` (`assemble_tool_pool`)**:
  - `mutmut_18`: `getattr(tool, "is_mcp", None)` — equivalent to `False` in boolean condition.
  - `mutmut_24`: fallback default `True` when `is_mcp` attribute is absent.
- **6 in `app/loop/tools/execution.py` (`run_tool_use`)**:
  - `mutmut_14`: `tool_name = str(tool_call.get("name") or "XXXX")`.
  - `mutmut_57, 58, 83`: `validate_input(None, context)` / `check_permissions(tool_args, None)` — default callback stubs ignore args/context and return valid/allow.
  - `mutmut_120, 123`: `tool_use_id = None` in `ToolResult` construction.
- **10 in `app/loop/tools/base.py` (`build_tool`)**:
  - `mutmut_1`: `is_mcp: bool = True` (default argument mutation).
  - optional default assignments (`None`, empty schema, default mapper).
  - `(lambda args: None)` instead of `(lambda args: False)` (equivalent in boolean contexts), and `is None` vs `is not None` guards.
- **3 in `app/loop/permissions/types.py`**:
  - default parameter `is_bypass_available: bool = True` in `get_next_permission_mode`.
  - `elif mode == PermissionMode.BYPASS_PERMISSIONS` and `elif mode == PermissionMode.DONT_ASK` mutated to `!=`, where both branch and `else:` fallback return `PermissionMode.DEFAULT` (equivalent mutant).
- **4 in `app/loop/permissions/rules.py`**:
  - `isinstance(app_state.tool_permission_context, ToolPermissionContext)` type check mutation.
  - `tool_perm_res = res` mutated to `None` in exception/assignment path.
  - `getattr(tool, "requires_user_interaction", None)` fallback default.
  - `req = requires_interaction_fn()` mutated to `req = None`.
- **7 in `app/loop/permissions/filesystem.py`**:
  - default parameter in `is_path_allowed`: `operation: Literal["read", "write"] = "write"`.
  - `is_dangerous_path`: empty message return fallback `return False, ""`.
  - `is_path_in_working_dir`: `except Exception: return False`.
  - string mutation on `path_str = str(path)` and dictionary key variations in `decision_reason={"type": "mode", "mode": context.mode}`.
- **19 in `app/loop/permissions/gate.py`**:
  - string literal mutations inside informational `message` and `decision_reason` dictionaries (e.g. `{"type": "userInteraction"}` or `{"type": "other", "reason": "passthrough"}`).
  - fallback defaults and boolean short-circuits on non-contractual branches.
- **42 in `app/loop/permissions/capability.py`**:
  - parser state initializers in `split_shell_commands` (`in_single = False` / `in_double = False` mutated to `None`, evaluating identically in boolean checks).
  - `split_shell_commands` list appends and loop step increments.
  - duration pattern regex matching variations and command wrapper argument skipping fallbacks.

**`app/loop/streaming/` (Part F1–F6) is measured under `paths_to_mutate = ["app/loop/streaming/"]` and clears the bar**, at 54 tests:

| | |
|---|---|
| Mutants | 594 (43 timeout) |
| Killed | 508 |
| Survived | 43 |
| **Mutation score** | **92.76%** (551 / 594) |

All 43 survivors across `app/loop/streaming/` are triaged and documented:
- **14 in `app/loop/streaming/executor.py` (`add_tool`)**: dictionary fallback defaults (`call.get("id") or "XXXX"`, `call.get("name") or "XXXX"`), logger message mutations.
- **2 in `app/loop/streaming/executor.py` (`_process_queue`)**: debug logger mutations and boolean short-circuits.
- **21 in `app/loop/streaming/executor.py` (`_execute_tool`)**: string mutations in debug/error logging, fallback defaults on unexercised optional branches.
- **6 in `app/loop/streaming/executor.py` (`get_completed_results` / `get_remaining_results`)**: logger mutations and local tracking set operations with no external read.

**`app/workspace/` and `app/sync/` (Part G1–G4) are measured under `paths_to_mutate = ["app/workspace/", "app/sync/"]` and clear the bar**, at 158 tests:

| | |
|---|---|
| Mutants | 831 (0 timeout) |
| Killed | 757 |
| Survived | 74 |
| **Mutation score** | **91.09%** (757 / 831) |

All 74 survivors across `app/workspace/` and `app/sync/` are triaged and documented:
- **Default arguments in signatures (28)**: e.g. `LocalWorkspace.list_files(path=".", depth=1)`, `read_file(encoding="utf-8")`, `SyncEngine.flush(kind="sub_req_boundary", full=False)`, `_reconcile(ignore_baseline=False)`. Equivalent by construction: caller defaults are resolved on the public wrapper and passed positionally.
- **Equivalent path logic (2)**: `if normalized == ".": return "."` vs `return normalized` (since `normalized` is already `"."`).
- **Codec aliases (8)**: `"utf-8"` -> `"UTF-8"` or `None` on systems where UTF-8 is the default encoding.
- **Logging wording and telemetry strings (24)**: logger messages, debug strings, and decision reasons (e.g. `f"both sides changed; {winner} wins"`).
- **Fallback defaults and timing bounds (12)**: non-contractual timestamps (`taken_at=None` when ignoring baseline) and command duration timing boundaries.

**Phase 0 (local runner baseline) is measured under `paths_to_mutate` = runner, run_tests, model, state, engine, factory, session/iteration, session/export, workspace/pyenv, workspace/local, and clears the bar**, at 4482 tests:

| | |
|---|---|
| Mutants | 1417 (6 timeout) |
| Killed | 1293 |
| Survived | 118 |
| **Mutation score** | **91.64%** (1293 / 1411) |

Survivor triage: `runner.py` 34 (29 log wording/arguments; 3 pool `phase_ledger` mutants equivalent because in RED the pool keeps all seven built-ins — writer classification is per call, at the gate; 2 `ToolContext.messages` mutants equivalent because `initial_loop_state` carries the messages), `factory.py` 2 (`tracking` default), `model.py` 1 (`keep_recent_turns=2` equals the default), `workspace/local.py` 13 (codec aliases, unreachable `list_files` branches, default arguments), plus the pre-existing engine/iteration/run_tests log and equivalent classes. Two tests had to be made mutmut-safe: subprocess imports of `app.main` hit trampolines in the child (`test_app_main_imports_without_an_e2b_key` now imports only), and the single-ledger-writer AST check dedupes files because a mutated module holds one copy of each function per mutant. Removed as redundant rather than covered: two `mkdir(parents=True)` calls already implied by `copytree` and `venv`, and the runner's `tool_vars` dict, which no tool prompt consumed.

**Phases 1 and 2 (model call, retry, tool pipeline) are measured under `paths_to_mutate` =
`retry`, `streaming/blocks`, `tools/{validation,result_storage,hook_permissions,todo,execution,orchestration,pool}`,
`permissions/bash_tdd`, `engine`, and clear the bar**, at 4813 tests:

| | |
|---|---|
| Mutants | 1997 (4 timeout) |
| Killed | 1807 |
| Survived | 186 |
| **Mutation score** | **90.67%** (1807 / 1993) |

The first run scored **80.7%**, and the gap was mostly a defect in the *tests*, not missing
coverage. `app/loop/tools/todo.py` reported 40% because the tool was built at test-module
import: that runs during collection, so mutmut's stats pass attributed it to no test and the
mutants were never exercised — every one died immediately once the build moved into a helper.
Two more rounds then killed genuine gaps rather than wording: `retry.py`'s status/header
lookups were only ever exercised through one attribute shape, so the `status`/`http_status`
branches and the whole `is_connection_error` name list were untested; the context-overflow
floor was never probed at its boundary (`available == FLOOR` must retry, one token less must
raise); `result_storage`'s newline snap was never tested at exactly half the preview; and the
hook payloads were never asserted, so a tool could have reported the wrong name or dropped a
Bash command without any test noticing. `permissions/bash_tdd.py` reached **100%** by pinning
each refusal verbatim — defensible here because that message is the only feedback the model
gets about why a write was refused.

The 186 survivors are the documented equivalent classes: logger wording and arguments,
codec aliases (`"utf-8"` -> `"UTF-8"`/`None`), `False` -> `None` flag initializers,
`getattr(x, "f", )` default drops on attributes that always exist, case-flips of internal
dict keys no contract reads, and two verified-equivalent control-flow mutants —
`while attempt <= max_retries + 2` (the inner `attempt > max_retries` guard already enforces
the ceiling) and `has_more=None` (falsy exactly like `False`).

Two earlier rounds on this package are worth repeating as method. Four survivors were
removed by deleting a `typing.cast` whose string argument is a runtime no-op — the mutants
went away with the redundancy rather than being covered by a test. Three more were genuine
gaps the fakes were hiding: the loop passes `state` and `config` into both injected
dependencies, and nothing noticed when a mutant passed `None` instead, because a fake that
ignores its arguments cannot fail when they are wrong. A fourth found a real defect —
`getattr(message, "tool_calls")` without its default raises `AttributeError` on any message
type that has none, inside the streaming branch, which would abort the turn instead of
answering "not asking".

`app/errors/` was brought under the gate after the fact and is worth reading as a worked
example of why the gate exists. Both classifiers decide whether each infrastructure
failure is retried or aborts the run, and both had **zero tests**. Bringing them in
surfaced a live bug: the `EmptyInputError` / `EmptyChannelError` branch of
`handle_llm_exception` rendered the literal text `{exc}` in its message, because the
continuation line of an implicit string concatenation had lost its `f` prefix — the same
silent-interpolation failure class as the Jinja2 gotcha below. It also removed a
duplicated `__init__` from both `TDDWorkflowError` subclasses.

Survivors in every phase so far have been dominated by mutations with no behavioral
effect: codec aliases (`"utf-8"` → `"UTF-8"`), log-message wording, and case-flips of
string literals no contract depends on. Anything behavioral that survives a mutation run
must be either killed or written down — treat that as the standard to hold, and treat 90%
as the floor it usually implies rather than as a number to reach by any route.

**claude-code ports of 2026-10 (file tools, ripgrep, memory/context assembly, Bash
permissions) are measured per module and all clear the bar**:

| Module group | Mutants | Score |
|---|---|---|
| `tools/fs.py`, `tools/ripgrep.py`, `context/{memory,assembly,attachments,model}`, `permissions/{settings,shell_rules,rules,gate}` | 4273 | 97.3% |
| `permissions/bash_permissions.py` / `bash_path_validation.py` | 1812 | 92.5% / 96.3% |
| `permissions/bash_commands.py` / `bash_sed_validation.py` | 2178 | 93.2% / 95.9% |
| `permissions/bash_read_only.py` / `bash_read_only_commands.py` / `bash_security.py` / `tools/bash.py` | 3903 | 96.1% / 92.6% / 94.0% / 99.6% |

Remaining survivors are the documented equivalent classes (defaults, `False`→`None`,
codec aliases, case flips with no letters, unreachable branches upstream also has).
**Not yet re-triaged:** `tools/execution.py` (77.6%), `orchestration.py`, `pool.py`,
`context/compact.py`, `slicing.py` — 84.9% together, and currently being edited.

### Gotchas

- **`also_copy` must name every non-code file a test reads**, in *directory* form:
  `["app/", "requirements.txt", ".tddagents/", "docs/", "scripts/"]` (`scripts/` since
  `tests/test_mutmut_score.py` loads `scripts/mutmut_score.py`). mutmut copies with
  `shutil.copy2`, which does not create parent directories, so `.tddagents/settings.json`
  silently fails to copy; any missing file makes stats collection die with
  "failed to collect stats" and every mutant reports "not checked".
- **Never build the thing under test at test-module import.** A module-level
  `TOOL = build_todo_write_tool()` runs during *collection*, so mutmut's stats pass
  attributes it to no test, every mutant inside the builder is reported as surviving, and
  the suite that would have killed them is never run against them. `app/loop/tools/todo.py`
  went from 40% to green by moving one line into a helper. Symptom: a module with many
  survivors that all die when you set `MUTANT_UNDER_TEST=...` by hand.
- **Verify a survivor from inside `mutants/`, not from the snapshot root.** mutmut runs the
  suite with the mutated copy as the working directory; running it from the snapshot root
  imports the *unmutated* `app/`, so every mutant looks equivalent and the check is
  worthless.
- **Invoke mutmut as `mutmut run`, never `python -m mutmut`.** Under `-m` the module is
  `__main__`, so `mutmut.__main__` is absent from `sys.modules`; the mutation trampoline
  re-imports it in each forked child, re-runs `set_start_method('fork')`, and the whole
  run dies with `RuntimeError: context has already been set`.
- **`also_copy = ["app/"]` in `pyproject.toml` is load-bearing.** mutmut copies the
  project into `mutants/` and runs the suite there, but it walks only `paths_to_mutate`.
  Without `also_copy`, `app/config/` is never copied, every mutant dies of `ImportError`
  rather than of a real assertion, and the run reports a meaningless 100%.
- **`paths_to_mutate = ["app/"]` mutates every new module automatically** (Phase 10). An
  exemption is a `do_not_mutate` entry, and only a seam module (see above) may have one.
- **Default-argument mutations are equivalent by construction — do not chase them.**
  mutmut keeps the *original* signature on the public wrapper, resolves the defaults
  there, and passes every argument positionally into the mutant
  (`args = [tool, raw_args, ctx, call_index, call_id]`, visible in any generated
  `mutants/**/*.py`). A mutated default in the mutant's own signature is therefore never
  reached, and no test can kill it. Verified directly with `MUTANT_UNDER_TEST=…`; there
  are 3 such survivors today.
- **Watch for assertions that pass for the wrong reason.** A blocked-hook message embeds
  the hook's own command, so `assert "no pip installs" in reason` held even when the
  dispatcher dropped stderr entirely — the probe string was in the command. Mutation
  testing is what surfaced it. Where output and input can share text, make them differ:
  `tests/test_hook_dispatcher.py::script_hook` puts the hook body in a file so its command
  and its output have nothing in common.
- `tests/conftest.py` seeds `OPENAI_API_KEY` / `E2B_API_KEY` before any
  `app.*` import, because `config.py` raises at import time without them.
- **Live tests never run by accident.** Tests marked `@pytest.mark.live` call a real provider and
  run only with `RUN_LIVE_LLM_TESTS=1`; the key comes from the repo's gitignored `.env` via
  `tests/conftest.py::read_dotenv_key`. `config.py` calls `load_dotenv()`, so a key in `.env`
  would otherwise silently turn skip conditions into live calls.
- **The integration tests' venv is built once and reused** (`tests/conftest.py::shared_test_venv`,
  under `$TMPDIR`, behind a file lock). mutmut's per-mutant time budget counts only the
  tests' call phase, never fixture setup, and each mutant is a new pytest session. A venv
  built per session (2.1 s and 15 MB each) was therefore rebuilt per mutant, invisibly to
  the budget: 12 children running pip at once turned passing mutants into false timeouts.
  It is built with plain `venv` + `pip`, never through the mutated `ensure_session_python`,
  so no mutant can corrupt the shared copy.
- **Real-pytest integration tests use the `python_workspace` fixture**, a `LocalWorkspace` whose
  commands run in one session-scoped venv (`session_python`). A bare `LocalWorkspace` has no
  `python` with pytest on `PATH` on hosts without one, and `RunTests` then exits 127.

## What this is

TDDAgents is a research prototype (UFF / SBES 2026): a multi-agent system that turns a natural-language problem description into tested Python code by driving the Red–Green–Refactor phases of TDD (since Phase 12: robust tests, then code, then a mandatory coverage-checked refactor). Agent-generated code is written and executed in a **per-session project folder outside TDDAgents' own** (since Phase 13: `~/.tddagents/workspaces/<thread_id>/project`, `TDDAGENTS_WORKSPACES_DIR` overrides), a git working tree with its own Python virtualenv (`project/.venv`; pip, pytest and pytest-cov pinned), never inside TDDAgents' environment. The session's reports go beside it, in `<thread_id>/metrics_and_logging/`. *(Changed 2026-10-08 at the user's direction: it used to run only in the E2B sandbox. The runner takes a `Workspace`, so E2B can be bound again later; `E2B_API_KEY` is optional and checked only when a sandbox is requested.)* A session is resumed from its transcript and sidecars, as upstream's `--resume`.

It is **claude-code's loop**, one conversation per session: plan mode (the Analyst) writes and gets approval for the plan, then the same conversation develops it item by item. See [The architecture](#the-architecture--claude-codes-loop-laid-out-like-its-source).

`pytest`, `flake8`, `mypy` and `mutmut` apply to **`app/` itself**, not only to the generated workspaces. `tests/` holds an offline suite for `app/` that needs no API keys, no sandbox and no database, and runs in about a minute; `setup.cfg` and `pyproject.toml` configure the four tools. See [Quality gate](#quality-gate-mandatory) for the mandatory procedure and the current baseline.

*(This reverses what this file used to say. Until Phase 1A the repository had no tests of its own and verification was purely end-to-end. That is no longer true, and treating it as true is how untested code lands.)*

Coverage is deliberately partial. The suite covers the modules that can be exercised offline; the three seam modules that talk to E2B and to the LLM are exempt and are still verified by **running the pipeline end to end**, which remains the only way to check the system as a whole. There is no build step.

## Commands

```bash
# Setup
cp .env.example .env          # then fill ANTHROPIC_API_KEY, OPENAI_API_KEY (E2B_API_KEY optional)
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run the pipeline (interactive; must be run from the repo root)
python -m app.main

# Headless (upstream `claude -p`): no questions, so ExitPlanMode is denied: the session plans and exits 1.
# A session with an approved plan (resumed) develops it; exit 0 only if every item completed its cycle
python -m app.main -p --spec app/prompts/specs/<file>.txt
echo "Build a calculator" | python -m app.main -p

# Continue a session (its transcript, TDD plan and plan file); a prompt is its next message
python -m app.main --thread-id "tdd-<hash>"

# Force a brand-new run instead of the request-derived thread_id
python -m app.main --fresh

# Flags: --permission-mode,
# --settings <file>, --dangerously-skip-permissions, --coordinator
```

### Quality tooling

Use the repo-local, gitignored `.venv` (`python3 -m venv .venv && .venv/bin/pip install -r
requirements.txt`; it also provides the vendored `rg` binary and `types-PyYAML`). On the
original workstation the equivalent is `/home/amaro/tdd-agents/.venv`. (`/home/amaro/tdd-agents/venv` there is missing
`langchain_together`, which `app/utils/chat_model_factory.py` imports at module scope, so
importing the model call fails there.)

```bash
P=.venv/bin

$P/pytest tests/ -q                                     # offline suite, no credentials
$P/flake8 app tests                                     # zero findings
$P/mypy                                                 # strict; config in pyproject.toml
$P/mutmut run && $P/python scripts/mutmut_score.py      # mutation testing; see the gotchas
```

The full procedure and its pass criteria are in [Quality gate](#quality-gate-mandatory).

Without `-p`, `app.main` is interactive: it prints a menu built from `app/prompts/specs/*.txt` (unless a prompt, `--spec` or `--thread-id` is given), then opens the REPL (`app/screens/repl.py`) in plan mode (`⏸ plan mode on`): the Analyst explores, asks through AskUserQuestion and writes the plan file, and `ExitPlanMode` shows the plan with upstream's choices (a "Yes", "No, keep planning" with feedback, or `/edit`). After the approval the same conversation develops the plan; `/exit` (or `/quit`) leaves, resumable with `--thread-id`. Type to the agents at any time, Esc interrupts, `/help` lists `/plan`, `/pause`, `/resume`, `/requirements` and `/exit`. Because the menu resolves `Path("app/prompts/specs")` relatively, the CWD must be the repo root.

### Experiment tooling (post-hoc analysis of generated workspaces)

```bash
python mutation_tests/<task>/run_mutation_tests.py   # mutmut over the 3 recorded runs of a task
```

These scripts contain **hardcoded absolute paths** (`VENV_BIN`, `PROJECT_ROOT` pointing at `/home/amaro/...`) and must be edited before they run anywhere else.

SonarQube analysis of a generated workspace: `docker compose -f docker-compose.sonarqube.yaml up -d`, copy `backup/analyze.sh` + `backup/sonar-project.properties` into the workspace dir, set `SONAR_TOKEN`, then `./analyze.sh`. Thresholds live in `backup/restrictions.txt` and are re-implemented inline in `analyze.sh`.

## Claude Code reference

This repository keeps a local, verified copy of Claude Code's own source alongside four
analyses of it, because the refactor mirrors its architecture. Everything here is in
English and lives under `reference/`.

```
reference/
├── claude-code-map.md      # the index — always read first
├── claude-code/            # source snapshot, gitignored (~35MB)
└── analysis/
    ├── 01-dive-into-claude-code.md            # arXiv paper — the main reference
    ├── 02-inside-claude-code-leaked-source.md # O-mega article
    ├── 03-architecture-deep-dive-hasan.md     # Zain Hasan deep dive
    ├── decode/00.md … 11.md                   # 12-part code-level teardown
    └── assets/                                # figures from the paper
```

### How to use it — the rule

**Read `reference/claude-code-map.md` before grepping `reference/claude-code/`.** It is
short, it covers every subsystem, and it gives the path plus the symbol name.

The map is a **router, not an answer**. Every entry names an analysis section and a code
path, and a lookup is only finished when you have used both:

1. **Read the cited analysis section** — the *why*, and the design pattern.
2. **Open and analyze the cited code** — the *how*, and the current truth.

Stopping at the map is an incomplete lookup. The map deliberately carries one or two
sentences per subsystem and never the mechanism.

**Where an analysis and the code disagree, the code wins.** Check the map's
"Known divergences" section before concluding anything: several published claims about this
codebase point at the wrong file, and one subsystem all four analyses describe in detail
is absent from the shipped build.

### Which analysis to open

| Question | Start with |
|---|---|
| "How does subsystem X actually work?" | `analysis/decode/NN.md` — function-level mechanics, state machines, exact constants |
| "Why is X designed this way?" | `analysis/01-dive-into-claude-code.md` — the design-space paper, and the main reference |
| "What does the whole system look like?" | `analysis/03-architecture-deep-dive-hasan.md` — diagrams, startup sequence, end-to-end walkthrough |
| "What is surprising or hidden in here?" | `analysis/02-inside-claude-code-leaked-source.md` |

### Provenance and maintenance

- The source snapshot is **v2.1.88**, extracted from the published npm package's source
  map: 1,884 `.ts`/`.tsx` files, ~512K lines. It is **gitignored** and reproducible with
  `rsync -a ~/claude-code-2.1.88/source/src reference/claude-code/` (plus `vendor/` and the
  provenance files). `cli.js` and the 57MB `cli.js.map` stay outside the copy.
- **All four analyses target v2.1.88**, the same version as the snapshot. The O-mega article
  frames itself around an earlier leak, but its content matches this build — do not discard
  it as out-of-version.
- `analysis/03-` was **transcribed by reading a rasterized PDF** (no text layer, no OCR
  available), so it is the one analysis where a transcription error is possible. Verify any
  specific figure or symbol from it against the code.
- The original PDFs stay in `docs/` as provenance.
- **Verify before you cite.** Any claude-code claim added to a repository document must name a
  path and symbol that exist. Two scripts check this and both must pass:
  ```bash
  python3 scripts/reference-checks/verify_paths.py   <doc.md>...   # paths + symbols resolve
  python3 scripts/reference-checks/verify_anchors.py <doc.md>...   # analysis anchors resolve
  ```
- **A missing file does not mean a missing symbol.** Three mechanisms hide code from this
  extraction, and the map's "Before you trust an analysis" section tells them apart. The one
  that catches people: a module exporting **only types** is erased by the compiler and never
  reaches the source map — 37 modules are absent that way, including `src/query/transitions.ts`,
  which declares the loop's entire termination vocabulary. If the import that points at a
  missing file reads `import type`, the symbol is real.
- The old `~/claude-code` mirror and the `claude-code-explorer` MCP server are **gone**. Any
  reference to either, anywhere in this repository, is dead — including in
  `docs/transition_elaboration_plan.md`.

## Architecture

The module map is [The architecture](#the-architecture--claude-codes-loop-laid-out-like-its-source)
above; this section is the runtime path through it.

### One conversation, driven by `app/main.py`

`app/main.py` → `app/session/driver.py` → `develop_interactive` (the REPL) or `develop_headless`
(`-p`). The session opens in plan mode (the Analyst) until the user approves a plan through
`ExitPlanMode`, and the same conversation then develops it. `app/main.py` writes
`approved_plan.md`, `planning_dialogue.txt` and each item's outcome
(`metrics_and_logging/subreq_results.txt`). See
[The Analyst is upstream's Plan Mode](#the-analyst-is-upstreams-plan-mode-2026-10-30).

### One development session

`open_session_run` (`app/cli/print.py`) builds the run once per session (`RunConfig`,
permission context, hook dispatcher, MCP connections, skills, transcript store, cost
tracker, an `AppStateStore` holding the `TddPlan` and a ledger in RED), fires SessionStart,
and each turn (`begin_session`, `submit_prompt`) fires UserPromptSubmit and goes through
`QueryEngine.submit` over the session's history, which drives `run_loop`
(`app/query/query.py`). Each iteration: the
pre-model pipeline, `stream_call_model`, the tool calls through `run_tools` /
`StreamingToolExecutor` and `run_tool_use`, the turn's attachments, then either a continue
reason or a terminal. The TDD cycle is held by three barriers that no model output can
reach: the ledger (`app/tdd/ledger.py`, written only by `RunTests`), the phase permission
tier in the gate (`app/tdd/phase_permissions.py`), and the stop hook
(`app/tdd/stop_hook.py`) that refuses `completed` until the cycle is observed. Before it,
`plan_advance` marks a cycle-complete item done and blocks with the next one, so the
loop moves through the plan without a new turn.

### State and config

`app/config/config.py` holds `Config`: the provider and model (`CHAT_MODEL = "anthropic"`,
`MODEL = "claude-sonnet-5-5"` since 2026-11-10; `claude-haiku-5-5` from 2026-10-29, `gpt-5-mini` before) and the E2B sandbox constants. It **raises at import
time** if `OPENAI_API_KEY` is unset (`E2B_API_KEY` is optional), so
importing anything under `app/` without a `.env` fails immediately. A run's own settings are
snapshotted once into `RunConfig` (`app/query/config.py`).

There is no checkpoint store: a session resumes from its transcript and sidecars, as upstream's
`--resume` does (since 2026-10-30).

## Conventions and gotchas

- **Everything is in English** — prompts, logs, docstrings, comments, identifiers, and type names. (Phase 0 of the refactor migrated this codebase from a prior Portuguese-prompts/English-identifiers split; match English everywhere when adding code.)
- **Prompts are Markdown and they are the behavior.** System sections, agents and skills live under `app/prompts/`, each tool's prompt in `prompt.md` beside it; see [Prompt conventions](#prompt-conventions). There is no templating engine left in the codebase.
- **A new module goes where upstream keeps its counterpart**, snake_cased (`src/utils/foo/barBaz.ts` → `app/utils/foo/bar_baz.py`); TDD-only code goes in `app/tdd/`. Its docstring names the upstream file and symbols it ports.
- **Package `__init__.py` files re-export nothing.** Import a name from the module that defines it; the hubs were removed in Phase 10 so a module can move without breaking importers.
- `experimental_executions/` and `mutation_tests/` are **frozen research artifacts** backing the paper's results. Do not regenerate or reformat them.
