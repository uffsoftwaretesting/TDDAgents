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

### Phase 8 — Background work, skills, MCP (only what the coordinator needs)

- Code: `claude-code/src/Task.ts` → `generateTaskId`; `claude-code/src/tasks/LocalAgentTask/LocalAgentTask.tsx` → `enqueueAgentNotification`; `claude-code/src/skills/loadSkillsDir.ts` → `parseSkillFrontmatterFields`; `claude-code/src/services/mcp/mcpStringUtils.ts` → `buildMcpToolName`
- Task registry, task notifications, `TaskOutput`/`TaskStop` — used only for read-only
  explore/plan fan-out before RED; TDD phases stay synchronous.
- Skills: listing attachment, inline `newMessages`, `allowed_tools` modifier, frontmatter
  preload; TDD skills (`test-design`, `refactor-clean`, `mutation-defense`) are the roster.
- MCP: async handlers, closure fix, name normalization — only if an MCP server is in scope.

### Phase 9 — Session shell and entry point

- Code: `claude-code/src/cli/print.ts` → `runHeadless`
- LangGraph shell (decision): analyst `interrupt()` → planner → per plan item **one coordinator
  run** (Phase 7) → evaluator reading the final ledger (`red_confirmed`, `green_passed`).
- Per-sub-requirement reset: fresh `AppStateStore`, `reset_session_context(run_id)`, run memory
  discarded, transcript boundary; carry forward only deterministic state (completed items, files).
- Headless entry point that runs a spec end-to-end without the interactive menu; Postgres
  checkpointer configurable; resolve `recursion_limit` against the no-ceilings rule.
- `TDDOrchestrator` stops auto-answering the analyst with `/yes` outside headless mode.

### Phase 10 — Cleanup

- Delete the dissolved graph modules (Part L3), `app/tools/`, `app/sync/`, and `app/hooks/`
  (after Phase 4); remove the runtime prompt registry and copied prompt trees from
  `app/loop/prompts/`.
- Update `CLAUDE.md` tables, baselines and gotchas; re-run the full mutation baseline.

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
