# Claude Code architecture map

**Snapshot:** `@anthropic-ai/claude-code@2.1.88`, extracted from the published npm
source map. 1,884 `.ts`/`.tsx` files, ~512K lines, 35 directories and 18 files at the
top of `src/`.
**Generated:** 2026-09-13.

**`reference/claude-code/` is the source of truth.** Everything below is an index into
it. Where this map, an analysis, and the code disagree, the code wins.

## How to use this map

This map is a **router, not an answer**. Every entry gives you two destinations, and a
lookup is only finished when you have visited both:

1. **Read the cited analysis section** — it tells you *why* the subsystem is shaped the
   way it is, and which design pattern it uses.
2. **Open and analyze the cited code** — it tells you *how* it actually works today.

Read the map before grepping. Grepping 1,884 files to find where something lives is the
thing this file exists to prevent. But never stop at the map: it deliberately carries one
or two sentences per subsystem, never the mechanism.

Every entry carries both an `Analysis:` and a `Code:` line. When no analysis covers a
subsystem, the line still appears, written `Analysis: none — code only`, so a gap is
stated rather than hidden.

Paths are given without line numbers, because line numbers rot. Use the symbol name.

**Read [Before you trust an analysis](#before-you-trust-an-analysis--read-this) before your
first lookup, and [Known divergences](#known-divergences) before concluding anything.** The
analyses are good and all target v2.1.88, but several send you to the wrong file, and one
subsystem all four describe in detail is absent from the shipped build. Those two sections
exist so you do not reason confidently about code that is not the code that runs.

### The analyses

| File | What it is | Best for |
|---|---|---|
| `analysis/01-dive-into-claude-code.md` | *"Dive into Claude Code"*, VILA Lab / MBZUAI, arXiv:2604.14228v1. Peer-style design-space paper. **The main reference.** | Why a design was chosen; the value/principle framing; comparison with other agents |
| `analysis/02-inside-claude-code-leaked-source.md` | *"Inside Claude Code: The Leaked Source Analysis"*, O-mega / Yuma Heymans | Narrative overview; hidden features; the "what is surprising here" reading |
| `analysis/03-architecture-deep-dive-hasan.md` | *"Claude Code v2.1.88 — Architecture Deep Dive"*, Zain Hasan | Whole-system diagrams; startup sequence; end-to-end message walkthrough; Grep and compaction deep dives |
| `analysis/decode/00.md` … `11.md` | 12-part code-level teardown, ~10,800 lines | **The most detailed source.** Function-level mechanics, state machines, exact constants, historical bug notes |

All four analyze **v2.1.88** — the same version as the dump. Start with `decode/` when the
question is "how does X actually work", and with `01-` when the question is "why is X like
this".

---

## Before you trust an analysis — read this

**The four analyses are secondary sources. They are good, they all target v2.1.88, and they
are still wrong in places that matter.** Every correction below was checked against
`reference/claude-code/` in this snapshot. If you follow an analysis without checking, the
most likely outcome is that you open the wrong file and reason confidently about code that
is not the code that runs.

There are four reasons the analyses drift, and telling them apart is most of the skill:

1. **Wrong altitude.** The analysis names a real file that really does the work it
   describes — but that file is a subroutine, not the decision point. The fix is to go up
   one level, not to discard the analysis.
2. **Wrong location.** The subsystem exists, under a different path than the analysis says.
   The analysis's *reasoning* is usually still fine; only the path is wrong.
3. **Build-time elimination.** The analysis describes a design that is genuinely in
   Anthropic's codebase but is **not in this artifact**, because `feature()` from
   `bun:bundle` is a compile-time macro: a flag that is off deletes the branch, so the
   module never enters the bundle or the source map. The design is real; the code is absent.
   This is normal dead-code elimination, not a damaged extraction.
4. **Type erasure.** A module that exports *only types* contributes no runtime code, so it
   never reaches the bundle or the source map either — even though nothing about it is
   disabled. `src/query/transitions.ts` is the important case: `src/query.ts` imports the
   loop's whole `Terminal`/`Continue` vocabulary from it with `import type`. **37 modules are
   absent this way**, against 7 from elimination.

**Telling 3 and 4 apart is mechanical.** Find the import that points at the missing file:

| the import reads | meaning | what to do |
|---|---|---|
| `import { x } from './missing.js'` inside a `feature('FLAG')` branch | eliminated at build time | `grep -rn "feature('FLAG')" src/` shows the call sites; the design is real, the code is not here |
| `import type { X } from './missing.js'` | erased by the compiler | the symbol is **real and in use** — reconstruct it from its construction sites |

Note what is *not* on that list: the code is not deprecated, not broken, and not partial.
It is Anthropic's shipped v2.1.88.

### The two that will actually change what you do

**1. To answer "may this Bash command run", start at `bashPermissions.ts` — not
`bashSecurity.ts`.**

Two analyses build sections on `src/tools/BashTool/bashSecurity.ts`, and they are describing
something real: 2,593 lines running ~20 validators. Its exports carry a `_DEPRECATED`
suffix — but **that marks the API shape, not disuse. The module is still called**, from
`bashPermissions.ts` (which imports it and re-aliases it back to `bashCommandIsSafeAsync`),
from `readOnlyValidation.ts`, and from `bashCommandHelpers.ts`. Treating it as dead code is
a mistake.

The error is altitude. The function that decides is **`bashToolHasPermission` in
`src/tools/BashTool/bashPermissions.ts`** (2,622 lines), which *no* analysis names. It does
prefix extraction, wrapper stripping, rule matching, and the speculative classifier check —
and calls the legacy battery as one gate among several. Read `bashPermissions.ts` first and
follow it into `bashSecurity.ts` when the trail leads there.

**2. The compaction pipeline is designed as five layers; this build runs three.**

All four analyses describe five layers. Layers 2 and 4 —
`snipCompactIfNeeded()` and `applyCollapsesIfNeeded()` — sit behind `feature('HISTORY_SNIP')`
and `feature('CONTEXT_COLLAPSE')`, and **their modules are not in this snapshot**:
`src/services/compact/snipCompact.ts` and `src/services/contextCollapse/` do not exist. Only
the lazy `require()` call sites remain, in `src/query.ts`. The write side of context collapse
*does* survive (`recordContextCollapseCommit` in `src/utils/sessionStorage.ts`), which is
what makes the absence easy to miss.

So the five-layer description is architecturally true and empirically false for this
artifact. Say "three layers ship here, two are feature-gated out" rather than picking a side.
The paper flags this class of problem itself, in
`analysis/01-dive-into-claude-code.md#b3-limitations`, while its body still describes all
five as present.

### Wrong-location corrections, in one table

Each of these is a path an analysis will send you to that does not hold what you want.

| If an analysis points you at… | …what is actually there | …where the thing you want lives |
|---|---|---|
| `src/hooks/` for the hook system | 83 React UI hooks | `src/utils/hooks.ts` + `src/utils/hooks/` |
| `src/agents/` for agent definitions | nothing — the directory does not exist | `src/tools/AgentTool/loadAgentsDir.ts` |
| `src/plugins/` for plugin code | 2 files (`builtinPlugins.ts`, `bundled/`) | `src/utils/plugins/` (44 files) |
| `src/context/` for the context window | 9 React context providers (`.tsx`) | `src/context.ts` + `src/constants/prompts.ts` + `src/utils/attachments.ts` |
| `src/assistant/` for turn logic | one file: remote session-history paging | `src/query.ts` |
| `src/Task.ts` for the Task/Agent tool | the background-task framework type | `src/tools/AgentTool/AgentTool.tsx` |
| `src/tools/` for tool orchestration | the 40 tool implementations | `src/services/tools/` |
| `src/coordinator/` for a scheduler | prompt and persona policy only | `src/services/tools/toolOrchestration.ts` |
| `history.ts` for conversation transcripts | the shell-style `↑` prompt input history | `src/utils/sessionStorage.ts` |
| `src/memdir/` for CLAUDE.md loading | the separate `MEMORY.md` auto-memory subsystem | `src/utils/claudemd.ts` |
| `src/query/transitions.ts` for stop reasons | nothing — types-only, erased (reason 4) | constructed inline in `src/query.ts` |
| `src/services/mcp/transports/` | nothing — no such directory | transports inlined in `src/services/mcp/client.ts` |
| `queryLoop()` as the public entry point | it is module-private (no `export`) | `query()` in `src/query.ts` |
| `QueryEngine.ts` as the agent loop | a per-session wrapper that calls `query()` | `src/query.ts` |

### Numbers in the analyses are not wrong so much as unstated

- **"Seven permission modes"** is the *type-level* count. `EXTERNAL_PERMISSION_MODES` has
  five; `auto` is added only under `feature('TRANSCRIPT_CLASSIFIER')`; `bubble` is
  typecheck-only. At runtime it is five or six. There is **no enum** — `as const` + `z.enum`.
- **"54 built-in tools"** counts the registry. `src/tools/` holds **40** `*Tool/`
  directories, and `getAllBaseTools()` references at least ten more whose modules were
  eliminated at build time. Three on-disk directories are partial (`REPLTool/`, `SleepTool/`
  keep only constants; `ExitPlanModeTool/` exports `ExitPlanModeV2Tool`), one holds three
  tools (`ScheduleCronTool/`), and one uses a factory (`createMcpAuthTool`).
- **"50+ / 100+ slash commands"** counts directory entries. Of 86 directories plus 15
  top-level modules, **18 directories are disabled stubs** exporting
  `{ isEnabled: () => false, isHidden: true, name: 'stub' }`.
- **27 hook events** is correct — and the list is duplicated verbatim in two files.
- **1,884 files** is correct and matches this snapshot exactly.

### How to handle a divergence you find yourself

The code wins. But "the code wins" is not "the analysis is garbage" — in every case above
the analysis was describing something real and got the address or the altitude wrong. When
you hit one:

1. Confirm it in `reference/claude-code/` before acting on it.
2. Check whether the module was **feature-eliminated** rather than absent by design:
   `grep -rn "feature('FLAG')" src/` shows the call sites even when the target is gone.
   `analysis/decode/10.md#i-complete-categorized-list-of-88-build-time-feature-flags`
   catalogues 88 such flags.
3. Report it as "the design is X, this build ships Y" rather than "the analysis is wrong".

The full list, with evidence, is in [Known divergences](#known-divergences) at the end of
this file. **Read it before concluding anything about this codebase.**

---

## Core runtime

### Agent loop
The `while(true)` async generator that drives every turn: assemble context, call the model,
run tools, repeat until the model answers with text only. Generator-driven reactive loop
(ReAct), with a single mutable `State` record rewritten wholesale at each of the seven
continue sites.

- Analysis: `analysis/01-dive-into-claude-code.md#41-the-query-pipeline`, `analysis/decode/01.md#i-queryloop-complete-state-machine-reconstruction`
- Code: `claude-code/src/query.ts` → `query`, `queryLoop`
- Consult when: you need to know the order of operations inside a turn, where a turn can exit, or where to hook new per-turn behavior.

### Loop termination and continue vocabulary
The named reasons a turn ends or iterates again. The `Terminal` and `Continue` unions are
declared in `src/query/transitions.ts`, which is **types-only and therefore absent from this
extraction** (see [Known divergences](#known-divergences) #5); the reasons below were
reconstructed from their construction sites in `src/query.ts` and are complete.

- Analysis: `analysis/01-dive-into-claude-code.md#45-stop-conditions`, `analysis/decode/01.md#i-queryloop-complete-state-machine-reconstruction`
- Code: `claude-code/src/query.ts` → `'completed'`, `'max_turns'`, `'blocking_limit'`, `'model_error'`, `'prompt_too_long'`, `'aborted_streaming'`, `'aborted_tools'`, `'stop_hook_prevented'`, `'hook_stopped'`, `'image_error'`
- Consult when: you are debugging why a turn stopped, or adding a new stop condition.

### Loop dependency injection
The four-slot seam that lets the loop be driven with fakes: model call, microcompact,
autocompact, uuid. Explicit constructor injection rather than a container.

- Analysis: `analysis/decode/01.md#viii-patterns-worth-learning`
- Code: `claude-code/src/query/deps.ts` → `QueryDeps`, `productionDeps`
- Consult when: you want to test the loop offline, or swap the model call.

### Query configuration snapshot
A once-per-query freeze of feature gates and environment so behavior cannot change mid-turn.

- Analysis: none — code only
- Code: `claude-code/src/query/config.ts` → `buildQueryConfig`, `QueryConfig`
- Consult when: you need to know which gates a given turn saw.

### Token budget tracking
Tracks spend against a turn/task budget and decides whether to continue, warn, or stop.

- Analysis: `analysis/decode/05.md#5-cost-tracking`
- Code: `claude-code/src/query/tokenBudget.ts` → `checkTokenBudget`, `createBudgetTracker`, `BudgetTracker`
- Consult when: implementing spend caps or diagnosing a `token_budget` stop.

### Stop hooks handling
Runs `Stop`/`SubagentStop` hooks at the loop boundary and can force the loop to keep going.

- Analysis: `analysis/decode/01.md#ix-complete-architecture-of-stop-hooks`
- Code: `claude-code/src/query/stopHooks.ts` → `handleStopHooks`
- Consult when: a hook needs to veto the end of a turn.

### QueryEngine (session wrapper)
Holds conversation state across turns — message history, session id, permission tracking,
file-state cache — and calls `query()` per turn. **It is not the loop.**

- Analysis: `analysis/01-dive-into-claude-code.md#34-queryengine-a-clarification`, `analysis/03-architecture-deep-dive-hasan.md#4-the-query-loop--core-interaction-flow`
- Code: `claude-code/src/QueryEngine.ts` → `QueryEngine`, `ask`
- Consult when: you need per-session state rather than per-turn state.

### Model call and streaming
Builds the API request, streams SSE back, and assembles the assistant message. Includes a
stream-stall detector that falls back to non-streaming.

- Analysis: `analysis/01-dive-into-claude-code.md#42-tool-dispatch-and-streaming-execution`, `analysis/decode/01.md#iii-in-depth-analysis-of-streaming-processing`
- Code: `claude-code/src/services/api/claude.ts` → `queryModelWithStreaming`, `queryModelWithoutStreaming`, `buildSystemPromptBlocks`, `addCacheBreakpoints`
- Consult when: touching prompt caching, request shape, or streaming behavior.

### Retry and model fallback
Per-attempt retry with backoff, plus a thrown signal that swaps to the fallback model.

- Analysis: `analysis/01-dive-into-claude-code.md#44-recovery-mechanisms`, `analysis/decode/01.md#ii-error-handling-layer-by-layer-analysis`
- Code: `claude-code/src/services/api/withRetry.ts` → `withRetry`, `FallbackTriggeredError`, `CannotRetryError`, `getRetryDelay`
- Consult when: diagnosing retry storms or adding a new retryable error class.

### Tool orchestration
Partitions a turn's tool calls into contiguous runs by concurrency safety: safe runs fan
out in parallel, unsafe runs execute serially. Results are re-sorted into the model's call
order.

- Analysis: `analysis/01-dive-into-claude-code.md#42-tool-dispatch-and-streaming-execution`, `analysis/03-architecture-deep-dive-hasan.md#5-tool-execution-sequence-diagram`
- Code: `claude-code/src/services/tools/toolOrchestration.ts` → `runTools`
- Consult when: reasoning about parallel tool execution or adding a scheduling rule.

### Streaming tool execution
Starts tools while the model is still streaming, buffering results in receive order. Owns a
sibling abort controller so one failing tool kills its siblings without ending the turn.

- Analysis: `analysis/decode/01.md#iii-in-depth-analysis-of-streaming-processing`
- Code: `claude-code/src/services/tools/StreamingToolExecutor.ts` → `StreamingToolExecutor`
- Consult when: investigating tool-abort semantics or early-start behavior.

### Single tool execution
Name resolution, input validation, permission check, call, and result mapping for one tool.

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#5-tool-execution-sequence-diagram`, `analysis/decode/03.md#1-deep-dive-into-the-tool-type-system`
- Code: `claude-code/src/services/tools/toolExecution.ts` → `runToolUse`, `classifyToolError`
- Consult when: a tool call fails before the tool body runs.

### Tool lifecycle hooks
The wrapper that fires `PreToolUse`/`PostToolUse`/`PostToolUseFailure` around a tool call and
lets a hook override the permission decision.

- Analysis: `analysis/01-dive-into-claude-code.md#53-auto-mode-classifier-and-hook-lifecycle`, `analysis/decode/06.md#6-hooks-system-in-depth`
- Code: `claude-code/src/services/tools/toolHooks.ts` → `runPreToolUseHooks`, `runPostToolUseHooks`, `resolveHookPermissionDecision`
- Consult when: a hook needs to block, rewrite, or annotate a tool call.

### Entry points and startup
Fast-path CLI dispatch (`--version` returns with zero heavy imports), then full init,
telemetry, permissions, setup, and REPL launch — aggressively parallelized.

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#3-startup--bootstrap-sequence-diagram`, `analysis/decode/00.md#ii-startup-timing-diagram-blockingnon-blocking-annotated-version`
- Code: `claude-code/src/entrypoints/cli.tsx`; `claude-code/src/main.tsx` → `main`, `startDeferredPrefetches`; `claude-code/src/setup.ts`
- Consult when: adding startup work, or explaining why the CLI starts fast.

### Task framework (background work)
Registry of background task kinds — local shell, local agent, remote agent, teammate, dream.
Only `kill` is dispatched polymorphically; the rest was deliberately removed.

- Analysis: `analysis/decode/11.md#i-deep-dive-into-the-task-system`
- Code: `claude-code/src/Task.ts` → `Task`, `TaskType`, `generateTaskId`; `claude-code/src/tasks.ts` → `getAllTasks`, `getTaskByType`; `claude-code/src/tasks/`
- Consult when: adding a background task type or debugging task lifecycle.

### Coordinator mode
A distinct system prompt and tool filter for multi-agent orchestration. Policy only — it is
**not** a scheduler.

- Analysis: `analysis/decode/07.md#4-coordinator-mode-in-depth`
- Code: `claude-code/src/coordinator/coordinatorMode.ts` → `isCoordinatorMode`, `getCoordinatorSystemPrompt`, `getCoordinatorUserContext`
- Consult when: you see "coordinator" and need to know whether it schedules anything (it does not).

### Cost tracking
Accumulates USD cost and token usage per session for the status line and `/cost`.

- Analysis: `analysis/decode/05.md#5-cost-tracking`
- Code: `claude-code/src/cost-tracker.ts`, `claude-code/src/costHook.ts`
- Consult when: reporting or capping spend.

---

## Tool layer

### Tool protocol
The contract every capability fulfils: identity, Zod schema, `call`, per-input predicates
(`isReadOnly`, `isConcurrencySafe`), permission check, and React renderers. A **type alias
with ~50 members**, not a class — `buildTool()` fills omitted members with defaults.

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#the-tool-interface-srctoolts`, `analysis/decode/03.md#1-deep-dive-into-the-tool-type-system`
- Code: `claude-code/src/Tool.ts` → `Tool`, `Tools`, `buildTool`, `ToolUseContext`, `ToolPermissionContext`, `findToolByName`
- Consult when: adding a tool, or reasoning about what the loop can ask of any tool.

### Tool pool assembly
Enumerate base tools → filter by mode → strip deny-ruled tools before the model sees them →
merge MCP tools → dedupe. Partition-sorted so the server-side cache breakpoint stays stable.

- Analysis: `analysis/01-dive-into-claude-code.md#62-tool-pool-assembly`, `analysis/decode/03.md#8-complete-tool-inventory`
- Code: `claude-code/src/tools.ts` → `getAllBaseTools`, `getTools`, `assembleToolPool`, `filterToolsByDenyRules`, `getMergedTools`
- Consult when: a tool is unexpectedly present or absent from the model's pool.

### Tool result governance
Per-result and per-message size caps. Oversized results spill to disk and are replaced by a
preview plus a path the model can read back.

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#layer-1-tool-result-budget`, `analysis/decode/03.md#7-tool-result-persistence`
- Code: `claude-code/src/utils/toolResultStorage.ts` → `applyToolResultBudget`, `enforceToolResultBudget`, `persistToolResult`; `claude-code/src/constants/toolLimits.ts` → `DEFAULT_MAX_RESULT_SIZE_CHARS`, `MAX_TOOL_RESULTS_PER_MESSAGE_CHARS`
- Consult when: a large tool output is truncated or replaced by a file reference.

### Deferred tool schemas (ToolSearch)
Tools can ship name-only in the initial context and load their full schema on demand, to
keep the tool block small.

- Analysis: `analysis/decode/03.md#4-toolsearch-deferred-loading-mechanism`
- Code: `claude-code/src/tools/ToolSearchTool/` → `ToolSearchTool`; `claude-code/src/Tool.ts` → `shouldDefer`
- Consult when: a tool exists but the model claims it cannot call it.

### Built-in tool catalog
40 tool directories under `src/tools/`, plus `shared/` and `testing/`. Each entry below
names the directory and the exported symbol. `getAllBaseTools()` is the registry of record.

- Analysis: `analysis/01-dive-into-claude-code.md#a2-conditional-tool-availability`, `analysis/decode/03.md#8-complete-tool-inventory`
- Code: `claude-code/src/tools/` → see the per-tool entries below
- Consult when: you need to know which tool does what before opening it.

#### AgentTool
Spawns a subagent with a fresh context and its own tool pool; the delegation primitive.
- Analysis: `analysis/01-dive-into-claude-code.md#81-the-agent-tool-and-delegation-criteria`, `analysis/decode/03.md#3-complete-anatomy-of-agenttool`
- Code: `claude-code/src/tools/AgentTool/AgentTool.tsx` → `AgentTool`
- Consult when: delegating work to an isolated agent.

#### AskUserQuestionTool
Presents a structured multiple-choice question to the user mid-turn.
- Analysis: `analysis/decode/03.md#8-complete-tool-inventory`
- Code: `claude-code/src/tools/AskUserQuestionTool/AskUserQuestionTool.tsx` → `AskUserQuestionTool`
- Consult when: the model needs a decision only the user can make.

#### BashTool
Shell command execution — the largest and most safety-critical tool (18 files).
- Analysis: `analysis/01-dive-into-claude-code.md#54-shell-sandboxing`, `analysis/decode/03.md#2-complete-anatomy-of-bashtool-18-files`
- Code: `claude-code/src/tools/BashTool/BashTool.tsx` → `BashTool`
- Consult when: anything touching command execution or its permission path.

#### BriefTool
Emits a short structured brief/summary artifact.
- Analysis: `analysis/decode/03.md#8-complete-tool-inventory`
- Code: `claude-code/src/tools/BriefTool/BriefTool.ts` → `BriefTool`
- Consult when: tracing where brief output originates.

#### ConfigTool
Reads and writes Claude Code configuration. Internal (`ant`) users only.
- Analysis: `analysis/01-dive-into-claude-code.md#a2-conditional-tool-availability`
- Code: `claude-code/src/tools/ConfigTool/ConfigTool.ts` → `ConfigTool`
- Consult when: explaining why a tool is missing for external users.

#### EnterPlanModeTool
Switches the session into plan mode, where writes are denied.
- Analysis: `analysis/01-dive-into-claude-code.md#51-permission-modes-and-rule-evaluation`
- Code: `claude-code/src/tools/EnterPlanModeTool/EnterPlanModeTool.ts` → `EnterPlanModeTool`
- Consult when: tracing plan-mode entry.

#### ExitPlanModeTool
Exits plan mode and requests user approval of the plan. **The registered symbol is `ExitPlanModeV2Tool`** — the directory name and the export do not match.
- Analysis: `analysis/01-dive-into-claude-code.md#51-permission-modes-and-rule-evaluation`
- Code: `claude-code/src/tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts` → `ExitPlanModeV2Tool`, `EXIT_PLAN_MODE_V2_TOOL_NAME`
- Consult when: grepping for `ExitPlanModeTool` and finding nothing.

#### EnterWorktreeTool
Creates and switches into a git worktree for isolated work.
- Analysis: `analysis/decode/07.md#6-worktree-isolation`
- Code: `claude-code/src/tools/EnterWorktreeTool/EnterWorktreeTool.ts` → `EnterWorktreeTool`
- Consult when: reasoning about filesystem isolation for subagents.

#### ExitWorktreeTool
Leaves a worktree and returns to the original working directory.
- Analysis: `analysis/decode/07.md#6-worktree-isolation`
- Code: `claude-code/src/tools/ExitWorktreeTool/ExitWorktreeTool.ts` → `ExitWorktreeTool`
- Consult when: tracing worktree teardown.

#### FileEditTool
Diff-based file editing with exact-match replacement.
- Analysis: `analysis/03-architecture-deep-dive-hasan.md#step-6-tool-calls-application--domain--infrastructure`
- Code: `claude-code/src/tools/FileEditTool/FileEditTool.ts` → `FileEditTool`
- Consult when: reasoning about edit preconditions or read-before-write rules.

#### FileReadTool
Reads files, including images, PDFs, and notebooks.
- Analysis: `analysis/decode/03.md#8-complete-tool-inventory`
- Code: `claude-code/src/tools/FileReadTool/FileReadTool.ts` → `FileReadTool`
- Consult when: tracing file-state caching or multimodal input.

#### FileWriteTool
Creates or overwrites a whole file.
- Analysis: `analysis/decode/03.md#8-complete-tool-inventory`
- Code: `claude-code/src/tools/FileWriteTool/FileWriteTool.ts` → `FileWriteTool`
- Consult when: reasoning about destructive write permissions.

#### GlobTool
File pattern matching by path.
- Analysis: `analysis/decode/03.md#8-complete-tool-inventory`
- Code: `claude-code/src/tools/GlobTool/GlobTool.ts` → `GlobTool`
- Consult when: comparing search strategies against GrepTool.

#### GrepTool
Content search via a managed ripgrep binary; read-only, concurrency-safe, paginated.
- Analysis: `analysis/03-architecture-deep-dive-hasan.md#10-deep-dive-the-grep-tool-agentic-search`
- Code: `claude-code/src/tools/GrepTool/GrepTool.ts` → `GrepTool`; `claude-code/src/utils/ripgrep.ts`
- Consult when: you want a worked example of a complete, well-defended tool.

#### LSPTool
Language-server diagnostics and navigation. Gated by `ENABLE_LSP_TOOL`.
- Analysis: `analysis/decode/08.md#10-other-key-utility-modules`
- Code: `claude-code/src/tools/LSPTool/LSPTool.ts` → `LSPTool`; `claude-code/src/services/lsp/`
- Consult when: wiring compiler feedback into the loop.

#### ListMcpResourcesTool
Lists resources exposed by connected MCP servers.
- Analysis: `analysis/01-dive-into-claude-code.md#61-four-extension-mechanisms`
- Code: `claude-code/src/tools/ListMcpResourcesTool/ListMcpResourcesTool.ts` → `ListMcpResourcesTool`
- Consult when: MCP resources are not reaching the model.

#### ReadMcpResourceTool
Reads one MCP resource by URI.
- Analysis: `analysis/01-dive-into-claude-code.md#61-four-extension-mechanisms`
- Code: `claude-code/src/tools/ReadMcpResourceTool/ReadMcpResourceTool.ts` → `ReadMcpResourceTool`
- Consult when: MCP resource content is wrong or missing.

#### MCPTool
The adapter that turns any MCP server tool into a normal `Tool` in the same flat pool.
- Analysis: `analysis/decode/08.md#8-mcptool-integration`
- Code: `claude-code/src/tools/MCPTool/MCPTool.ts` → `MCPTool`
- Consult when: understanding how external tools become indistinguishable from built-ins.

#### McpAuthTool
Handles MCP OAuth flows from inside a turn. **Built by a factory, not a const export.**
- Analysis: `analysis/decode/08.md#3-oauth-pkce-complete-flow`
- Code: `claude-code/src/tools/McpAuthTool/McpAuthTool.ts` → `createMcpAuthTool`
- Consult when: an MCP server needs authentication mid-session.

#### NotebookEditTool
Cell-level editing of Jupyter notebooks.
- Analysis: `analysis/decode/03.md#8-complete-tool-inventory`
- Code: `claude-code/src/tools/NotebookEditTool/NotebookEditTool.ts` → `NotebookEditTool`
- Consult when: handling `.ipynb` edits.

#### PowerShellTool
Windows PowerShell execution, parallel to BashTool.
- Analysis: `analysis/01-dive-into-claude-code.md#a2-conditional-tool-availability`
- Code: `claude-code/src/tools/PowerShellTool/PowerShellTool.tsx` → `PowerShellTool`; `claude-code/src/utils/powershell/`
- Consult when: working on Windows command execution.

#### REPLTool
An in-process REPL for internal users. **Implementation is dead-code-eliminated in this build** — only `constants.ts` and `primitiveTools.ts` remain.
- Analysis: `analysis/decode/03.md#8-complete-tool-inventory`
- Code: `claude-code/src/tools/REPLTool/constants.ts` → `REPL_TOOL_NAME`, `REPL_ONLY_TOOLS`
- Consult when: you find a reference to REPLTool and no implementation.

#### RemoteTriggerTool
Triggers work in a remote environment from the local session.
- Analysis: `analysis/decode/11.md#vi-remote-execution-system`
- Code: `claude-code/src/tools/RemoteTriggerTool/RemoteTriggerTool.ts` → `RemoteTriggerTool`
- Consult when: tracing local-to-remote handoff.

#### ScheduleCronTool
Scheduled/recurring agent runs. **The directory holds three separate tools**, not one.
- Analysis: `analysis/decode/03.md#8-complete-tool-inventory`
- Code: `claude-code/src/tools/ScheduleCronTool/` → `CronCreateTool`, `CronListTool`, `CronDeleteTool`
- Consult when: grepping for a single `ScheduleCronTool` export that does not exist.

#### SendMessageTool
Direct message passing between agents in a team/swarm.
- Analysis: `analysis/decode/07.md#5-team-communication-mechanism`, `analysis/03-architecture-deep-dive-hasan.md#inter-agent-communication-swarms`
- Code: `claude-code/src/tools/SendMessageTool/SendMessageTool.ts` → `SendMessageTool`
- Consult when: designing inter-agent communication.

#### SkillTool
Meta-tool that launches a skill by name and injects its instructions.
- Analysis: `analysis/01-dive-into-claude-code.md#61-four-extension-mechanisms`, `analysis/decode/08.md#7-skills-system`
- Code: `claude-code/src/tools/SkillTool/SkillTool.ts` → `SkillTool`
- Consult when: understanding how skills reach the model.

#### SleepTool
Waits for a duration, interruptibly. **Implementation is dead-code-eliminated in this build** — only `prompt.ts` remains.
- Analysis: `analysis/decode/03.md#8-complete-tool-inventory`
- Code: `claude-code/src/tools/SleepTool/prompt.ts` → `SLEEP_TOOL_NAME`, `SLEEP_TOOL_PROMPT`
- Consult when: you find a reference to SleepTool and no implementation.

#### SyntheticOutputTool
Injects synthetic tool output, used for testing and internal instrumentation.
- Analysis: none — code only
- Code: `claude-code/src/tools/SyntheticOutputTool/SyntheticOutputTool.ts` → `SyntheticOutputTool`
- Consult when: you see tool output with no matching real execution.

#### TaskCreateTool / TaskGetTool / TaskListTool / TaskUpdateTool / TaskStopTool / TaskOutputTool
The six-tool surface over the background task system (gated by `todoV2`).
- Analysis: `analysis/decode/11.md#i-deep-dive-into-the-task-system`
- Code: `claude-code/src/tools/TaskCreateTool/TaskCreateTool.ts` → `TaskCreateTool`; `claude-code/src/tools/TaskGetTool/TaskGetTool.ts` → `TaskGetTool`; `claude-code/src/tools/TaskListTool/TaskListTool.ts` → `TaskListTool`; `claude-code/src/tools/TaskUpdateTool/TaskUpdateTool.ts` → `TaskUpdateTool`; `claude-code/src/tools/TaskStopTool/TaskStopTool.ts` → `TaskStopTool`; `claude-code/src/tools/TaskOutputTool/TaskOutputTool.tsx` → `TaskOutputTool`
- Consult when: managing long-running or backgrounded work.

#### TeamCreateTool / TeamDeleteTool
Create and tear down an agent swarm (gated by `swarms`).
- Analysis: `analysis/decode/07.md#5-team-communication-mechanism`
- Code: `claude-code/src/tools/TeamCreateTool/TeamCreateTool.ts` → `TeamCreateTool`; `claude-code/src/tools/TeamDeleteTool/TeamDeleteTool.ts` → `TeamDeleteTool`
- Consult when: working on multi-agent teams.

#### TodoWriteTool
Maintains the visible task checklist for the current session.
- Analysis: `analysis/decode/11.md#i-deep-dive-into-the-task-system`
- Code: `claude-code/src/tools/TodoWriteTool/TodoWriteTool.ts` → `TodoWriteTool`
- Consult when: tracing todo-list behavior.

#### ToolSearchTool
Lets the model fetch full schemas for deferred tools by name or keyword.
- Analysis: `analysis/decode/03.md#4-toolsearch-deferred-loading-mechanism`
- Code: `claude-code/src/tools/ToolSearchTool/ToolSearchTool.ts` → `ToolSearchTool`
- Consult when: the tool block is too large for the context budget.

#### WebFetchTool
Fetches and converts a URL for the model.
- Analysis: `analysis/decode/03.md#8-complete-tool-inventory`
- Code: `claude-code/src/tools/WebFetchTool/WebFetchTool.ts` → `WebFetchTool`
- Consult when: handling external content and its injection risk.

#### WebSearchTool
Server-side web search.
- Analysis: `analysis/decode/03.md#8-complete-tool-inventory`
- Code: `claude-code/src/tools/WebSearchTool/WebSearchTool.ts` → `WebSearchTool`
- Consult when: comparing search-backed answers with local search.

#### Tool shared helpers and test doubles
Cross-tool utilities and the in-repo tool testing harness.
- Analysis: none — code only
- Code: `claude-code/src/tools/shared/`, `claude-code/src/tools/testing/`
- Consult when: writing a new tool and looking for existing helpers.

---

## Authorization and safety

### Permission system
Deny-first rule evaluation over an immutable permission context, in precedence order
deny → ask → allow, returning a tagged decision union.

- Analysis: `analysis/01-dive-into-claude-code.md#52-the-authorization-pipeline`, `analysis/decode/06.md#10-permission-type-system`
- Code: `claude-code/src/utils/permissions/permissions.ts` → `hasPermissionsToUseTool`, `checkRuleBasedPermissions`, `getAllowRules`, `getDenyRules`, `getAskRules`
- Consult when: a tool call was allowed or denied and you need to know which layer decided.

### Permission modes
The mode ladder. **No enum** — `as const` arrays plus `z.enum`. Five external modes, plus
`auto` behind a feature flag and a typecheck-only `bubble`.

- Analysis: `analysis/01-dive-into-claude-code.md#51-permission-modes-and-rule-evaluation`, `analysis/decode/06.md#1-permission-modes`, `analysis/03-architecture-deep-dive-hasan.md#permission-decision-flow`
- Code: `claude-code/src/types/permissions.ts` → `EXTERNAL_PERMISSION_MODES`, `INTERNAL_PERMISSION_MODES`, `PermissionMode`, `PermissionBehavior`, `PermissionRuleSource`
- Consult when: you need the real mode list, or are counting modes (see [Known divergences](#known-divergences)).

### Permission rule loading and persistence
Reads allow/deny/ask rules from the settings scopes and writes user decisions back.

- Analysis: `analysis/01-dive-into-claude-code.md#52-the-authorization-pipeline`
- Code: `claude-code/src/utils/permissions/permissionsLoader.ts` → `loadAllPermissionRulesFromDisk`, `addPermissionRulesToSettings`; `claude-code/src/utils/permissions/PermissionUpdate.ts` → `applyPermissionUpdate`, `persistPermissionUpdate`
- Consult when: a rule is not taking effect and you need to know which scope won.

### Permission context setup
Builds the initial permission context from CLI flags and settings, and handles mode transitions.

- Analysis: `analysis/decode/06.md#1-permission-modes`
- Code: `claude-code/src/utils/permissions/permissionSetup.ts` → `initializeToolPermissionContext`, `initialPermissionModeFromCLI`, `transitionPermissionMode`, `removeDangerousPermissions`
- Consult when: reasoning about what a session starts with.

### Auto-mode classifier
An LLM side-call that judges whether an action is safe to auto-approve, returning a
`classify_result` tool call. Gated by `TRANSCRIPT_CLASSIFIER` and the `auto` mode.

- Analysis: `analysis/01-dive-into-claude-code.md#53-auto-mode-classifier-and-hook-lifecycle`, `analysis/02-inside-claude-code-leaked-source.md#8-the-yolo-classifier-and-auto-mode-permissions`
- Code: `claude-code/src/utils/permissions/yoloClassifier.ts` → `classifyYoloAction`, `buildYoloSystemPrompt`, `YOLO_CLASSIFIER_TOOL_NAME`
- Consult when: studying model-mediated approval, or its failure modes.

### Bash permission decision (live path)
The actual decision point for shell commands: prefix extraction, wrapper stripping, rule
matching, and a speculative classifier check.

- Analysis: `analysis/decode/06.md#7-bash-permission-decision-flow`
- Code: `claude-code/src/tools/BashTool/bashPermissions.ts` → `bashToolHasPermission`, `checkCommandAndSuggestRules`, `getSimpleCommandPrefix`, `stripSafeWrappers`
- Consult when: any question about whether a command is allowed. This is the top of the decision; `bashSecurity.ts` is called from inside it — see [Known divergences](#known-divergences).

### Bash command safety validators
A battery of ~20 injection and malformed-token validators. Its two main exports carry a
`_DEPRECATED` suffix, but the module is **still called** — from `bashPermissions.ts` (which
re-aliases it), `readOnlyValidation.ts`, and `bashCommandHelpers.ts`. It is a gate inside
the decision, not the decision itself.

- Analysis: `analysis/02-inside-claude-code-leaked-source.md#7-security-2592-lines-of-bash-paranoia`, `analysis/decode/06.md#2-complete-list-of-23-security-validators`
- Code: `claude-code/src/tools/BashTool/bashSecurity.ts` → `bashCommandIsSafe_DEPRECATED`, `bashCommandIsSafeAsync_DEPRECATED`, `stripSafeHeredocSubstitutions`
- Consult when: you need the actual pattern list, or are tracing why a specific command was flagged.

### Shell parsing
The AST-level shell parser underneath every command safety decision — the largest
security-adjacent subsystem, and the one the analyses mostly skip.

- Analysis: `analysis/decode/06.md#3-dual-engine-parsing-in-depth`
- Code: `claude-code/src/utils/bash/bashParser.ts`, `claude-code/src/utils/bash/ast.ts`, `claude-code/src/utils/bash/heredoc.ts`, `claude-code/src/utils/bash/prefix.ts`
- Consult when: a command is parsed into the wrong subcommands, or you are hardening injection defenses.

### Dangerous path and pattern guards
Explicit lists of dangerous files, directories, and command patterns, plus path validation.

- Analysis: `analysis/decode/06.md#9-unicodeinjection-protection`
- Code: `claude-code/src/utils/permissions/filesystem.ts` → `DANGEROUS_FILES`, `DANGEROUS_DIRECTORIES`; `claude-code/src/utils/permissions/pathValidation.ts` → `isPathAllowed`, `isDangerousRemovalPath`; `claude-code/src/utils/permissions/dangerousPatterns.ts` → `DANGEROUS_BASH_PATTERNS`
- Consult when: extending the blocklist or auditing coverage.

### Sandboxing
Decides per-command whether to sandbox, then delegates to an **external npm package**. There
is no seatbelt or bubblewrap implementation in this tree.

- Analysis: `analysis/01-dive-into-claude-code.md#54-shell-sandboxing`, `analysis/decode/06.md#5-sandbox-implementation`
- Code: `claude-code/src/tools/BashTool/shouldUseSandbox.ts` → `shouldUseSandbox`; `claude-code/src/utils/sandbox/sandbox-adapter.ts` → `SandboxManager`, `convertToSandboxRuntimeConfig`
- Consult when: you need to know what sandboxing actually does here (mostly: calls out).

### Permission UI and handlers
The interactive approval dialog and the per-runtime strategies (interactive, coordinator,
swarm worker).

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#permission-decision-flow`
- Code: `claude-code/src/hooks/useCanUseTool.tsx` → `useCanUseTool`, `CanUseToolFn`; `claude-code/src/hooks/toolPermission/handlers/`
- Consult when: changing how approval is requested.

### Permission safety valves
Kill switches and circuit breakers that disable bypass/auto modes, and detection of
unreachable rules.

- Analysis: `analysis/decode/06.md#11-security-architecture-summary`
- Code: `claude-code/src/utils/permissions/bypassPermissionsKillswitch.ts` → `checkAndDisableBypassPermissionsIfNeeded`; `claude-code/src/utils/permissions/denialTracking.ts` → `shouldFallbackToPrompting`; `claude-code/src/utils/permissions/shadowedRuleDetection.ts` → `detectUnreachableRules`
- Consult when: a permission mode silently turns itself off.

---

## Hooks

### Hook events
27 lifecycle events spanning tool authorization, session lifecycle, user interaction,
subagent coordination, context management, and workspace changes. `as const` array plus
Zod enum — **no TypeScript enum**. The list is duplicated verbatim in two files.

- Analysis: `analysis/01-dive-into-claude-code.md#61-four-extension-mechanisms`, `analysis/02-inside-claude-code-leaked-source.md#the-hooks-system`
- Code: `claude-code/src/entrypoints/sdk/coreTypes.ts` → `HOOK_EVENTS`; `claude-code/src/entrypoints/sdk/coreSchemas.ts` → `HOOK_EVENTS`, `HookEventSchema`
- Consult when: you need the authoritative event list.

### Hook dispatcher
Matches configured hooks to an event and runs them, one `execute*Hooks` generator per
event family. **This is the hook system — not `src/hooks/`.**

- Analysis: `analysis/decode/06.md#6-hooks-system-in-depth`
- Code: `claude-code/src/utils/hooks.ts` → `getMatchingHooks`, `executePreToolHooks`, `executePostToolHooks`, `executeStopHooks`, `createBaseHookInput`
- Consult when: a configured hook does not fire.

### Hook command types and schemas
Four persistable hook kinds as a discriminated union on `type`: shell `command`, LLM
`prompt`, `agent`, and `http`; plus a non-persistable `callback` used by the SDK.

- Analysis: `analysis/01-dive-into-claude-code.md#61-four-extension-mechanisms`
- Code: `claude-code/src/schemas/hooks.ts` → `HookCommandSchema`, `HookMatcherSchema`, `HooksSchema`; `claude-code/src/types/hooks.ts` → `hookJSONOutputSchema`, `HookResult`, `HookBlockingError`
- Consult when: writing or validating hook configuration.

### Hook execution backends and registry
Per-kind executors, async hook bookkeeping, dynamic skill/frontmatter hook registration,
and SSRF protection for HTTP hooks.

- Analysis: `analysis/decode/06.md#6-hooks-system-in-depth`
- Code: `claude-code/src/utils/hooks/` → `execPromptHook.ts`, `execAgentHook.ts`, `execHttpHook.ts`, `ssrfGuard.ts`, `AsyncHookRegistry.ts`, `registerSkillHooks.ts`, `hooksConfigManager.ts`
- Consult when: adding a hook backend or debugging hook lifecycle.

---

## Context and memory

### System prompt assembly
Builds the layered system prompt from identity, rules, tool descriptions, output style, and
environment details. Section-registry pattern with cache-stability constraints.

- Analysis: `analysis/01-dive-into-claude-code.md#71-context-window-assembly`, `analysis/decode/02.md#1-complete-prompt-text-extraction`
- Code: `claude-code/src/constants/prompts.ts` → `getSystemPrompt`, `enhanceSystemPromptWithEnvDetails`; `claude-code/src/constants/systemPromptSections.ts` → `systemPromptSection`, `resolveSystemPromptSections`
- Consult when: changing what the model is told, or chasing a prompt-cache miss.

### Prompt cache strategy
Where cache breakpoints go and why the static prefix must stay byte-stable.

- Analysis: `analysis/decode/02.md#2-the-mathematics-of-cache-hit-rate`, `analysis/decode/02.md#4-cache-break-detection-system`
- Code: `claude-code/src/services/api/claude.ts` → `addCacheBreakpoints`, `getCacheControl`, `getPromptCachingEnabled`
- Consult when: cache hit rate drops after a prompt change.

### User and system context
Git status, working directory, date, and the CLAUDE.md hierarchy entry point. Memoized per
session.

- Analysis: `analysis/01-dive-into-claude-code.md#71-context-window-assembly`, `analysis/03-architecture-deep-dive-hasan.md#step-3-context-gathering-application-layer`
- Code: `claude-code/src/context.ts` → `getUserContext`, `getSystemContext`, `getGitStatus`
- Consult when: adding session-level context.

### Attachments (per-turn context injection)
The delta-attachment mechanism: only changed context is re-injected each turn, as
system-reminder messages.

- Analysis: `analysis/decode/02.md#5-agent_listing_delta-and-mcp_instructions_delta-migration-from-tool-schema-to-message-attachments`
- Code: `claude-code/src/utils/attachments.ts` → `getAttachments`, `getAttachmentMessages`, `startRelevantMemoryPrefetch`
- Consult when: something appears in context every turn that should not.

### Compaction pipeline
Five layers applied in order before each model call, escalating from cheap and lossless to
expensive and lossy. **Two of the five are absent from this build** — see [Known divergences](#known-divergences).

- Analysis: `analysis/01-dive-into-claude-code.md#73-compaction-pipeline`, `analysis/03-architecture-deep-dive-hasan.md#11-deep-dive-memory-management--context-compaction`, `analysis/decode/01.md#iv-in-depth-analysis-of-the-5-layer-compaction-pipeline`
- Code: `claude-code/src/query.ts` (call order), `claude-code/src/services/compact/`
- Consult when: context is being dropped and you need to know which layer did it.

### Auto-compaction
Threshold-triggered full compaction, with a session-memory fast path and a forked-agent
fallback.

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#layer-5-autocompact-threshold-based`, `analysis/decode/05.md#4-auto-compaction-trigger-mechanism`
- Code: `claude-code/src/services/compact/autoCompact.ts` → `autoCompactIfNeeded`, `shouldAutoCompact`, `getAutoCompactThreshold`, `AUTOCOMPACT_BUFFER_TOKENS`
- Consult when: tuning when compaction fires.

### Full conversation compaction
The forked summarization agent and the structured summary prompt it fills in.

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#layer-5-autocompact-threshold-based`, `analysis/decode/05.md#3-complete-implementation-of-the-three-tier-compression`
- Code: `claude-code/src/services/compact/compact.ts` → `compactConversation`, `buildPostCompactMessages`, `truncateHeadForPTLRetry`; `claude-code/src/services/compact/prompt.ts` → `getCompactPrompt`
- Consult when: the post-compaction summary loses something it should have kept.

### Session-memory compaction
The lightweight path: reuse the background-maintained summary file instead of an API call.

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#layer-5-autocompact-threshold-based`
- Code: `claude-code/src/services/compact/sessionMemoryCompact.ts` → `trySessionMemoryCompaction`, `shouldUseSessionMemoryCompaction`, `calculateMessagesToKeepIndex`
- Consult when: compaction happens without a visible summarization call.

### Microcompaction
Removes old tool results without invalidating the prompt cache, via `cache_edits` or,
when the cache is already cold, direct content clearing.

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#layer-3-microcompaction-per-turn`
- Code: `claude-code/src/services/compact/microCompact.ts` → `microcompactMessages`, `evaluateTimeBasedTrigger`, `pinCacheEdits`; `claude-code/src/services/compact/apiMicrocompact.ts` → `getAPIContextManagement`
- Consult when: old tool output vanishes mid-session.

### Post-compaction cleanup
Invalidates the caches that compaction makes stale — tool registrations, memory files,
classifier approvals.

- Analysis: `analysis/decode/05.md#8-post-compaction-cleanup-postcompactcleanup`
- Code: `claude-code/src/services/compact/postCompactCleanup.ts` → `runPostCompactCleanup`
- Consult when: stale state survives a compaction.

### Token counting
Hybrid counting: API `countTokens` where available, heuristic estimate otherwise.

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#how-tokens-are-counted`, `analysis/decode/05.md#2-precise-token-counting-implementation`
- Code: `claude-code/src/services/tokenEstimation.ts`, `claude-code/src/utils/tokens.ts`
- Consult when: token numbers disagree with the API.

### CLAUDE.md hierarchy
Discovery and loading of instruction files up and down the tree, with conditional and
path-scoped rules. **Not `src/memdir/`.**

- Analysis: `analysis/01-dive-into-claude-code.md#72-claudemd-hierarchy-and-auto-memory`
- Code: `claude-code/src/utils/claudemd.ts` → `getMemoryFiles`, `getClaudeMds`, `processMemoryFile`, `MAX_MEMORY_CHARACTER_COUNT`
- Consult when: a CLAUDE.md rule is not being applied.

### Auto memory (MEMORY.md)
The separate file-based long-term memory subsystem, with its own entrypoint file, scan,
relevance search, and freshness model.

- Analysis: `analysis/decode/11.md#x-memdir-memory-system`, `analysis/02-inside-claude-code-leaked-source.md#6-the-six-layer-memory-system`
- Code: `claude-code/src/memdir/memdir.ts` → `loadMemoryPrompt`, `buildMemoryPrompt`, `ENTRYPOINT_NAME`; `claude-code/src/memdir/findRelevantMemories.ts`, `claude-code/src/memdir/memoryScan.ts`
- Consult when: designing durable cross-session memory.

### Session memory extraction
A background forked agent that maintains a structured per-session summary file between turns.

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#background-session-memory-extraction`
- Code: `claude-code/src/services/SessionMemory/sessionMemory.ts` → `initSessionMemory`, `shouldExtractMemory`; `claude-code/src/services/extractMemories/extractMemories.ts` → `executeExtractMemories`
- Consult when: you want compaction to be cheap because a summary already exists.

### Team memory sync
Shares memory across a team, with secret scanning before anything leaves the machine.

- Analysis: `analysis/decode/11.md#x-memdir-memory-system`
- Code: `claude-code/src/services/teamMemorySync/` → `syncTeamMemory`, `pullTeamMemory`, `pushTeamMemory`; `claude-code/src/services/teamMemorySync/secretScanner.ts` → `scanForSecrets`
- Consult when: memory must cross a trust boundary.

### Context usage analysis
Powers `/context`: attributes the context window to prompt sections, tools, and history.

- Analysis: `analysis/decode/02.md#9-complete-data-flow-overview`
- Code: `claude-code/src/utils/analyzeContext.ts` → `analyzeContextUsage`, `countToolDefinitionTokens`
- Consult when: you need to know what is actually eating the window.

---

## Persistence

### Session transcripts
Append-oriented JSONL per session, under a per-project directory. Compaction appends a
boundary and summary rather than rewriting history.

- Analysis: `analysis/01-dive-into-claude-code.md#91-transcript-model`, `analysis/03-architecture-deep-dive-hasan.md#step-9-persistence-infrastructure-layer`
- Code: `claude-code/src/utils/sessionStorage.ts` → `recordTranscript`, `getTranscriptPath`, `loadTranscriptFromFile`, `flushSessionStorage`
- Consult when: reading or writing conversation history on disk.

### Resume and fork
Rehydrating a session from its transcript, and branching a conversation at a point.
Permissions are deliberately **not** restored.

- Analysis: `analysis/01-dive-into-claude-code.md#92-resume-fork-and-not-restoring-permissions`
- Code: `claude-code/src/utils/sessionRestore.ts` → `processResumedConversation`, `restoreSessionStateFromLog`; `claude-code/src/utils/conversationRecovery.ts` → `loadConversationForResume`
- Consult when: implementing resume, or explaining why permissions reset.

### Sidechain transcripts
Subagent conversations recorded in their own files, flagged `isSidechain`, excluded from
main-session stats.

- Analysis: `analysis/01-dive-into-claude-code.md#83-sidechain-transcripts`
- Code: `claude-code/src/utils/sessionStorage.ts` → `recordSidechainTranscript`, `getAgentTranscript`, `loadSubagentTranscripts`
- Consult when: you need a subagent's full history, which the parent never sees.

### Prompt input history
The shell-style `↑` history of what the user typed. **Not the conversation transcript.**

- Analysis: `analysis/decode/11.md#ix-cli--io-system`
- Code: `claude-code/src/history.ts` → `addToHistory`, `getHistory`, `parseReferences`
- Consult when: an analysis cites `history.ts` as conversation persistence.

### Application state store
A single `Store<AppState>` with React bindings, selectors, and a side-effect handler.

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#appstate-srcstateappstatestorets`, `analysis/decode/11.md#ii-state-management-system`
- Code: `claude-code/src/state/AppStateStore.ts` → `AppState`, `getDefaultAppState`; `claude-code/src/state/store.ts` → `createStore`; `claude-code/src/state/selectors.ts`
- Consult when: adding reactive UI state.

### Bootstrap global state
A mutable module-level container of ~150 session properties, read from everywhere.

- Analysis: `analysis/decode/11.md#ii-state-management-system`
- Code: `claude-code/src/bootstrap/state.ts`
- Consult when: you find global reads with no obvious owner.

### Settings
Layered settings resolution: user, project, local, remote-managed, and enterprise policy.

- Analysis: `analysis/decode/11.md#iv-utils-directory-classification`
- Code: `claude-code/src/utils/settings/`, `claude-code/src/services/remoteManagedSettings/`, `claude-code/src/services/settingsSync/`
- Consult when: a setting is not taking effect and you need the precedence order.

### Migrations
One-shot startup migrations of settings and model selections.

- Analysis: none — code only
- Code: `claude-code/src/migrations/`
- Consult when: an old config must keep working.

---

## Extensibility

### MCP client and transports
Connects to MCP servers over several transports and folds their tools, resources, and
prompts into the normal pool. Transports are constructed inline — **there is no
`transports/` directory**.

- Analysis: `analysis/01-dive-into-claude-code.md#61-four-extension-mechanisms`, `analysis/decode/08.md#1-mcp-protocol-implementation-8-transport-types`
- Code: `claude-code/src/services/mcp/client.ts` → `connectToServer`, `getMcpToolsCommandsAndResources`, `callMCPToolWithUrlElicitationRetry`; `claude-code/src/services/mcp/InProcessTransport.ts`, `claude-code/src/services/mcp/SdkControlTransport.ts`
- Consult when: an MCP server will not connect or its tools do not appear.

### MCP configuration
Merges MCP server definitions from project, user, local, enterprise, plugin, and claude.ai scopes.

- Analysis: `analysis/decode/08.md#5-mcp-configuration-system-configts`
- Code: `claude-code/src/services/mcp/config.ts` → `getAllMcpConfigs`, `addMcpConfig`, `filterMcpServersByPolicy`; `claude-code/src/services/mcp/types.ts` → `McpServerConfigSchema`
- Consult when: the wrong MCP server set is loaded.

### MCP authentication
OAuth (PKCE) and cross-app access for MCP servers.

- Analysis: `analysis/decode/08.md#3-oauth-pkce-complete-flow`, `analysis/decode/08.md#4-mcp-oauth-xaa-cross-app-access`
- Code: `claude-code/src/services/mcp/auth.ts` → `performMCPOAuthFlow`, `ClaudeAuthProvider`; `claude-code/src/services/mcp/xaa.ts`
- Consult when: an MCP server needs credentials.

### Plugins
Manifest-validated packages that contribute commands, agents, skills, hooks, MCP and LSP
servers, and output styles. **Code lives in `src/utils/plugins/`, not `src/plugins/`.**

- Analysis: `analysis/01-dive-into-claude-code.md#61-four-extension-mechanisms`, `analysis/decode/08.md#6-plugin-architecture`
- Code: `claude-code/src/utils/plugins/pluginLoader.ts` → `loadAllPlugins`, `loadPluginManifest`; `claude-code/src/utils/plugins/schemas.ts` → `PluginManifestSchema`, `PluginMarketplaceSchema`
- Consult when: a plugin's contribution does not register.

### Plugin marketplaces and lifecycle
Discovery, install, enable/disable, update, blocklist, and dependency resolution.

- Analysis: `analysis/decode/08.md#6-plugin-architecture`
- Code: `claude-code/src/utils/plugins/marketplaceManager.ts` → `getMarketplace`, `refreshAllMarketplaces`; `claude-code/src/services/plugins/pluginOperations.ts` → `installPluginOp`, `uninstallPluginOp`
- Consult when: working on plugin distribution.

### Built-in plugins
The small in-binary plugin registry.

- Analysis: `analysis/decode/08.md#6-plugin-architecture`
- Code: `claude-code/src/plugins/builtinPlugins.ts` → `registerBuiltinPlugin`, `getBuiltinPlugins`, `BUILTIN_MARKETPLACE_NAME`
- Consult when: distinguishing bundled from installed plugins.

### Skills
`SKILL.md` files with YAML frontmatter, loaded by the same loader as markdown slash
commands. Invoked through `SkillTool`.

- Analysis: `analysis/01-dive-into-claude-code.md#61-four-extension-mechanisms`, `analysis/decode/08.md#7-skills-system`
- Code: `claude-code/src/skills/loadSkillsDir.ts` → `parseSkillFrontmatterFields`, `getSkillDirCommands`, `getSkillsPath`; `claude-code/src/skills/bundledSkills.ts` → `registerBundledSkill`
- Consult when: authoring skills or debugging skill discovery.

### Frontmatter parsing
The shared YAML-frontmatter engine behind skills, commands, and agent definitions.

- Analysis: `analysis/decode/08.md#7-skills-system`
- Code: `claude-code/src/utils/frontmatterParser.ts` → `parseFrontmatter`, `FRONTMATTER_REGEX`, `parseShellFrontmatter`
- Consult when: a frontmatter field is silently ignored.

### Slash command registry
Merges commands from built-ins, markdown directories, plugins, skills, and MCP, then filters
by mode and availability.

- Analysis: `analysis/decode/04.md#2-command-registration-mechanism--merging-strategy-for-6-sources`, `analysis/decode/04.md#3-two-layer-filtering-mechanism`
- Code: `claude-code/src/commands.ts` → `getCommands`, `findCommand`, `INTERNAL_ONLY_COMMANDS`, `REMOTE_SAFE_COMMANDS`, `BRIDGE_SAFE_COMMANDS`
- Consult when: a command is missing in a particular mode.

### Command type contract
The tagged-union module shape every command exports: direct, JSX, or prompt.

- Analysis: `analysis/decode/04.md#1-command-type-system`
- Code: `claude-code/src/types/command.ts` → `Command`, `getCommandName`, `isCommandEnabled`, `CommandAvailability`
- Consult when: writing a new slash command.

### Output styles
Swappable response-formatting blocks injected into the system prompt.

- Analysis: `analysis/decode/02.md#7-prompt-priority-routing-buildeffectivesystemprompt`
- Code: `claude-code/src/constants/outputStyles.ts` → `OUTPUT_STYLE_CONFIG`, `getAllOutputStyles`, `DEFAULT_OUTPUT_STYLE_NAME`; `claude-code/src/outputStyles/loadOutputStylesDir.ts` → `getOutputStyleDirStyles`
- Consult when: changing how responses are formatted globally.

### Feature flags
Two layers: build-time `feature()` from `bun:bundle`, which dead-code-eliminates whole
subsystems, and runtime GrowthBook flags for A/B and kill-switches.

- Analysis: `analysis/decode/10.md#i-complete-categorized-list-of-88-build-time-feature-flags`, `analysis/decode/10.md#v-growthbook-integration-deep-dive`
- Code: `bun:bundle` `feature()` imported directly in 197 files — start at `claude-code/src/tools.ts` and `claude-code/src/query.ts`; `claude-code/src/services/analytics/`
- Consult when: a symbol is referenced but its module does not exist (it was stripped at build time).

---

## Subagents and multi-agent

### Agent tool and delegation
Dispatch to a subagent along three axes: routing (teammate), isolation (remote, worktree),
and lifecycle (async, sync).

- Analysis: `analysis/01-dive-into-claude-code.md#81-the-agent-tool-and-delegation-criteria`, `analysis/decode/07.md#2-agenttools-6-operating-modes`
- Code: `claude-code/src/tools/AgentTool/AgentTool.tsx` → `AgentTool`; `claude-code/src/tools/AgentTool/agentToolUtils.ts`
- Consult when: choosing between delegation modes.

### Subagent execution and isolation
Runs a subagent with a fresh message list and its own tool pool; only summary text returns
to the parent.

- Analysis: `analysis/01-dive-into-claude-code.md#82-isolation-architecture`
- Code: `claude-code/src/tools/AgentTool/runAgent.ts` → `runAgent`, `filterIncompleteToolCalls`; `claude-code/src/tools/AgentTool/resumeAgent.ts` → `resumeAgentBackground`
- Consult when: reasoning about what a subagent can and cannot see.

### Agent definitions
Frontmatter-based agent types from built-ins, user directories, and plugins. **`src/agents/`
does not exist.**

- Analysis: `analysis/decode/07.md#10-built-in-agent-registry`
- Code: `claude-code/src/tools/AgentTool/loadAgentsDir.ts` → `getAgentDefinitionsWithOverrides`, `parseAgentFromMarkdown`, `AgentDefinition`; `claude-code/src/tools/AgentTool/builtInAgents.ts` → `getBuiltInAgents`
- Consult when: adding an agent type or listing the built-ins.

### Fork subagent
Forks the parent conversation into a child that keeps the prompt cache warm.

- Analysis: `analysis/decode/07.md#3-fork-agents-cache-innovation`
- Code: `claude-code/src/tools/AgentTool/forkSubagent.ts` → `buildForkedMessages`, `FORK_SUBAGENT_TYPE`, `isInForkChild`
- Consult when: you want delegation without paying to rebuild context.

### Agent-scoped memory
Per-agent memory directories, separate from session and project memory.

- Analysis: `analysis/decode/07.md#9-agent-memory-system`
- Code: `claude-code/src/tools/AgentTool/agentMemory.ts` → `getAgentMemoryDir`, `loadAgentMemoryPrompt`
- Consult when: a subagent needs durable state of its own.

### Teams and swarms
Multi-agent teams coordinated through a file-locked shared task list plus a Unix-domain-socket
inbox.

- Analysis: `analysis/decode/07.md#5-team-communication-mechanism`, `analysis/03-architecture-deep-dive-hasan.md#inter-agent-communication-swarms`
- Code: `claude-code/src/utils/swarm/`, `claude-code/src/tasks/InProcessTeammateTask/`, `claude-code/src/tools/SendMessageTool/`
- Consult when: designing agent-to-agent coordination.

### Subagent summarization
Condenses a subagent's work into the summary the parent receives.

- Analysis: `analysis/01-dive-into-claude-code.md#82-isolation-architecture`
- Code: `claude-code/src/services/AgentSummary/agentSummary.ts` → `startAgentSummarization`; `claude-code/src/services/toolUseSummary/`
- Consult when: the parent gets too little or too much back.

---

## Slash commands

86 directories under `src/commands/`. **18 of them are disabled stubs** — their
`index.js` exports `{ isEnabled: () => false, isHidden: true, name: 'stub' }`, meaning the
real implementation was stripped from the public build. The 68 live commands follow, then
the stubs.

- Analysis: `analysis/decode/04.md#5-complete-command-list`, `analysis/decode/04.md#4-complete-analysis-of-internal-commands`
- Code: `claude-code/src/commands/` → one directory per command; registry in `claude-code/src/commands.ts`
- Consult when: you need to know which command exists before grepping for it.

#### /add-dir
Adds another working directory to the session's allowed roots.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/add-dir/index.ts`
- Consult when: the agent must read or write outside the original cwd.

#### /advisor
Configures the advisor model used for side judgements.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/advisor.ts`
- Consult when: tuning which model backs advisory calls.

#### /agents
Manage agent definitions — list, create, edit the frontmatter-based agent registry.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/agents/index.ts`
- Consult when: adding or inspecting custom subagent types.

#### /branch
Branches the conversation at the current point into a new session.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/branch/index.ts`
- Consult when: exploring an alternative without losing the current thread.

#### /bridge
Connects this terminal so a remote client can drive it.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/bridge/index.ts`
- Consult when: working on remote-control or phone/web driving of a local REPL.

#### /bridge-kick
Injects bridge failure states for manual recovery testing.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/bridge-kick.ts`
- Consult when: testing remote-bridge recovery.

#### /brief
Toggles brief-only output mode.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/brief.ts`
- Consult when: suppressing verbose responses.

#### /btw
Injects an out-of-band aside into the conversation without a full turn.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/btw/index.ts`
- Consult when: adding side context mid-task.

#### /chrome
Settings for the Claude-in-Chrome browser integration.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/chrome/index.ts`
- Consult when: working on browser automation.

#### /clear
Clears conversation history and frees the context window.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/clear/index.ts`
- Consult when: you need the hard reset path rather than compaction.

#### /color
Sets the prompt bar color for the session.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/color/index.ts`
- Consult when: theming the REPL chrome.

#### /commit-push-pr
Commits, pushes, and opens a pull request in one step.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/commit-push-pr.ts`
- Consult when: the end-to-end git workflow.

#### /commit
Creates a git commit from the current changes.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/commit.ts`
- Consult when: the built-in git workflow.

#### /compact
Manually triggers conversation compaction.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/compact/index.ts`
- Consult when: forcing compaction rather than waiting for the threshold.

#### /config
Opens the configuration panel.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/config/index.ts`
- Consult when: changing settings interactively.

#### /context
Visualizes current context usage as a colored grid.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/context/index.ts`
- Consult when: diagnosing what is consuming the context window.

#### /copy
Copies conversation content to the clipboard.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/copy/index.ts`
- Consult when: exporting a snippet.

#### /cost
Shows total cost and duration of the current session.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/cost/index.ts`
- Consult when: reporting spend.

#### /createMovedToPluginCommand
Factory that generates a shim command for functionality moved into a plugin.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/createMovedToPluginCommand.ts`
- Consult when: a built-in command now redirects to a plugin.

#### /desktop
Continues the current session in Claude Desktop.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/desktop/index.ts`
- Consult when: handing a session between surfaces.

#### /diff
Views uncommitted changes and per-turn diffs.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/diff/index.ts`
- Consult when: reviewing what the agent changed.

#### /doctor
Diagnoses and verifies the installation and settings.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/doctor/index.ts`
- Consult when: debugging a broken environment.

#### /effort
Sets the effort level for model usage.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/effort/index.ts`
- Consult when: trading latency for reasoning depth.

#### /exit
Exits the REPL.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/exit/index.ts`
- Consult when: tracing shutdown.

#### /export
Exports the conversation to a file or clipboard.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/export/index.ts`
- Consult when: archiving a session.

#### /extra-usage
Configures extra usage so work continues past a limit.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/extra-usage/index.ts`
- Consult when: handling rate limits.

#### /fast
Toggles fast mode.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/fast/index.ts`
- Consult when: trading depth for speed.

#### /feedback
Submits feedback about Claude Code.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/feedback/index.ts`
- Consult when: the in-product feedback path.

#### /files
Lists all files currently in context.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/files/index.ts`
- Consult when: checking what the model can actually see.

#### /heapdump
Dumps the JS heap to disk.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/heapdump/index.ts`
- Consult when: investigating a memory leak.

#### /help
Shows help and available commands.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/help/index.ts`
- Consult when: discovering the command surface.

#### /hooks
Views hook configurations for tool events.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/hooks/index.ts`
- Consult when: verifying which hooks are registered.

#### /ide
Manages IDE integrations and shows status.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/ide/index.ts`
- Consult when: working on editor integration.

#### /init-verifiers
Sets up verifier hooks for the project.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/init-verifiers.ts`
- Consult when: wiring automated verification.

#### /init
Bootstraps a CLAUDE.md for the current repository.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/init.ts`
- Consult when: generating project instructions.

#### /insights
Shows usage and behavior insights.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/insights.ts`
- Consult when: analytics-facing features.

#### /install-github-app
Sets up Claude GitHub Actions for a repository.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/install-github-app/index.ts`
- Consult when: wiring CI-side automation.

#### /install-slack-app
Installs the Claude Slack app.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/install-slack-app/index.ts`
- Consult when: wiring the Slack surface.

#### /install
Installs the Claude Code native build.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/install.tsx`
- Consult when: distribution and native install.

#### /keybindings
Opens or creates the keybindings configuration file.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/keybindings/index.ts`
- Consult when: customizing input handling.

#### /login
Authenticates the CLI.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/login/index.ts`
- Consult when: debugging auth.

#### /logout
Signs out from the Anthropic account.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/logout/index.ts`
- Consult when: clearing credentials.

#### /mcp
Manages MCP servers — add, remove, inspect, reconnect.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/mcp/index.ts`
- Consult when: an MCP server is misconfigured.

#### /memory
Edits Claude memory files.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/memory/index.ts`
- Consult when: curating CLAUDE.md or MEMORY.md by hand.

#### /mobile
Shows a QR code to download the mobile app.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/mobile/index.ts`
- Consult when: cross-surface onboarding.

#### /model
Selects the model for the session.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/model/index.ts`
- Consult when: switching models mid-session.

#### /output-style
Deprecated shim; output style now lives in /config.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/output-style/index.ts`
- Consult when: you find references to this command.

#### /passes
Manages usage passes.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/passes/index.ts`
- Consult when: billing-adjacent flows.

#### /permissions
Manages allow and deny tool permission rules.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/permissions/index.ts`
- Consult when: editing rules without hand-writing settings.json.

#### /plan
Enables plan mode or views the current session plan.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/plan/index.ts`
- Consult when: entering the read-only planning workflow.

#### /plugin
Manages Claude Code plugins — install, enable, update.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/plugin/index.tsx`
- Consult when: plugin lifecycle work.

#### /pr_comments
Fetches comments from a GitHub pull request.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/pr_comments/index.ts`
- Consult when: code-review automation.

#### /privacy-settings
Views and updates privacy settings.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/privacy-settings/index.ts`
- Consult when: data-handling questions.

#### /rate-limit-options
Shows options when a rate limit is reached.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/rate-limit-options/index.ts`
- Consult when: limit-handling UX.

#### /release-notes
Views release notes.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/release-notes/index.ts`
- Consult when: version history.

#### /reload-plugins
Activates pending plugin changes in the current session.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/reload-plugins/index.ts`
- Consult when: plugin hot reload.

#### /remote-env
Configures the default remote environment for teleport sessions.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/remote-env/index.ts`
- Consult when: remote execution setup.

#### /remote-setup
Sets up remote execution.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/remote-setup/index.ts`
- Consult when: remote execution setup.

#### /rename
Renames the current conversation.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/rename/index.ts`
- Consult when: session metadata.

#### /resume
Resumes a previous conversation from its transcript.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/resume/index.ts`
- Consult when: the resume path and what it restores.

#### /review
Reviews a pull request. The command exists in two places: a top-level module for the local
path, and a directory holding the remote "ultrareview" variant and its dialogs.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/review.ts`; `claude-code/src/commands/review/reviewRemote.ts`
- Consult when: review automation, and as the example of a command split across a module and a directory.

#### /rewind
Restores the code and/or conversation to a previous point.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/rewind/index.ts`
- Consult when: undo semantics across both code and history.

#### /sandbox-toggle
Toggles shell sandboxing for the session.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/sandbox-toggle/index.ts`
- Consult when: sandbox behavior.

#### /security-review
Runs a security review of pending changes on the current branch.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/security-review.ts`
- Consult when: security-focused review automation.

#### /session
Shows the remote session URL and QR code.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/session/index.ts`
- Consult when: cross-device session handoff.

#### /skills
Lists available skills.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/skills/index.ts`
- Consult when: checking skill discovery.

#### /stats
Shows usage statistics and activity.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/stats/index.ts`
- Consult when: usage reporting.

#### /status
Shows current session status.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/status/index.ts`
- Consult when: a quick health check.

#### /statusline
Sets up the status line UI.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/statusline.tsx`
- Consult when: customizing the status bar.

#### /stickers
Orders Claude Code stickers.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/stickers/index.ts`
- Consult when: the easter-egg surface.

#### /tag
Toggles a searchable tag on the current session.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/tag/index.ts`
- Consult when: organizing sessions.

#### /tasks
Lists and manages background tasks.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/tasks/index.ts`
- Consult when: inspecting backgrounded work.

#### /terminalSetup
Configures terminal integration.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/terminalSetup/index.ts`
- Consult when: terminal key handling and setup.

#### /theme
Changes the UI theme.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/theme/index.ts`
- Consult when: theming.

#### /thinkback
The year-in-review retrospective view.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/thinkback/index.ts`
- Consult when: the easter-egg surface.

#### /thinkback-play
Plays the thinkback animation.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/thinkback-play/index.ts`
- Consult when: the easter-egg surface.

#### /ultraplan
Refines a plan using the remote deep-thinking planner.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/ultraplan.tsx`
- Consult when: studying long-horizon remote planning.

#### /upgrade
Upgrades to a higher plan tier.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/upgrade/index.ts`
- Consult when: billing flows.

#### /usage
Shows plan usage limits.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/usage/index.ts`
- Consult when: limit reporting.

#### /version
Prints the version.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/version.ts`
- Consult when: the fast-path startup that returns without heavy imports.

#### /vim
Toggles between Vim and Normal editing modes.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/vim/index.ts`
- Consult when: input-mode handling.

#### /voice
Toggles voice mode.
- Analysis: `analysis/decode/04.md#5-complete-command-list`
- Code: `claude-code/src/commands/voice/index.ts`
- Consult when: speech input/output.

#### Disabled command stubs
These 18 directories exist but their implementations were removed from the public build;
each contains only a stub `index.js` that reports itself disabled and hidden.
`/ant-trace`, `/autofix-pr`, `/backfill-sessions`, `/break-cache`, `/bughunter`, `/ctx_viz`, `/debug-tool-call`, `/env`, `/good-claude`, `/issue`, `/mock-limits`, `/oauth-refresh`, `/onboarding`, `/perf-issue`, `/reset-limits`, `/share`, `/summary`, `/teleport`
- Analysis: `analysis/decode/04.md#4-complete-analysis-of-internal-commands`
- Code: `claude-code/src/commands/<name>/index.js` → the literal stub object
- Consult when: a command name appears in documentation or telemetry but has no implementation here.

---

## UI and terminal rendering

### Ink renderer (vendored fork)
A forked, in-tree copy of the Ink React-for-terminal renderer: reconciler, Yoga layout,
ANSI output, keypress and mouse handling. 96 files.

- Analysis: `analysis/decode/09.md#2-custom-ink-rendering-engine`, `analysis/03-architecture-deep-dive-hasan.md#6-dependency--layer-diagram`
- Code: `claude-code/src/ink/` → `ink.tsx`, `reconciler.ts`, `dom.ts`, `layout/`, `events/`
- Consult when: the terminal renders incorrectly, or you need to know why React works in a terminal here.

### REPL screen
The main interactive screen and the largest component in the tree; it wires input, message
list, status line, dialogs, and the query call together.

- Analysis: `analysis/decode/09.md#1-repltsx-god-component-deep-dive`
- Code: `claude-code/src/screens/REPL.tsx` → `REPL`
- Consult when: tracing a user keystroke to a `query()` call.

### Screens
The three top-level full-screen views.

- Analysis: `analysis/decode/09.md#3-component-classification-system`
- Code: `claude-code/src/screens/` → `REPL.tsx`, `ResumeConversation.tsx`, `Doctor.tsx`
- Consult when: adding a new full-screen mode.

### Components
389 React/Ink components: message rendering, markdown, diffs, permission dialogs, spinners,
status line, virtualized lists.

- Analysis: `analysis/decode/09.md#3-component-classification-system`, `analysis/decode/09.md#5-design-system`
- Code: `claude-code/src/components/` → `App.tsx`, `Messages.tsx`, `Message.tsx`, `PromptInput/`, `StatusLine.tsx`, `Markdown.tsx`
- Consult when: changing what the user sees.

### React hooks (UI)
83 `use*` hooks for input buffers, keybindings, IDE integration, notifications, and
suggestions. **This directory is not the hook-event system.**

- Analysis: `analysis/decode/09.md#4-performance-optimization-techniques`
- Code: `claude-code/src/hooks/` → `useCanUseTool.tsx`, `useCommandQueue.ts`, `useInputBuffer.ts`, `toolPermission/`, `notifs/`
- Consult when: working on terminal UI behavior — and whenever an analysis says "hooks" and means this.

### Keybindings
User-customizable key bindings with a parser, resolver, and matcher over a JSON config.

- Analysis: `analysis/decode/11.md#vii-keybinding-system`
- Code: `claude-code/src/keybindings/` → `parser.ts`, `resolver.ts`, `match.ts`, `defaultBindings.ts`, `loadUserBindings.ts`
- Consult when: a shortcut does not fire.

### Vim mode
A small vim editing engine for the prompt input: motions, operators, text objects, state
transitions.

- Analysis: `analysis/decode/11.md#v-vim-mode-state-machine`
- Code: `claude-code/src/vim/` → `motions.ts`, `operators.ts`, `textObjects.ts`, `transitions.ts`
- Consult when: modal editing behaves oddly.

### React context providers
UI-level React contexts. **Not context-window assembly.**

- Analysis: none — code only
- Code: `claude-code/src/context/`
- Consult when: an analysis says `src/context/` and means the context window (it does not).

---

## Services and infrastructure

### API client and providers
Anthropic API access across direct, Bedrock, Vertex, and Foundry backends.

- Analysis: `analysis/decode/08.md#2-api-client-deep-dive`
- Code: `claude-code/src/services/api/` → `claude.ts`, `withRetry.ts`
- Consult when: changing provider routing or request construction.

### Analytics and telemetry
Event logging, Datadog, OpenTelemetry spans, and GrowthBook runtime flags.

- Analysis: `analysis/decode/10.md#v-growthbook-integration-deep-dive`, `analysis/02-inside-claude-code-leaked-source.md#sentiment-detection-and-telemetry`
- Code: `claude-code/src/services/analytics/`
- Consult when: adding instrumentation or auditing what is reported.

### LSP integration
Language-server client and diagnostic registry feeding compiler feedback back to the model.

- Analysis: `analysis/decode/08.md#10-other-key-utility-modules`
- Code: `claude-code/src/services/lsp/` → `LSPClient.ts`, `LSPServerManager.ts`, `LSPDiagnosticRegistry.ts`, `passiveFeedback.ts`
- Consult when: wiring static analysis into the loop.

### Background "dreaming"
An idle-time consolidation agent that runs between sessions.

- Analysis: `analysis/02-inside-claude-code-leaked-source.md#autodream-background-memory-consolidation`
- Code: `claude-code/src/services/autoDream/` → `initAutoDream`, `executeAutoDream`; `claude-code/src/tasks/DreamTask/`
- Consult when: studying offline memory consolidation.

### Other services
Prompt suggestions, magic docs, policy limits, tips, OAuth, x402 payments, agent summaries.

- Analysis: `analysis/decode/08.md#10-other-key-utility-modules`
- Code: `claude-code/src/services/` → `PromptSuggestion/`, `MagicDocs/`, `policyLimits/`, `tips/`, `oauth/`, `AgentSummary/`
- Consult when: sweeping for a capability you cannot place.

### Remote sessions
Drives a session from the cloud over WebSocket, with a permission bridge back to the user.

- Analysis: `analysis/decode/11.md#vi-remote-execution-system`
- Code: `claude-code/src/remote/` → `RemoteSessionManager.ts`, `SessionsWebSocket.ts`, `remotePermissionBridge.ts`
- Consult when: working on cloud-executed sessions.

### REPL bridge
Lets a phone or web client drive this local REPL, with device trust and a shared secret.

- Analysis: `analysis/decode/07.md#7-the-true-purpose-of-the-bridge-module`
- Code: `claude-code/src/bridge/` → `replBridge.ts`, `bridgeMain.ts`, `sessionRunner.ts`, `trustedDevice.ts`
- Consult when: a remote surface needs to drive the local process.

### Local server
Direct-connect session management, letting another client attach to this process. Three files
in this build; the browser/PTY server described in some analyses is not present here.

- Analysis: none — code only
- Code: `claude-code/src/server/` → `createDirectConnectSession.ts`, `directConnectManager.ts`, `types.ts`
- Consult when: exposing the CLI over a local socket.

### Upstream proxy
Routes API traffic through a sandbox-side proxy for containerized runtimes.

- Analysis: `analysis/decode/11.md#viii-upstream-proxy-system`
- Code: `claude-code/src/upstreamproxy/` → `upstreamproxy.ts`, `relay.ts`
- Consult when: running inside a network-restricted sandbox.

### Native TypeScript reimplementations
Pure-TS replacements for native/WASM dependencies so the binary ships with no native addons.

- Analysis: none — code only
- Code: `claude-code/src/native-ts/` → `yoga-layout/`, `color-diff/`, `file-index/`
- Consult when: explaining how a single-file binary avoids native modules.

### Vendored native sources
Source stubs for the native helpers the published binary embeds. **`vendor/` here holds
`audio-capture-src`, `image-processor-src`, `modifiers-napi-src` and `url-handler-src` — it
does NOT contain the ripgrep binaries**, which the analyses describe at
`vendor/ripgrep/{arch}-{platform}/rg`. Binary resolution logic still exists in
`src/utils/ripgrep.ts`, but the binaries are not in this extraction.

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#ripgrep-binary-resolution-srcutilsripgrepts`
- Code: `claude-code/vendor/`; `claude-code/src/utils/ripgrep.ts`
- Consult when: search fails on a platform, or you are looking for the vendored `rg` and cannot find it.

### Voice mode
Speech-to-text and text-to-speech, behind a feature gate.

- Analysis: `analysis/decode/11.md#iv-utils-directory-classification`
- Code: `claude-code/src/voice/voiceModeEnabled.ts` → `isVoiceModeEnabled`; `claude-code/src/services/voiceStreamSTT.ts`
- Consult when: working on speech surfaces.

### Buddy (companion sprite)
An animated terminal companion — an easter egg, feature-gated off by default.

- Analysis: `analysis/02-inside-claude-code-leaked-source.md#the-buddy-system-tamagotchi-for-developers`, `analysis/decode/10.md#iii-complete-anatomy-of-the-buddy-digital-pet`
- Code: `claude-code/src/buddy/` → `companion.ts`, `CompanionSprite.tsx`, `sprites.ts`
- Consult when: you are cataloguing hidden features.

### Shared utilities
31 subdirectories under `src/utils/` — bash parsing, permissions, plugins, MCP, memory,
messages, model selection, sandbox, settings, shell, git, github, telemetry, todo, swarm,
task, teleport, secure storage, background work, and more.

- Analysis: `analysis/decode/11.md#iv-utils-directory-classification`
- Code: `claude-code/src/utils/`
- Consult when: a helper is clearly shared but you cannot place it; start from the decode index rather than grepping.

### Shared types, schemas, and constants
Cross-cutting TypeScript types, Zod validation schemas, and constant tables including the
prompt text and tool limits.

- Analysis: `analysis/decode/10.md#viii-summary-of-all-21-files-in-constants`
- Code: `claude-code/src/types/`, `claude-code/src/schemas/`, `claude-code/src/constants/`
- Consult when: you need the authoritative shape or value of something.

### SDK surface
The public typed surface for embedding Claude Code, including the hook event and schema
definitions.

- Analysis: none — code only
- Code: `claude-code/src/entrypoints/sdk/` → `coreTypes.ts`, `coreSchemas.ts`; `claude-code/sdk-tools.d.ts`
- Consult when: building against Claude Code rather than inside it.

### Assistant session history (remote)
Paging over remote/web session history. **Not turn execution.**

- Analysis: none — code only
- Code: `claude-code/src/assistant/sessionHistory.ts` → `fetchLatestEvents`, `fetchOlderEvents`
- Consult when: an analysis places turn logic under `src/assistant/` (it is not there).

### Unclear / placeholder
A single stub hook whose arguments are unused; apparently a gutted or placeholder feature.

- Analysis: none — code only
- Code: `claude-code/src/moreright/useMoreRight.tsx`
- Consult when: you encounter it and wonder whether it matters. It does not.

---

## Known divergences

This is the full, evidenced list. The high-impact subset, with the reasoning for how to
handle each kind, is at the top of this file in
[Before you trust an analysis](#before-you-trust-an-analysis--read-this) — read that first;
this section is the reference behind it.

Every item below was checked against `reference/claude-code/` in this snapshot. Where an
analysis and the code disagree, **the code wins** — but note that in nearly every case the
analysis is describing something real and has the address or the altitude wrong, not the
mechanism. All four analyses target v2.1.88, so none of this is version drift: these are
naming traps, count ambiguities, or designs that are real but feature-eliminated from this
build.

### 1. Two of the five compaction layers are not in this build
All four analyses describe a five-layer pipeline. Layers 2 and 4 — `snipCompactIfNeeded()`
and `applyCollapsesIfNeeded()` — are behind `feature('HISTORY_SNIP')` and
`feature('CONTEXT_COLLAPSE')`, and **their modules do not exist here**:
`src/services/compact/snipCompact.ts` and `src/services/contextCollapse/` are absent. They
survive only as lazy `require()` call sites that would throw if the flag were on. The write
side of context collapse *does* survive (`recordContextCollapseCommit` in
`src/utils/sessionStorage.ts`), which is what makes the absence easy to miss.

The design is five layers; this artifact ships three. The paper itself flags this class of
problem in `analysis/01-dive-into-claude-code.md#b3-limitations` ("different build targets
may produce functionally different applications"), but its body still describes all five as
present.

### 2. `bashSecurity.ts` is a subroutine, not the decision point
`analysis/01-...#113-architectural-trade-offs` states that "the `bashSecurity.ts` module
performs sequential AST-based checks", and `analysis/02-...#7-security-2592-lines-of-bash-paranoia`
builds a whole section on the file. Both are describing something real: the file exists, is
2,593 lines, and runs a battery of ~20 validators.

**It is still live.** Its two exports carry a `_DEPRECATED` suffix, but that marks the *API
shape*, not disuse: `bashPermissions.ts` imports `bashCommandIsSafeAsync_DEPRECATED`,
re-aliases it to `bashCommandIsSafeAsync`, and calls it in six places;
`readOnlyValidation.ts` and `bashCommandHelpers.ts` call it too. Do not treat it as dead
code.

What the analyses get wrong is the **altitude**, not the file. `bashSecurity.ts` is one
input to the decision, not the decision. The function that answers "may this command run"
is **`bashToolHasPermission` in `src/tools/BashTool/bashPermissions.ts`** (2,622 lines),
which no analysis names; it does prefix extraction, wrapper stripping, rule matching, and
the speculative classifier check, and calls the legacy battery as one gate among several.
Parsing sits under `src/utils/bash/` (15 files).

**Start at `bashPermissions.ts`; follow it into `bashSecurity.ts` when the trail leads
there.**

### 3. `src/hooks/` is not the hook system
It is 83 React UI hooks. The hook-event system is `src/utils/hooks.ts` plus
`src/utils/hooks/`. The only safety-relevant thing under `src/hooks/` is
`toolPermission/` and `useCanUseTool.tsx`.

### 4. `queryLoop()` is not exported; `QueryEngine` is not the loop
`queryLoop` is module-private in `src/query.ts`; the exported entry point is `query()`.
`QueryEngine.ts` is a *session wrapper* that calls `query()` per turn. The two are easy to
conflate and `analysis/01-...#34-queryengine-a-clarification` exists specifically to
separate them.

### 5. `src/query/transitions.ts` is absent through *type erasure*, not deletion
`src/query.ts` contains `import type { Terminal, Continue } from './query/transitions.js'`.
The module exists upstream and is imported — it is invisible here because it is **types-only**,
and TypeScript erases type-only imports at compile time, so such a module contributes no
runtime code and never reaches the source map.

So the `Terminal` and `Continue` unions are **declared** in that file and only *constructed*
inline in `src/query.ts`. The ten terminal reasons and seven continue reasons reconstructed
from those construction sites are listed in the
[Loop termination](#loop-termination-and-continue-vocabulary) entry above; they are complete
and correct as data, only the declaration text is missing.

**This is a general mechanism, not a one-off: 37 modules are absent this way** — including
`src/constants/querySource`, `src/cli/transports/Transport`, and most component `types`
modules — versus 7 absent through feature-flag elimination. See
[reason 4](#before-you-trust-an-analysis--read-this).

Separately and for the DCE reason: **`src/shims/` does not exist** — `bun:bundle` is imported
directly in 197 files.

### 6. Tool orchestration is under `src/services/tools/`
Not `src/tools/` (which holds implementations) and not `src/coordinator/` (which is prompt
and persona policy, with no scheduler in it at all).

### 7. `src/agents/` does not exist
Agent definitions are loaded by `src/tools/AgentTool/loadAgentsDir.ts`.

### 8. `src/Task.ts` is not the Task/Agent tool
It is the background-task framework type. Delegation is `AgentTool`. Of its original
polymorphic surface only `kill` remains dispatched dynamically.

### 9. Plugin code is in `src/utils/plugins/`
`src/plugins/` holds only `builtinPlugins.ts` and `bundled/`.

### 10. `src/context/` is React context, not the context window
Context-window assembly is `src/context.ts` + `src/constants/prompts.ts` +
`src/utils/attachments.ts` + `src/services/api/claude.ts`.

### 11. CLAUDE.md loading is `src/utils/claudemd.ts`
`src/memdir/` is a *separate* auto-memory subsystem keyed on `MEMORY.md`. The two are
routinely conflated.

### 12. `history.ts` is prompt input history
It is the shell-style `↑` history of what the user typed, not the conversation transcript.
Transcripts are `src/utils/sessionStorage.ts`.

### 13. There are no enums for permission modes or hook events
Both are `as const` arrays plus `z.enum`. `EXTERNAL_PERMISSION_MODES` has **five** entries
(`acceptEdits`, `bypassPermissions`, `default`, `dontAsk`, `plan`);
`INTERNAL_PERMISSION_MODES` adds `auto` only when `feature('TRANSCRIPT_CLASSIFIER')` is on;
`bubble` exists at the type level only. So "seven permission modes" (used in
`analysis/01-...` and its abstract) is the **type-level** count. At runtime it is five or
six, and `dontAsk` and `bubble` are the ones usually omitted from prose.

`HOOK_EVENTS` really is 27, and is duplicated verbatim in
`src/entrypoints/sdk/coreTypes.ts` and `src/entrypoints/sdk/coreSchemas.ts`.

### 14. Sandbox backends are not in this repository
`src/utils/sandbox/sandbox-adapter.ts` wraps the npm package
`@anthropic-ai/sandbox-runtime`. There is no seatbelt or bubblewrap implementation in
`src/`; bubblewrap appears only as environment *detection*.

### 15. `shouldUseSandbox.ts` is a BashTool internal
It lives at `src/tools/BashTool/shouldUseSandbox.ts`, not at runtime level, despite how it
is cited.

### 16. `src/services/mcp/` has no `transports/` directory
Stock SDK transports are constructed inline in `client.ts`; only `InProcessTransport.ts` and
`SdkControlTransport.ts` are custom files.

### 17. `src/assistant/` is remote history paging
It contains one file, `sessionHistory.ts`, and has nothing to do with turn execution.

### 18. Tool counts differ from every published figure
`src/tools/` holds **40** `*Tool/` directories plus `shared/` and `testing/`. But
`getAllBaseTools()` in `src/tools.ts` also references **at least ten tools whose directories
are not in this build**: `VerifyPlanExecutionTool`, `OverflowTestTool`, `CtxInspectTool`,
`TerminalCaptureTool`, `WebBrowserTool`, `SnipTool`, `ListPeersTool`, `WorkflowTool`,
`MonitorTool`, `PushNotificationTool`. They are `require()`d behind feature flags that are
off, so the modules were eliminated at build time.

Three more on-disk directories are partially eliminated: **`REPLTool/`** and **`SleepTool/`**
keep only their constants/prompt files, and **`ExitPlanModeTool/`** exports
`ExitPlanModeV2Tool`. **`ScheduleCronTool/`** is not one tool but three
(`CronCreateTool`, `CronListTool`, `CronDeleteTool`), and **`McpAuthTool`** is produced by a
factory (`createMcpAuthTool`), not exported as a const.

So "54 built-in tools" (`analysis/01-...`) counts the registry including eliminated entries;
"40+" and "50+" elsewhere are the right order of magnitude but no published figure matches
what you can actually open.

### 19. 18 of the slash commands are disabled stubs
`src/commands/` has 86 directories plus 15 top-level modules. Eighteen of the directories
contain only an `index.js` exporting
`{ isEnabled: () => false, isHidden: true, name: 'stub' }` — the implementations were
stripped from the public build. Counts of "50+" or "100+" slash commands are counting
directory entries, not working commands.

### 20. Feature-flag elimination is the general rule, not an exception
`feature()` from `bun:bundle` is a build-time macro, so a flag that is off removes the
module entirely. This is why several symbols in this codebase are referenced but
unopenable. Before concluding that an analysis is wrong about a module, check whether the
module was simply eliminated: `grep -rn "feature('FLAG')" src/` will show the call sites
even when the target is gone. `analysis/decode/10.md#i-complete-categorized-list-of-88-build-time-feature-flags`
catalogues 88 such flags.

### 21. The O-mega article's framing is out of date, its content is not
`analysis/02-...` presents itself as an analysis of the March 2026 leak of an earlier
version. Its *content* describes v2.1.88 and matches this dump — its `queryLoop` `State`
listing matches `src/query.ts` field for field, including `transition`. Do not discard it as
out-of-version.

### 22. `analysis/03-` was transcribed from a rasterized PDF
The Hasan document had no text layer, so it was transcribed by reading the rendered pages.
It is the one analysis here where a transcription error is possible. Its two flag names
`CACHED_MICROCOMPACT` and `REACTIVE_COMPACT` do both exist in this build, but treat any
specific figure or symbol from that file as needing a check against the code.
