# TDDAgents: Refactoring Transition Plan — claude-code structure, TDD semantics

**Status:** Draft for execution — supersedes the previous "Completed & Fully Tested" version of
this file, whose claims do not hold against the tree (see §1).
**Date:** 2026-10-08
**Reference:** claude-code v2.1.88 at `reference/claude-code/src` (route through
`reference/claude-code-map.md`; where an analysis and the code disagree, the code wins).
**Companion:** `docs/transition_elaboration_plan.md` (design rationale, Parts A–L). This file is
the execution order from the current tree to a system that has claude-code's *structure* — one
loop, a tool pipeline, a permission gate, hooks, context assembly, compaction, transcripts,
subagents — while keeping the three TDD barriers that are TDDAgents' thesis contribution.

---

## 0. Decisions this plan is built on

Recorded user decisions (2026-10-08). Each phase below assumes them.

| Decision | Choice |
|---|---|
| Orchestration of one TDD cycle | **Coordinator + subagents.** A coordinator loop that only holds `Agent` delegates synchronously to `tester` (RED) → `developer` (GREEN) → `refactorer` (REFACTOR) → `verification`. Mirrors claude-code's coordinator mode. |
| Copied claude-code prompts / prompt registry | **Removed as an agent source.** Agents and skills resolve only from `app/prompts/` (TDDAgents-owned `AGENT.md` / `SKILL.md`, plus project/user overrides). Copies may stay under `docs/` as reference, never loaded at runtime. |
| Outer session layer | **Keep LangGraph as the shell only** (Part K): checkpointing, the analyst `interrupt()`, plan-item iteration. Each plan item runs one coordinator loop. |
| Earlier, still binding | Every behavior mirrors upstream unless a TDD invariant requires otherwise; product names become TDDAgents (`TDDAGENTS.md`, `.tddagents/`, `TDDAGENTS_*` env vars); headless — an unresolved `ask` is a deny; no ceilings (latches only); AutoMem off by default (Part I5); ripgrep via the pinned wheel. |

**The three TDD barriers that every phase must preserve** (§3.3 of the elaboration plan):
1. the **phase ledger**, written only by `RunTests` from observed exit codes;
2. **phase-derived deny rules**, at pool assembly *and* at the runtime gate, above every allow path;
3. the **`tdd_phase_incomplete` Stop hook**, which refuses termination until Red→Green is observed,
   kept terminable by the `stop_hook_active` latch.

---

## 1. Verified starting point (2026-10-08)

Established by five read-only audits of `app/loop` against the reference, plus direct runs.
"Built" means implemented and unit-tested in isolation; it does **not** mean reachable at runtime.

| Area | State | Evidence |
|---|---|---|
| Entry point | **Broken** | `import app.main` raises `ImportError` — `app/loop/factory.py` imports `LoopState` from `app.loop.context` (it lives in `app.loop.state`); `app/graph/orchestrator.py` → `app/main.py` import it at module load. |
| Production runner | **Broken** | `app/loop/runner.py`: `Path` undefined (NameError), `RunConfig(abort_signal=…)`, `CompactionTracking(message_id_range=…)`, `LoopState` missing 3 fields; builds `tool_context_for` without `workspace`, `permission_context`, `hook_dispatcher`. 21 mypy errors across the parallel modules. |
| Loop (`engine.py`) | Built, partial | Faithful `while True` + whole-record `LoopState`; 4 continue sites. Missing: proactive compaction, blocking-limit precheck, model fallback, API-error guard before stop hooks. Transitions go to a no-op `emit_event`. |
| Model call (`model.py`) | **Semantically wrong** | Yields raw stream chunks; the engine dispatches `tool_calls_in(chunk)` on partial chunks (empty `name`/`args`). No retry. |
| Tool pipeline (`tools/execution.py`) | Built, partial | Order close to upstream; no schema validation, no hook permission resolution, no PostToolUseFailure, no result-size governance; TDD check runs twice (pre-hook + gate). |
| File tools, ripgrep | Built, matches | Upstream contracts, errorCodes, read-before-write; ≥96% mutation. Missing: per-tool `maxResultSizeChars`. |
| Bash permissions | Built, matches | Legacy-path port of `bashToolHasPermission`; ≥92% mutation. **Not phase-aware**: `echo x > src/a.py` passes in RED. |
| Permission gate | Built, partial | `is_bypass_permissions_mode_available` defaults to `True` (upstream: false); TDD step fails open (`except Exception: pass`); content rules never consulted; pool deny uses `startswith` (a deny on `Edit` strips `EditTest`). |
| Settings → permissions | **Not wired** | `permissions/settings.py` has no runtime caller; `.tddagents/settings.json` is inert. |
| Hooks | **Not wired** | `app/hooks` (H1–H5) never invoked; `production_stop_hooks` is a no-op, so the D5 Stop hook never runs in production. |
| Compaction | **Not wired** | No autocompact; `production_compact` is a passthrough; post-compact cleanup has no caller. |
| TDD-state attachment | Bug | Appended to the request only, never to `state.messages`, so the delta check never dedupes. |
| Prompt cache blocks | Dead code | `context/cache.py` unused; one flat `SystemMessage`. |
| Transcript / resume | Partial | JSONL of stream chunks; no uuid chain, no boundary, no resume. |
| Subagents | **Stub** | `default_subagent_runner` returns canned text; background spawns nothing; subagents get a discarding `set_app_state`, so a worker's `RunTests` ledger write is **lost** — RED can never be confirmed through delegation. |
| Agent resolution | **Hijacked** | `agents/subagent.py` consults the prompt registry first: `explore`, `plan`, `verification` resolve to unrelated copied Claude Code prompts. |
| Auto-memory | Conflicts with I5 | `update_auto_memory` writes cross-run memory without the enable gate; run memory leaks when no store is passed. |
| Session shell | Partial | Without a runner, `execute_plan_item` fabricates success (`red`/`green` True). |
| `RunTests` | Fail-open | No workspace ⇒ exit 0 "all passed"; any non-zero exit (incl. pytest 2/5: usage error / no tests) confirms RED; `test_path` unescaped. |

---

## 2. Phases

Order is by dependency: nothing in a later phase is reachable until the earlier one lands. Every
phase ends with the `CLAUDE.md` quality gate (pytest → flake8 → mypy → mutmut ≥ 90% with every
survivor triaged) on the modules it touched, and with the structural invariants of §3 still green.

### Phase 0 — A runnable, honest baseline

**Goal:** `app.main` imports, the runner builds a real `LoopState` against a real workspace, and
nothing can report success it did not observe.

- `factory.py`: import `LoopState` from `app.loop.state`.
- `runner.py`: rebuild on `initial_loop_state` / `build_run_config`; pass `workspace`
  (`app/workspace` local or E2B), `permission_context`, `hook_dispatcher` to `tool_context_for`.
- Clear all 21 mypy errors (`runner`, `factory`, `resilience`, `tdd/hooks`, `pool`, `run_tests`,
  `subagent`, `registry`, `mcp`).
- **`RunTests` fails closed:** no workspace ⇒ error, not a pass. Classify pytest exit codes:
  `1` = failing tests (may confirm RED); `0` = pass; `2/3/4/5` (interrupted, internal, usage,
  no tests collected) never move the ledger. Quote `test_path`; add a timeout.
- **Single ledger writer:** remove the write-back of `state.phase_ledger` into app state in
  `engine.py` / `state.py` so `RunTests` is the only writer (barrier 1).
- Session shell: with no runner, `execute_plan_item` must fail, never fabricate success.
- Delete the root-level one-off scripts `patch_assembly.py`, `patch_loader.py`.

*Phase 0 outcome (2026-10-08), per the interview:* runs are **local** (E2B optional, room kept
via the runner's `Workspace` parameter); one workspace per session at
`.tddagents/runs/<thread_id>/workspace`, exported by copy to `workspace_output_<thread_id>/`;
tests run in a per-session venv (`ensure_session_python`, pytest pinned); `call_model` yields
whole messages (pulled forward from Phase 1); `RunTests` moves the ledger only on exit 0/1 and
on an exit-2 collection error caused by missing code; a shell without a runner raises.

*Acceptance:* `python -c "import app.main"` succeeds; an end-to-end smoke run on `LocalWorkspace`
with a scripted model writes a test, observes RED via `RunTests`, writes code, observes GREEN.

### Phase 1 — The model call and the loop's remaining continue/terminal paths

**Goal:** the loop sees complete assistant messages and recovers the way `queryLoop` does.

- Code: `claude-code/src/query.ts` → `queryLoop`, `State`; `claude-code/src/query/deps.ts` → `productionDeps`; `claude-code/src/services/api/withRetry.ts` → `withRetry`, `getRetryDelay`, `FallbackTriggeredError`; `claude-code/src/services/api/claude.ts` → `queryModelWithStreaming`
- `model.py`: yield **whole assistant messages** (accumulate chunks; emit at content-block close)
  so `tool_calls_in` never sees partial calls; keep the streaming executor's early dispatch on
  block-close semantics.
- Port `withRetry` (exponential backoff with jitter, `retry-after`, fallback signal) as the
  retry layer around `call_model`; add the engine's fallback branch (`discard()` the executor,
  tombstone partial output, rebuild).
- Add the **API-error guard**: if the last message is an API error, skip stop hooks and terminate
  `completed` — otherwise a model error becomes a TDD block and loops.
- `model_error` must yield the error and missing `tool_result` blocks (history repair, B7).
- Rename `resilience.py` to what it is (metrics), so "retry" means `withRetry` only.
- **Pool refresh between turns** (upstream `refreshTools`): rebuild the phase-filtered pool after
  every `RunTests` ledger change, so RED→GREEN updates both tools and prompts.

*TDD:* the stop hook latch stays as-is (`stop_hook_active` set only at `stop_hook_blocking`,
`has_attempted_reactive_compact` preserved across it — already matches upstream).

*Phase 1 outcome (2026-10-11), per the interview:* `withRetry` ported with upstream's
constants and its attempt ceiling — the one ceiling in the codebase, and `CLAUDE.md`'s
blanket "no ceilings" rule was rewritten to "ceilings follow claude-code, nothing else".
The model emits **one message per content block** (`streaming/blocks.py`), restoring eager
dispatch. Retry wraps opening the stream only. The fallback branch is an inner retry inside
one iteration, so the continue vocabulary is unchanged. The API-error guard skips stop hooks
but terminates `model_error` rather than upstream's `completed`, because the runner reads
`completed` as a passed sub-requirement. `refresh_tools` rebuilds the phase-filtered pool
between turns, compared by identity. `resilience.py` is now `metrics.py`.

### Phase 2 — Tool execution pipeline parity

- Code: `claude-code/src/services/tools/toolExecution.ts` → `runToolUse`, `checkPermissionsAndCallTool`; `claude-code/src/services/tools/toolHooks.ts` → `resolveHookPermissionDecision`; `claude-code/src/utils/toolResultStorage.ts` → `persistToolResult`, `getPersistenceThreshold`; `claude-code/src/constants/toolLimits.ts` → `DEFAULT_MAX_RESULT_SIZE_CHARS`; `claude-code/src/services/tools/toolOrchestration.ts` → `partitionToolCalls`
- Schema validation (JSON Schema against `input_schema`) as the first step of `run_tool_use`, and
  before `is_concurrency_safe` in partitioning.
- Port `resolveHookPermissionDecision` (hook `allow` still re-checked against deny/ask rules; hook
  `deny` final) and `PostToolUseFailure`; emit hook output as separate messages.
- `max_result_size_chars` on `BuiltTool`; persist oversized results to
  `.tddagents/tool-results/` with a preview (`ReadFile` opts out, as upstream `Read`).
- Concurrency cap for safe batches; run `Workspace.execute` off the event loop
  (`asyncio.to_thread`) and pass the per-tool abort controller into the tool context so the
  upward bubble works.
- **One TDD enforcement point:** delete `tdd_pre_tool_use_hook`'s duplicate check; the gate
  (and the fs tools' `check_permissions`) is the only path-level phase check, and it fails closed.
- **Bash becomes phase-aware:** its write redirections and file-writing commands go through
  `check_tdd_phase_permission` (a `>` into a production path in RED is a deny). Without this,
  barrier 2 is bypassable through `Bash`.
- Fix the pool's `startswith` deny (prefix only for `mcp__server`).
- TodoWrite tool (upstream `TodoWrite`) replacing the ad-hoc `TODO.md` read.

*Phase 2 outcome (2026-10-11), per the interview:* pipeline order is upstream's, with
schema validation (`jsonschema`) first and result governance **before** PostToolUse. A hook
`allow` is re-checked against the gate (`tools/hook_permissions.py`), so it cannot defeat a
phase rule. Oversized results are filed under `<config>/projects/<cwd>/<run>/tool-results/`
with a head-only 2000-byte preview, and `ReadFile` may read that one directory. **`Bash` is
phase-aware** through its redirection targets, closing the `echo code > calculator.py`
bypass of barrier 2. Both duplicate TDD hooks are deleted, leaving the gate as the single
enforcement point. Prefix tool denial is restricted to `mcp__`. Concurrent results are
emitted in completion order, matching `runTools`. `TodoWrite` replaces the `TODO.md` read.

### Phase 3 — Permissions and settings wired at runtime

- Code: `claude-code/src/utils/permissions/permissions.ts` → `hasPermissionsToUseTool`, `hasPermissionsToUseToolInner`, `checkRuleBasedPermissions`; `claude-code/src/utils/permissions/permissionSetup.ts` → `initializeToolPermissionContext`; `claude-code/src/utils/permissions/permissionsLoader.ts` → `loadAllPermissionRulesFromDisk`; `claude-code/src/utils/permissions/filesystem.ts` → `checkWritePermissionForTool`
- The runner builds the context with `build_tool_permission_context(…,
  should_avoid_permission_prompts=True)`; it lives in `AppState` and is passed to workers.
- `is_bypass_permissions_mode_available` defaults to `False`, as upstream.
- `WriteFile` / `Edit` get a `check_permissions` porting `checkWritePermissionForTool`
  (path deny rules, dangerous-file safety check, `acceptEdits` inside the working dir).
- Rule lookups pass content, so `Bash(…)`/`Edit(src/**)` rules apply at the gate.
- PermissionRequest hook before the headless deny (upstream
  `runPermissionRequestHooksForHeadlessAgent`) — it may allow, never override a phase deny.
- Add `flag`/`policy` settings sources; move `settings_paths` into `app/loop` (stop importing
  `app.hooks`).
- **TDD rules as a managed tier:** phase rules live in code, keyed on the ledger, evaluated
  *before* any settings-loaded allow — the equivalent of upstream's managed-rules-only option —
  so a project allow such as `Bash(python:*)` can never defeat RED. Review the shipped
  `.tddagents/settings.json` allow list against this.

*Phase 3 outcome (2026-10-14), per the interview ("always follow claude-code"):* the gate is
`hasPermissionsToUseToolInner` step for step (a `checkPermissions` exception is passthrough,
not deny; no read-only shortcut for `acceptEdits`/`plan`; no invented "interaction" ask),
with the TDD phase check above step 1a and **failing closed**. The run's context is built
like upstream's startup — `initialPermissionModeFromCLI` (run config, then settings
`defaultMode`), `initializeToolPermissionContext` over user, project, local, flag and policy
(`/etc/tddagents/managed-settings.json` + `managed-settings.d/`), `allowManagedPermissionRulesOnly`
— and lives in `AppState`. `WriteFile`/`Edit` port `checkWritePermissionForTool` with
gitignore-style `Edit(...)` rules (`matchingRuleForInput`, via `pathspec`), and Bash path
checks use the same `isPathAllowed`. The shipped settings gain `"defaultMode": "acceptEdits"`,
upstream's way to let a headless run write. **TDD managed tier:** upstream's
`stripDangerousPermissionsForAutoMode` (`isDangerousBashPermission`, `DANGEROUS_BASH_PATTERNS`)
runs for every TDD context, because the phase gate — like the classifier it was built for —
cannot see where an interpreter writes; `Bash(python:*)` and `Bash(python -m pytest:*)` are
stripped from the shipped allow list at load. PermissionRequest hooks run before the
headless deny (`runPermissionRequestHooksForHeadlessAgent`); they are reached only by an
'ask', so a phase deny never reaches them.

*Phase 3 follow-up (2026-10-14), from a source-verified research pass:* `resolveHookPermissionDecision`
is ported exactly (a PreToolUse allow is re-checked by `checkRuleBasedPermissions`, which
carries the TDD tier; a hook ask meets the TDD tier and deny rules before it is handled as an
ask); `checkReadPermissionForTool` guards `ReadFile`/`Glob`/`Grep`, with Read-deny exclude
globs for ripgrep; a PermissionRequest hook's `updatedPermissions` are applied and persisted
(`applyPermissionUpdate`, `persistPermissionUpdate`, `updateSettingsForSource`). TDD guards,
each chosen by the user: a hook's `updatedInput` is re-checked by the phase tier and deny
rules (as v2.1.110 later does upstream); applied updates are re-stripped of dangerous allows;
`setMode` stays unguarded and policy/flag dangerous allows survive, both as v2.1.88.

### Phase 4 — Hooks rebuilt against the loop

- Code: `claude-code/src/utils/hooks.ts` → `getMatchingHooks`, `matchesPattern`, `processHookJSONOutput`; `claude-code/src/query/stopHooks.ts` → `handleStopHooks`; `claude-code/src/utils/hooks/hooksConfigSnapshot.ts` → `getHooksFromAllowedSources`
- Move the dispatcher into `app/loop/hooks/` with upstream's JSON contract
  (`hookSpecificOutput.permissionDecision`, `updatedInput`, `additionalContext`, `continue:false`,
  legacy `decision`), async execution, and `disableAllHooks` / managed-only gates.
- Call sites: PreToolUse, PostToolUse, PostToolUseFailure, PermissionRequest, Stop,
  SubagentStop, SessionStart, UserPromptSubmit, PreCompact.
- `get_production_deps` wires the stop runner with **`tdd_phase_incomplete_hook` first**; a
  configured hook can add a block but never clear it; workers fire `SubagentStop` with
  `agent_type`.
- Then delete `app/hooks/` (scheduled in `CLAUDE.md`).

*Phase 4 outcome (2026-10-21), per the interview ("follow claude-code's source and
architecture" for every choice):* `app/loop/hooks/` ports the hook system whole — the 27-event
vocabulary, the four hook types (command, prompt, agent, http with the SSRF guard), the
config snapshot over the same five settings sources as the permission rules (policy
`disableAllHooks` / `allowManagedHooksOnly` / `strictPluginOnlyCustomization`), `executeHooks`
(parallel, results as they complete, deny > ask > allow), `processHookJSONOutput`, async and
`asyncRewake` hooks delivered between turns, and `if` conditions through each tool's new
`prepare_permission_matcher`. Call sites are upstream's: PreToolUse / PostToolUse /
PostToolUseFailure in the tool pipeline (hook output is now separate meta messages, reordered
after a batch's tool results for the API), PermissionRequest at the headless ask, Stop /
SubagentStop (by `agent_id`), StopFailure at the API-error and prompt-too-long exits,
SessionStart (`startup`, and `compact` after a compaction) + UserPromptSubmit + SessionEnd in
the runner, PreCompact / PostCompact in reactive compaction. `get_production_deps` wires the
stop runner with **`tdd_phase_incomplete_hook` first**; configured hooks only add blocks, and a
configured `continue: false` ends the run `stop_hook_prevented`, never `completed`. Prompt and
agent hooks default to `TDDAGENTS_SMALL_FAST_MODEL`, then the run's model. The agent-hook
verifier gets upstream's pool (the parent's phase-filtered tools minus the disallowed set) in
`dontAsk`, reads the parent's ledger and rules so the phase tier applies, and has its app-state
writes discarded, so it can never move the ledger. `app/hooks/` and `app/tools/` are deleted.

### Phase 4b — The auto-mode (YOLO) classifier

- Code: `claude-code/src/utils/permissions/yoloClassifier.ts` → `classifyYoloAction`, `buildYoloSystemPrompt`; `claude-code/src/utils/permissions/classifierDecision.ts` → `isAutoModeAllowlistedTool`; `claude-code/src/utils/permissions/denialTracking.ts` → `recordDenial`, `recordSuccess`; `claude-code/src/utils/permissions/permissions.ts` → `hasPermissionsToUseTool`
- Added by the user on 2026-10-21, built after Phase 4 passes its gate.
- Port the classifier code whole: transcript building, the `classify_result` tool, the
  two-stage XML path, fail-closed on errors, denial tracking, the acceptEdits and allowlist
  fast paths, and the auto branch of `hasPermissionsToUseTool` (before the headless deny), plus
  PermissionDenied hooks on a classifier denial.
- **The classifier's system prompt is absent from the v2.1.88 snapshot** (the `.txt` files are
  compiled out). A TDDAgents-owned Markdown prompt under `app/prompts/` replaces it, recorded as
  a divergence.
- **TDD:** the classifier only ever sees an 'ask' — the phase tier sits above it and denies
  first, so no classifier verdict can open a phase-forbidden write.
- `auto` is opt-in (`RunConfig.permission_mode` or settings `defaultMode: auto`); the shipped
  default stays `acceptEdits`.

*Phase 4b outcome (2026-10-22):* built as specified. `permissions/auto_mode.py` ports the auto
branch step for step (fail-closed, `DENIAL_LIMITS` 3/20; a headless abort raises upstream's
`AbortError`, which the tool pipeline's outer handler reports as the call's error), and `permissions/yolo_classifier.py` ports the classifier with upstream's
external defaults. The two-stage XML path is reachable only through `ClassifierOptions`
(upstream's switch is GrowthBook). The prompt and default rules under `app/prompts/permissions/`
ship as reconstructed. A test pins that RED denies a production write before the classifier is
consulted.

### Phase 5 — Context assembly, attachments and compaction

- Code: `claude-code/src/services/compact/autoCompact.ts` → `autoCompactIfNeeded`, `AUTOCOMPACT_BUFFER_TOKENS`; `claude-code/src/services/compact/compact.ts` → `compactConversation`; `claude-code/src/services/compact/postCompactCleanup.ts` → `runPostCompactCleanup`; `claude-code/src/utils/attachments.ts` → `getAttachments`; `claude-code/src/services/api/claude.ts` → `addCacheBreakpoints`
- Persist attachments into `state.messages` (meta user messages) so delta dedupe and the
  transcript see them; fixes the TDD-state attachment resending every turn.
- Proactive autocompact before `call_model` with upstream's window arithmetic (effective window,
  autocompact buffer, blocking limit); keep reactive compact after prompt-too-long; advance
  `CompactionTracking`.
- Real `production_compact`: upstream's sectioned summary prompt **plus TDD sections** (phase
  ledger, latest failing-test evidence, test file paths, sub-requirement verbatim); restore files
  from `read_file_state`; then `invalidate_context_caches`.
- **TDD state survives compaction from authoritative state**, re-attached after the boundary from
  the ledger and the last `RunTests` result — never reconstructed from the summary.
- Microcompaction and tool-result budget move onto stored history (stable prefix).
- Wire `build_system_prompt_blocks` / cache breakpoints when the provider supports them.
- Attachments worth porting: `changed_files` (tests/impl edited outside the agent), a
  todo-reminder cadence.
- Memory: gate `update_auto_memory` behind `is_auto_memory_enabled` (or remove it); rename or
  replace `extract_session_memory`; fix the I5 run-memory leak.

*Phase 5 outcome (2026-10-22), per the interview ("follow claude-code's source and
architecture"):* the engine runs upstream's pre-model pipeline on stored history (boundary
slice, tool-result budget — off as in the external build — microcompact — off by default —
autocompact, blocking-limit precheck) and persists `getAttachmentMessages` after each turn's
tools; the runner attaches the user-input attachments. `app/loop/compact/` ports
`compactConversation` with no kept tail, file restoration, PTL retry, the hooks and the
boundary, plus `autoCompactIfNeeded` with its circuit breaker of 3; reactive compaction uses
the same function. The summary prompt gains TDD sections, but the TDD state after the boundary
is re-attached from the ledger and `AppState.last_test_run` (written by `RunTests`), never from
the summary — pinned by a test whose summary lies. `app/loop/attachments/` ports the framework
and every source whose subsystem exists here (TDD-state among them). Cache breakpoints are wired
for Anthropic. Both memory inventions are deleted, and run-scoped agent memory is one store per
sub-requirement, outside the workspace, discarded at its end.

### Phase 6 — Session wrapper and transcripts (the evidence layer)

- Code: `claude-code/src/QueryEngine.ts` → `QueryEngine`, `submitMessage`; `claude-code/src/utils/sessionStorage.ts` → `recordTranscript`, `recordSidechainTranscript`; `claude-code/src/utils/conversationRecovery.ts` → `loadConversationForResume`; `claude-code/src/cost-tracker.ts` → `addToTotalSessionCost`
- A `QueryEngine`-equivalent per run: records the transcript **before** the loop, accumulates
  usage/cost, maps terminals to result subtypes.
- Transcript JSONL with `uuid`/`parentUuid`, `isSidechain`, compact-boundary records (append,
  never rewrite); whole messages, not stream chunks; sidechain files per worker.
- `emit_event` writes transitions **with a ledger snapshot and the `RunTests` exit code** — the
  input Part L needs to derive F1/F2 offline; sidechains excluded from per-sub-requirement
  flow derivation.
- Resume: load the chain, filter unresolved tool uses, detect interrupted turns.

*Phase 6 outcome (2026-10-22), per the interview ("follow claude-code's source and
architecture, while enforcing the TDD cycle"):* the runner drives each sub-requirement through
`app/loop/query_engine.py` — transcript recorded before the loop, whole messages after, the
result (`success` / `error_during_execution`, usage, cost, terminal reason) yielded and logged.
`app/loop/storage/` ports `recordTranscript` / `recordSidechainTranscript` at upstream's
`<config>/projects/<cwd>/<session>.jsonl` layout, and the resume read path
(`load_conversation_for_resume`), wired by the Phase 9 entry point. `app/loop/events.py` is the
run's `emit_event`: transitions with the ledger snapshot and the last `RunTests` exit code in
`<session>/events.jsonl` — Part L's input. `app/loop/cost/` ports the Claude price table and
adds an OpenAI one; unknown models cost 0 with a flag. The ledger is never restored from a
transcript.

### Phase 7 — Agents and the TDD coordinator

- Code: `claude-code/src/tools/AgentTool/AgentTool.tsx` → `AgentTool`; `claude-code/src/tools/AgentTool/runAgent.ts` → `runAgent`; `claude-code/src/tools/AgentTool/agentToolUtils.ts` → `resolveAgentTools`, `finalizeAgentTool`; `claude-code/src/utils/forkedAgent.ts` → `createSubagentContext`; `claude-code/src/tools/AgentTool/loadAgentsDir.ts` → `parseAgentFromMarkdown`; `claude-code/src/coordinator/coordinatorMode.ts` → `getCoordinatorSystemPrompt`
- **Agent resolution from `app/prompts/agents` only** (+ project/user overrides); remove the
  prompt registry from agent/skill lookup; keep the copied prompts only under `docs/`.
- A real `SubagentRunner`: drives `run_loop` with the agent's prompt, resolved tools and its own
  permission context; returns the last assistant text (`finalizeAgentTool`).
- **Ledger propagation:** sync workers share the parent's `set_app_state` (upstream
  `shareSetAppState: !isAsync`), so a tester's `RunTests` confirms RED on the shared ledger.
- **Phase on spawn:** the `Agent` call transitions the ledger to the agent's `phase` only if
  `PhaseLedger.transition_to` allows it (GREEN needs `red_confirmed`, REFACTOR needs
  `green_passed`); the worker pool is assembled with that ledger, so barrier 2 applies inside
  the worker.
- Enforce `permissionMode` (`read_only` strips writers); disable fork in headless runs, as
  upstream does in non-interactive sessions; require `subagent_type`.
- **TDD coordinator:** a coordinator agent whose pool is `Agent` (+ TaskStop), with a
  TDDAgents-owned coordinator prompt (adapted from upstream's phases): tester → developer →
  refactorer → verification, synchronously, re-delegating a failed phase. The
  `tdd_phase_incomplete` Stop hook guards the **coordinator**; workers fire `SubagentStop`.
- Agent-scoped memory is run-scoped (I5): one store per sub-requirement, discarded at its end.

*Phase 7 outcome (2026-10-22), per the interview ("follow claude-code's source and
architecture, while enforcing the TDD cycle"):*
- `run_agent` ports `runAgent` and `agents/tool.py` ports `AgentTool.call` (sync path).
  Sync workers share the parent's `set_app_state`, so a tester's `RunTests` confirms RED on
  the coordinator's ledger.
- Phase-on-spawn goes through `PhaseLedger.transition_to`: GREEN needs `red_confirmed`,
  REFACTOR a completed cycle, and RED restarts the cycle.
- Coordinator mode is upstream's env switch (`TDDAGENTS_COORDINATOR_MODE`, default off): the
  pool is filtered to `Agent`, and the system prompt is a TDDAgents-authored coordinator
  prompt.
- Agent definitions resolve from `app/prompts/agents` (+ user/project) only, with upstream's
  frontmatter; `resolveAgentTools` keeps Agent out of every worker.
- `subagent_type` is required and fork is off. The Agent tool stays concurrency-safe.
  `model` takes the Claude aliases plus an OpenAI table (default `o4-mini`; `gpt-5-mini` since 2026-10-27, when OpenAI deprecated o4-mini).
- An end-to-end test drives coordinator → tester → developer to `completed` on a real
  workspace.

### Phase 8 — Background work, skills, MCP (only what the coordinator needs)

- Code: `claude-code/src/Task.ts` → `generateTaskId`; `claude-code/src/tasks/LocalAgentTask/LocalAgentTask.tsx` → `enqueueAgentNotification`; `claude-code/src/skills/loadSkillsDir.ts` → `parseSkillFrontmatterFields`; `claude-code/src/services/mcp/mcpStringUtils.ts` → `buildMcpToolName`
- Task registry, task notifications, `TaskOutput`/`TaskStop` — used only for read-only
  explore/plan fan-out before RED; TDD phases stay synchronous.
- Skills: listing attachment, inline `newMessages`, `allowed_tools` modifier, frontmatter
  preload; TDD skills (`test-design`, `refactor-clean`, `mutation-defense`) are the roster.
- MCP: async handlers, closure fix, name normalization — only if an MCP server is in scope.

*Phase 8 outcome (2026-10-25), per the interview ("follow claude-code's source and
architecture, while enforcing the TDD cycle"; MCP: "port the main MCP functionalities"):*
- Background agents (`run_in_background`, `background: true`, coordinator-forced async) only for
  agents without a TDD `phase`; a phase agent is refused before any worker runs. Task registry
  in `AppState.tasks`, upstream's `<task-notification>` at `later` priority on one per-run
  command queue, output file symlinked to the sidechain.
- The runner ports `print.ts`'s `waitingForAgents` loop: notifications become turns of the same
  conversation until nothing runs or is queued; the outcome is the last turn's terminal.
- `TaskOutput`/`TaskStop` on the main thread; `SendMessage` (local agents, resume from the
  sidechain) behind `TDDAGENTS_EXPERIMENTAL_AGENT_TEAMS`.
- Skills: upstream loader, `Skill` tool (inline `newMessages`, allowed-tools as `command` rules
  under the TDD tier, `model` as `mainLoopModel`, fork via `run_agent` with phase-on-spawn for a
  phase agent), listing attachment, agent preload, conditional `paths` activation.
- MCP: config scopes, env expansion, stdio/sse/http/ws, connection manager, `mcp__` wrappers
  with passthrough permissions (headless deny without an allow rule; TDD tier first), resource
  tools, result limits, `mcp_instructions` section. Divergences are listed in `CLAUDE.md`.

### Phase 9 — Session shell and entry point

- Code: `claude-code/src/cli/print.ts` → `runHeadless`
- LangGraph shell (decision): analyst `interrupt()` → planner → per plan item **one coordinator
  run** (Phase 7) → evaluator reading the final ledger (`red_confirmed`, `green_passed`).
- Per-sub-requirement reset: fresh `AppStateStore`, `reset_session_context(run_id)`, run memory
  discarded, transcript boundary; carry forward only deterministic state (completed items, files).
- Headless entry point that runs a spec end-to-end without the interactive menu; Postgres
  checkpointer configurable; resolve `recursion_limit` against the no-ceilings rule.
- `TDDOrchestrator` stops auto-answering the analyst with `/yes` outside headless mode.

*Phase 9 outcome (2026-10-26), per the interview:*
- The session shell owns requirements gathering: `analyst ⇄ analyst_review (interrupt) →
  engineer → planner → items → evaluator`. The blocking `RequirementsOrchestrator`, its nodes
  and `TDDOrchestrator` (the double analyst and the auto-`/yes`) are deleted. The analyst's
  model call and its `interrupt()` are separate nodes, so a resume never asks the model again.
- Analyst, engineer and planner prompts are Markdown (`app/prompts/session/`); their calls go
  through `with_retry`. Approval is `/yes`, only while a checklist is pending; no round cap.
- Headless entry point (`python -m app.main -p`, `runHeadless`): checklist approved, a
  clarifying question fails the session, exit code from the outcome. Startup flags
  (`--permission-mode`, `--settings`, `--dangerously-skip-permissions`, `--coordinator`) reach
  every item's `RunConfig`; coordinator mode stays off by default.
- Checkpointer: `POSTGRES_URL` by default with the logged memory fallback, `--checkpoint
  memory` to opt out, and `Gates.postgres_checkpointing` records the real outcome.
  `recursion_limit` is unbounded (`sys.maxsize`).
- Resume: a thread continues from its checkpoint; an unfinished item (run id
  `item_run_id(thread, index)`) resumes its transcript with SessionStart(`resume`) and the
  interrupted prompt re-submitted once. The ledger is never restored.
- Per item: fresh `AppStateStore`, run memory discarded, `reset_session_context(run_id)`.
  Divergences are listed in `CLAUDE.md`.

### Phase 10 — Cleanup

- Delete the dissolved graph modules (Part L3), `app/tools/`, `app/sync/`, and `app/hooks/`
  (after Phase 4); remove the runtime prompt registry and copied prompt trees from
  `app/loop/prompts/`.
- Update `CLAUDE.md` tables, baselines and gotchas; re-run the full mutation baseline.

*Phase 10 outcome (2026-10-26), per the interview:*
- `app/loop/` is dissolved into claude-code's `src/` tree at `app/`, snake_cased
  (`tools/bash_tool/bash_permissions.py`, `services/api/with_retry.py`, `query/query.py` for
  `query.ts`); modules are split or merged to upstream's file boundaries. The TDD
  customization is one package, `app/tdd/`; each tool's prompt is `prompt.md` beside it.
- Deleted: the dissolved graph modules and legacy agents (Part L3), their Jinja2 templates,
  `app/sync/`, `app/errors/`, `app/schema/`, the runtime prompt registry and the copied prompt
  trees (`app/loop/prompts/` and `docs/`), and every module or symbol with no production
  caller. `app/tools/` and `app/hooks/` were already gone (Phase 4). E2B stays behind the
  `Workspace` protocol.
- F1/F2 and the resilience metrics are dropped; `subreq_results.txt` reports each item's
  terminal and final ledger.
- The gate runs over the whole tree: flake8 and strict mypy on all of `app/` and `tests/`,
  mutmut on `app/` minus the seam modules. Details in `CLAUDE.md`.

### Phase 11 — Interactive TDD REPL

After Analyst → Engineer → Planner, the tool behaves like Claude Code. The user talks to the
agents at any time and edits the plan or the requirements. Work continues until every
sub-requirement has been observed Red → Green.

*Phase 11 outcome (2026-10-27), per the interview:*
- **One conversation after planning.** The shell keeps `analyst ⇄ analyst_review → engineer
  → planner` and ends at `PLANNED`; the per-item `execute_plan_item → evaluator` iteration
  and `PlanItemResult` are deleted. `app/cli/print.py` opens one `DevelopmentRun` per session
  (`open_session_run`) and submits each user turn over its history (`REPL.tsx` `onQuery`).
- **The plan is state** (`app/tdd/plan.py`): `TddPlan` on `AppState`, saved as
  `tdd-plan.json` beside the transcript and restored on resume; the ledger is not.
  - **Only the ledger marks an item done.** `plan_advance` runs first in the stop-hook
    runner: a complete cycle marks the item done and blocks with the next item.
  - **Any change of the current item restarts the ledger in RED** (`update_tdd_plan`).
  - Editing a done item reopens it.
- **The model edits through `UpdatePlan`**, whose permission check is an ask with the diff
  (as `ExitPlanModeV2Tool`); main thread only. **The user edits through `/plan`.**
- **The REPL** (`app/screens/repl.py`, `app/commands.py`):
  - input typed mid-turn is queued at `next` (`handlePromptSubmit`);
  - Esc / Ctrl-C aborts the turn (`useCancelRequest`);
  - slash commands wait for the turn to end, except `/help`, `/plan`, `/pause` and `/exit`;
  - `/pause` ends a turn `stop_hook_prevented`;
  - `/requirements` re-runs analyst → engineer → planner; done items stay done, pending
    ones are replaced.

  `prompt_toolkit` is the terminal seam (`app/screens/terminal_io.py`).
- **Permission asks are interactive** (`app/hooks/use_can_use_tool.py`,
  `app/components/permissions/permission_options.py`). Choices are upstream's: yes, yes and
  don't ask again (with the Bash/file rule suggestions), or no with feedback. The TDD tier is
  still checked first. Workers and the agent-hook verifier keep the headless deny.
- **Headless `-p`** runs the same engine with nobody to answer. Every ask is denied,
  `UpdatePlan` included. A turn that ends before the current item is done stops the session;
  the remaining items are NOT_RUN and the exit code is 1.
- **Coordinator mode** keeps upstream's tool filter verbatim, so the coordinator has no
  `UpdatePlan`. Upstream's filter likewise drops its own planning tools (`TodoWrite`,
  `ExitPlanMode`). In coordinator mode the plan is edited by the user only.

*Divergences:*
- permission hooks run before the prompt instead of racing it;
- there is no editable Bash prefix and no accept-with-feedback input;
- `getDirectoryForPath` is not stat-based.

### Phase 12 — Robust-tests RED, mandatory coverage REFACTOR

*Phase 12 outcome (2026-10-28), per the interview:*
- **RED** builds robust unit tests and need not fail. It ends with a RunTests run (pass or
  fail) in which the item's test files changed since the item started. RunTests observes
  that itself by fingerprinting the test files against a baseline taken when the item
  starts (`app/tdd/suite_fingerprint.py`).
- **GREEN** ends with a passing run. The tests are locked.
- **REFACTOR is mandatory.** It ends with a passing run whose line + branch coverage of the
  production code reaches 90% (`app/tdd/coverage_gate.py`, pytest-cov on every run). No
  production code to measure counts as met.
- **Write rules:** RED writes tests, GREEN writes code, and REFACTOR writes both.
- **The planner is relaxed:** one behaviour per item, with no fail-first rule and no ban on
  hardening items.
- **The ledger fields are renamed** (`tests_written`, `green_passed`, `refactor_passed`,
  `coverage`), and `events.jsonl` records them.
- **The mutation gate counts timeouts once they are re-verified alone.**
- **The TDD stop hook never gives up:** no latch, in any phase, headless included (as
  upstream). A turn ends early only on pause, abort, a configured `continue: false` or an
  error.

---

## 3. Verification

**Per phase:** the `CLAUDE.md` quality gate on touched modules.

**Structural invariants (Part D6), re-run after every phase:**
1. No reachable tool pool — main loop *or worker* — in RED contains a production-writing path,
   including `Bash` write redirections and settings-loaded allows.
2. No sequence of model outputs reaches `completed` without the ledger showing Red-then-Green,
   including runs that end in a model error, a compaction, or a worker's termination.
3. (new) The ledger has exactly one writer: `RunTests`.

**End to end:** from Phase 0 on, a scripted-model run on `LocalWorkspace`; from Phase 7 on, a real
model on the E2B sandbox through the coordinator; Part L derives F1/F2 from the Phase 6 transcript.

**Against the reference:** every `- Code:` citation in this file must pass
`python3 scripts/reference-checks/verify_paths.py docs/refactoring_transition_plan.md`.
