# TDDAgents → the loop engineering architecture

**Status:** architectural direction, superseding the previous agent/tool/skill transition plan.
**Reference base:** `reference/` — read `reference/claude-code-map.md` first; it routes to the
analyses and to the source. Every claude-code claim below cites a path and a symbol you can
open.

---

## 1. Context and thesis

### 1.1 What changed

The previous plan ported claude-code's **features** — agent definitions, a tool protocol,
skills, delegation — onto a LangGraph pipeline whose `tester → runner_red → developer →
runner_green` subgraph remained the enforcement mechanism. The architecture stayed a graph;
the features hung off it.

That is not how claude-code is built, and the difference is not cosmetic. Its own summary of
itself:

> The core of the system is a simple while-loop that calls the model, runs tools, and repeats.
> Most of the code, however, lives in the systems around this loop.
>
> — `analysis/01-dive-into-claude-code.md`

**The loop is the architecture.** Permissions, hooks, context assembly, compaction, recovery,
tool orchestration and persistence are not peers of the loop; they are systems arranged around
a single control structure. A graph of nodes is a different design that reuses the vocabulary.

This document specifies TDDAgents as a loop.

### 1.2 What it costs, stated up front

TDDAgents' research claim rests on the Red→Green invariant being **structurally guaranteed**
rather than prompt-guaranteed. Today that guarantee is graph topology: the ordering is
unreachable by any LLM decision because no edge exists.

A loop has no edges. The invariant moves to a different pair of mechanisms — **tool
availability and stop conditions** — described in §3.3. It remains structural and remains
unreachable by model decision, and in one respect it is stronger: topology prevents a *node*
from running, while tool-pool scoping prevents the model from **naming** the forbidden action
at all, and the runtime gate denies it even if the model hallucinates the name.

But the argument the paper makes changes, and reviewers will press on it. §3.3 is written to
be that argument, and §5 names the two property tests that carry it.

### 1.3 What is not in scope

`experimental_executions/` and `mutation_tests/` stay on disk untouched as frozen paper
artifacts. They will not be re-runnable from the new code, which was accepted when the
previous plan was written and remains accepted here.

---

## 2. The loop engineering architecture

Seven principles, each with the citation to check it against.

### 2.1 Minimal decision scaffolding, maximal operational harness
The model gets broad local autonomy; the engineering investment goes into the harness around
it rather than into constraining its choices. The loop itself is small; `src/query.ts` is
1,729 lines and most of it is recovery.

- Analysis: `analysis/01-dive-into-claude-code.md#4-turn-execution-the-agentic-query-loop`
- Code: `claude-code/src/query.ts` → `query`, `queryLoop`

### 2.2 One mutable record, rewritten wholesale
The loop carries a single `State`. Every continue site assigns a complete new record instead
of mutating fields. The stated reason is reviewability: the reset-or-preserve decision for
every field becomes visible at each site. A `transition` field records *why* the previous
iteration continued, so tests assert a recovery path fired without reading message contents.

- Analysis: `analysis/decode/01.md#i-queryloop-complete-state-machine-reconstruction`
- Code: `claude-code/src/query.ts` → `State`, `Continue`

### 2.3 Named termination vocabulary, not string-literal routing
Seven reasons to continue, ten to stop — a closed vocabulary, not ad-hoc status strings
compared in router functions.

- Analysis: `analysis/01-dive-into-claude-code.md#45-stop-conditions`
- Code: `claude-code/src/query.ts` → `'completed'`, `'max_turns'`, `'stop_hook_prevented'`

### 2.4 Capability as structure, not instruction
A tool the model must not use is **absent from its pool**, not forbidden in its prompt. The
`verification` built-in agent can run tests but has file-editing tools in `disallowedTools`,
so it cannot fix what it verifies. Its prompt explains the role; the tool list enforces it.

- Analysis: `analysis/01-dive-into-claude-code.md#61-four-extension-mechanisms`
- Code: `claude-code/src/tools/AgentTool/built-in/verificationAgent.ts` → `VERIFICATION_AGENT`

### 2.5 Graduated context management
Cheap and lossless before expensive and lossy. Per-result size budgets, then cache-aware
micro-compaction, then a model-generated summary as the last resort — each layer running only
if the previous one left the context over threshold.

- Analysis: `analysis/03-architecture-deep-dive-hasan.md#11-deep-dive-memory-management--context-compaction`
- Code: `claude-code/src/services/compact/autoCompact.ts` → `autoCompactIfNeeded`, `AUTOCOMPACT_BUFFER_TOKENS`

### 2.6 Graceful recovery — every failure gets a named continue
Prompt-too-long, output-token exhaustion, model failure and hook blocking each have a recovery
path that re-enters the loop with modified state, rather than aborting the run. Hard exits are
reserved for cases where recovery is exhausted.

- Analysis: `analysis/01-dive-into-claude-code.md#44-recovery-mechanisms`
- Code: `claude-code/src/services/api/withRetry.ts` → `withRetry`, `FallbackTriggeredError`

### 2.7 Externalised programmable policy
Behaviour that varies by project is configuration executed at lifecycle points, not branches
in the loop. This is the seam TDDAgents uses to enforce its own invariant (§3.3).

- Analysis: `analysis/decode/06.md#6-hooks-system-in-depth`
- Code: `claude-code/src/utils/hooks.ts` → `getMatchingHooks`, `executeStopHooks`

### 2.8 Two cross-cutting rules

**Ordering is a cache key.** The order of the base tool array, the partition-sort in pool
assembly, the static prompt prefix and the frozen replacement strings all exist to keep the
wire prefix byte-stable. The registry carries the warning in-source: it *"MUST stay in sync
with [the caching dynamic config], in order to cache the system prompt across users."* The
generalisation worth internalising: **N independent runtime booleans in the static prefix
produce 2^N distinct cache entries.**

- Code: `claude-code/src/tools.ts` → `getAllBaseTools`, `assembleToolPool`

**Defaults fail closed.** `isConcurrencySafe` and `isReadOnly` default to `false`, and an
exception thrown *inside* either predicate is also treated as `false`.

- Code: `claude-code/src/Tool.ts` → `buildTool`

---

## 3. Target design

### 3.1 The loop, and the three-way state split

The first thing to port is not the loop body — it is the division of state into three kinds.
The rationale is recorded in-source: separating immutable config from the per-iteration record
*"makes future step() extraction tractable — a pure reducer can take `(state, event, config)`
where config is plain data."*

| Kind | Lifetime | Mutability | TDDAgents type |
|---|---|---|---|
| `RunConfig` | snapshotted once at entry | immutable plain data | frozen dataclass |
| `LoopState` | one per iteration | **replaced wholesale** at each continue | frozen dataclass, rebuilt |
| `ToolContext` | ambient, threaded through | mutable, re-spread per iteration | dataclass |

- Code: `claude-code/src/query/config.ts` → `buildQueryConfig`, `QueryConfig`

`LoopState` fields, TDDAgents' version:

```
messages                          conversation so far
tool_context                      ambient context
phase_ledger                      TDD phase state  (§3.3 — TDDAgents-specific)
tdd_block_count                   bounded stop-hook retries (§3.3)
compaction_tracking               turn id, counter, consecutive failures
output_limit_recovery_count       bounded at 3
has_attempted_reactive_compact    single-shot guard
stop_hook_active                  whether hooks already blocked this turn
turn_count                        advances only at next_turn
transition                        why the previous iteration continued
```

**The reset/preserve discipline is the load-bearing part.** A recovery-attempt flag may be
cleared only by an event that *changed the context*, never by one that merely re-entered the
loop. The in-source comment records the bug that established this:

> "Resetting to false here caused an infinite loop: compact → still too long → error → stop
> hook blocking → compact → … burning thousands of API calls."

Consequences to port verbatim: `turn_count` advances only at `next_turn`, so recovery
iterations are free against `max_turns`; `has_attempted_reactive_compact` survives the
stop-hook continue; recovery counters reset only where real model output was appended.

### 3.2 Systems around the loop, in execution order

| Position | System | Modelled on |
|---|---|---|
| pre-model | context assembly — env, spec, conventions, workspace state | `claude-code/src/constants/prompts.ts` → `getSystemPrompt` |
| pre-model | per-turn delta injection | `claude-code/src/utils/attachments.ts` → `getAttachmentMessages` |
| pre-model | per-result size budget | `claude-code/src/utils/toolResultStorage.ts` → `applyToolResultBudget` |
| pre-model | graduated compaction | `claude-code/src/services/compact/` |
| pre-model | budget check | `claude-code/src/query/tokenBudget.ts` → `checkTokenBudget` |
| model | streamed call, eager dispatch | `claude-code/src/services/tools/StreamingToolExecutor.ts` → `StreamingToolExecutor` |
| tools | partition by concurrency safety | `claude-code/src/services/tools/toolOrchestration.ts` → `runTools` |
| tools | permission gate | `claude-code/src/utils/permissions/permissions.ts` → `hasPermissionsToUseTool` |
| tools | Pre/Post lifecycle hooks | `claude-code/src/services/tools/toolHooks.ts` → `runPreToolUseHooks` |
| exit | stop conditions and recovery | `claude-code/src/query.ts` → `queryLoop` |

### 3.3 How the Red→Green invariant is enforced without a graph

This section replaces "the sealed TDD core" and is the architectural centre of the document.

**The mechanism already exists in the source, and it is doubled.** `getDenyRuleForTool` is
called from two independent places: pool assembly, and the runtime permission gate.

- Code: `claude-code/src/tools.ts` → `filterToolsByDenyRules`
- Code: `claude-code/src/utils/permissions/permissions.ts` → `getDenyRuleForTool`

Pool assembly strips denied tools *before the model sees them*; the gate re-checks at call
time. TDDAgents uses both against the same ledger.

**Not `isEnabled()`.** That predicate is nullary — it takes no input and reads only ambient
process state — so it cannot express "forbidden during this phase of this run".

- Code: `claude-code/src/Tool.ts` → `isEnabled`

#### The four parts

**1 — The phase ledger.** Three fields on `LoopState`, mirrored into app state so tools can
write them:

```
phase           RED | GREEN | REFACTOR
red_confirmed   a test has been observed failing
green_passed    the same test has been observed passing
```

**2 — `RunTests` writes it.** Tools already receive `set_app_state`, so the ledger is updated
by the only component that can observe ground truth: the test runner. No agent asserts its own
progress.

- Code: `claude-code/src/Tool.ts` → `ToolUseContext`

**3 — Phase-derived deny rules.** Pool assembly derives deny rules from `phase`:

| Phase | Denied | Consequence |
|---|---|---|
| RED | implementation writers | the model cannot write production code before a failing test exists |
| GREEN | test writers | the model cannot edit the test to make it pass |
| REFACTOR | test writers | behaviour is pinned while structure changes |

`RunTests` is never denied. The Developer's current prompt rule — *"never invoke the test
runner yourself"* — becomes an absent tool rather than a sentence, per §2.4.

**4 — The `tdd_phase_incomplete` Stop hook.** When the model stops with no tool calls, the
Stop hook inspects the ledger. If the cycle is incomplete it returns blocking errors; the loop
rebuilds `LoopState` with those errors appended as messages, sets `stop_hook_active`, and
continues. The model is told *why* it may not stop.

- Analysis: `analysis/decode/01.md#ix-complete-architecture-of-stop-hooks`
- Code: `claude-code/src/query/stopHooks.ts` → `handleStopHooks`

Precedence to port exactly: `preventContinuation` wins over `blockingErrors` and blanks them —
a hook cannot both stop the turn and request a retry.

#### Why this is structural

Three independent barriers, none reachable by a model decision:

1. The forbidden tool is **not in the pool**, so it is not in the model's schema. It cannot be
   called correctly.
2. If the model invents the name, the **runtime gate denies it** by the same rule.
3. If the model gives up and answers in text, the **Stop hook refuses the exit**.

Graph topology gives one barrier and only at node granularity. This gives three at action
granularity.

#### The F2 "green in red" case

A test that passes on first run is a ledger state, not a special edge: `red_confirmed` stays
false, so the Stop hook blocks and tells the model the test never failed. The cycle cannot be
skipped, and the F1/F2 distinction is read off the transition history (§3.6) rather than
recorded by wrapper functions.

#### The bounded-retry gap

Upstream has **no counter** bounding stop-hook retries; the only protection is the hook
honouring `stop_hook_active`. That is acceptable for an interactive tool with a human present
and unacceptable for an unattended research run. TDDAgents adds `tdd_block_count` to
`LoopState`, bounded, and reset exactly where `output_limit_recovery_count` is reset — per the
§3.1 discipline. Exhausting it is a terminal reason, not an infinite loop.

### 3.4 What LangGraph retains

LangGraph keeps what the loop has no answer for, and stops being the enforcement mechanism:

- Postgres checkpointing and cross-restart resume
- `interrupt()` for the analyst human-in-the-loop
- iteration over plan items

**Dissolved into the loop:** `execute_tester`, `execute_developer`, `execute_runner_red`,
`execute_runner_green`, `build_tdd_subgraph`, the four `wrapper_*` functions, the sparse
`is_flow_type` list, and both router functions. The `status` string vocabulary is replaced by
the closed transition enums of §2.3.

### 3.5 Agents, tools, skills and delegation

These survive from the previous plan, re-framed as systems around the loop rather than as the
architecture.

**Agent definitions** stay frontmatter-declared. **Tool resolution** is per-agent: allowlist ∩
available, minus denylist.

**Delegation isolation is stronger than previously specified.** A subagent's pool is assembled
from the **worker's own** permission mode, not inherited and filtered, so parent restrictions
do not leak down; and the allow-rule list is *replaced* rather than merged, so parent approvals
do not leak down either.

- Code: `claude-code/src/tools/AgentTool/AgentTool.tsx` → `assembleToolPool`
- Code: `claude-code/src/tools/AgentTool/runAgent.ts` → `runAgent`

**Forking** carries a hard API constraint: an assistant message holding a `tool_use` with no
matching `tool_result` is rejected. Forked context must be filtered.

- Code: `claude-code/src/tools/AgentTool/runAgent.ts` → `filterIncompleteToolCalls`

**Skills** keep three-level progressive disclosure. Only name, description and when-to-use
reach the prompt; the body loads on invocation.

- Code: `claude-code/src/skills/loadSkillsDir.ts` → `parseSkillFrontmatterFields`, `estimateSkillFrontmatterTokens`

### 3.6 Metrics as a derivation

Flow classification stops being recorded and becomes derived. `transition.reason` is already
on `LoopState` for testability; the event log is a second reader of the same field. F1 is a
transition history showing RED with `red_confirmed` set before GREEN; F2 is one where
`red_confirmed` was still false at the first Stop-hook block. No wrapper functions, no sparse
list indexed by plan position.

---

## 4. Implementation roadmap

Twelve parts, 64 sub-phases. Each ends runnable and offline-testable. The order builds the
centre first so every later part has something to attach to.

Every sub-phase is gated by the repository quality gate in `CLAUDE.md` — tests written *with*
the code, then flake8, mypy, and mutation testing on what was touched.

### Part A — Loop core (7)

| # | Ships | Notes |
|---|---|---|
| A1 | `RunConfig` / `LoopState` / `ToolContext` split | frozen dataclasses; the split is the point |
| A2 | `Continue` and `Terminal` as two `StrEnum`s | closed vocabulary, replaces `status` literals |
| A3 | `while True` skeleton, `next_turn` only | driven by a fake model; no sandbox, no network |
| A4 | DI seam | `call_model`, `compact`, `uuid`, `now`, `run_tools`, `stop_hooks`, event sink |
| A5 | the ten terminal returns | each with its own test |
| A6 | `max_turns` and the turn-count asymmetry | recovery iterations must be free |
| A7 | reset/preserve matrix | one executable test per continue site, asserting `transition.reason` |

A7 is the gate for the whole part: it is what stops a future edit from silently reintroducing
the compact↔hook infinite loop.

### Part B — Tool layer (8)

| # | Ships |
|---|---|
| B1 | result / validation / permission dataclasses |
| B2 | `Tool` protocol + `build_tool` with fail-closed defaults |
| B3 | `ToolContext` minimum surface — abort, app state, messages, in-flight ids |
| B4 | `run_tool_use` happy path: resolve → validate → call → map result |
| B5 | `partition_tool_calls` + serial and concurrent `run_tools` |
| B6 | context-modifier queue, replayed in the model's call order |
| B7 | `yield_missing_tool_results` — the history-repair invariant |
| B8 | pool assembly with the partition-sort |

B7 is not optional polish: every emitted `tool_use` must receive a `tool_result` on *every*
abnormal exit, or the next request is malformed.

### Part C — Permission and safety (5)

`C1` permission context and the mode ladder · `C2` `check_rule_based_permissions`, the
rule-only subset · `C3` the full gate including bypass and the passthrough→ask conversion ·
`C4` per-input capability, so `Bash("ls")` is a read and `Bash("pip install")` is not ·
`C5` the workspace boundary.

The precedence to port: deny rules and interaction-required asks sit **above** every allow
path, including bypass mode.

### Part D — TDD enforcement (6) — the crux

| # | Ships |
|---|---|
| D1 | phase ledger in app state |
| D2 | `RunTests` writes the ledger — the only component that observes ground truth |
| D3 | phase-derived deny rules feeding pool assembly |
| D4 | the same rules at the runtime gate |
| D5 | `tdd_phase_incomplete` Stop hook + bounded `tdd_block_count` |
| D6 | the two invariant property tests |

D6 **is** the paper's structural claim, so it is named here rather than left to §5:

1. No reachable tool pool in RED contains an implementation-writing tool, for any ledger state
   the loop can produce.
2. No sequence of model outputs reaches `completed` without the ledger showing Red-then-Green.

### Part E — Context systems (8)

| # | Ships | Why it is ordered here |
|---|---|---|
| E1 | token counting | everything downstream consumes it |
| E2 | API-invariant slicing | pure functions, highest test value |
| E3 | instruction-file loading | |
| E4 | system prompt sections + static/dynamic boundary | |
| E5 | cache breakpoints and the latch rule | |
| E6 | attachments and the delta pattern | |
| E7 | compaction: summarise + head truncation | |
| E8 | post-compact cleanup | |

E2 carries three constraints that produce hard API errors rather than degraded quality, and
each needs a test before anything depends on it: never split a `tool_use`/`tool_result` pair,
never orphan a thinking block, never leave an assistant message first after head truncation.

E5's rule: anything feeding the cache key must be **latched for the session**. A value that
flips mid-run silently busts the cache.

E8 carries a two-layer cache trap worth stating in the code: clearing an inner memoised cache
without clearing the outer one that wraps it is a no-op from the caller's side.

### Part F — Streaming and recovery (6)

`F1` streamed model call · `F2` dispatch at content-block close, not on partial parse ·
`F3` the three-controller abort tree — turn, sibling, per-tool — with only Bash errors
cascading to siblings and exactly one deliberate upward bubble · `F4` ordered emission and
`discard()` · `F5` withhold-then-decide · `F6` the recovery continue sites.

F5's hazard is specific: the withhold decision and the recovery decision must read the **same**
gate snapshot, or a value flipping during a long stream causes a message to be withheld and
then never surfaced.

### Part G — Workspace and execution (4)

`G1` workspace protocol · `G2` sandbox adapter · `G3` local workspace · `G4` checkpointed
sync. Generated code continues to run only in the sandbox.

### Part H — Hooks (5)

`H1` event vocabulary and matching — per-event match key, dedup, `if`-conditions ·
`H2` the command schema · `H3` exit-code semantics: 0 proceeds, 2 blocks, anything else is
logged and ignored · `H4` prompt and agent backends · `H5` the TDD lifecycle hooks, including
the one from §3.3.

H2 carries a bug worth not rediscovering: a schema used for round-tripping user configuration
must not contain transforms producing non-serializable values, or saving the file silently
deletes the user's own settings.

### Part I — Agents and delegation (5)

`I1` agent definitions as frontmatter · `I2` per-agent tool resolution · `I3` the `Agent` tool
with an independently assembled worker pool · `I4` context forking with incomplete-call
filtering · `I5` run-scoped agent memory, discarded at run end so each run stays an
independent sample.

### Part J — Skills (4)

`J1` loader and frontmatter · `J2` progressive disclosure budget · `J3` path-conditional
activation · `J4` the TDD skill roster.

### Part K — Session shell (3)

`K1` LangGraph reduced to checkpointing · `K2` `interrupt()` for the analyst loop ·
`K3` plan-item iteration.

### Part L — Metrics and cleanup (3)

`L1` the event log · `L2` F1/F2 derived from transition history · `L3` deletion of the
dissolved graph modules listed in §3.4.

---

## 5. Verification

### 5.1 Per sub-phase
The `CLAUDE.md` quality gate, in order: unit tests written with the change, flake8 and mypy
clean on touched files, mutation testing with every survivor either killed or written down.

### 5.2 The structural claim
Parts A7 and D6 are the two gates that matter beyond ordinary correctness:

- **A7** — one test per continue site asserting which fields reset and which persist. Without
  it, the recovery machinery degrades silently into infinite loops.
- **D6** — the two property tests in Part D. These are the paper's structural argument in
  executable form. If they cannot be written, the architecture does not support the claim and
  that must be discovered in Part D, not at writing-up time.

### 5.3 End to end
The seam modules named in `CLAUDE.md` — the sandbox adapter, the model factory, the agent
runtime — stay exempt from unit and mutation testing and are verified by running the pipeline
end to end. That remains the only check of the system as a whole.

### 5.4 Against the reference
Any claude-code claim added to this document must cite a path and symbol that exist in
`reference/claude-code/`. Check with:

```bash
python3 scripts/reference-checks/verify_paths.py docs/transition_elaboration_plan.md
python3 scripts/reference-checks/verify_anchors.py docs/transition_elaboration_plan.md
```
