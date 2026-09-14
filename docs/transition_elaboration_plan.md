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
be that argument, and §6 names the two property tests that carry it.

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
A closed set of named reasons — seven to continue and ten to stop upstream — rather than
ad-hoc status strings compared in router functions. A reason that does not exist cannot be
constructed, so a misspelling fails at the site that made it instead of producing a run that
silently fails to route.

- Analysis: `analysis/01-dive-into-claude-code.md#45-stop-conditions`
- Code: `claude-code/src/query.ts` → `'completed'`, `'max_turns'`, `'stop_hook_prevented'`

TDDAgents carries the subset its own subsystems produce, keeping upstream's exact spellings:

```
Continue   next_turn · stop_hook_blocking · reactive_compact_retry
           max_output_tokens_recovery

Terminal   completed · blocking_limit · model_error · prompt_too_long
           aborted_streaming · aborted_tools · stop_hook_prevented · hook_stopped
```

Six of upstream's reasons are left out because nothing here produces them: the
context-collapse drain and the token-budget continuation belong to subsystems this
architecture does not have, `max_output_tokens_escalate` needs an output cap to escalate
away from, `max_turns` needs a turn ceiling, and `image_error` needs image inputs. A member
no site can set is a member no test can cover — the same objection that retired the recovery
counters (§3.1). Each spelling is recorded above and added when the part that produces it
lands.

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
compaction_tracking               whether this turn compacted, turn id, turn counter
has_attempted_reactive_compact    single-shot guard
stop_hook_active                  whether hooks already blocked this turn
turn_count                        advances only at next_turn
transition                        why the previous iteration continued
```

**The record holds no counter, because it enforces no ceiling.** Upstream's
`maxOutputTokensRecoveryCount` and the `consecutiveFailures` member of its compaction
tracking exist to be compared against a maximum, and each is read in exactly one place: the
recovery guard in `query.ts` and the circuit breaker in `autoCompactIfNeeded`. This loop is
unbounded by decision, so neither comparison exists, and a counter with no comparison is a
bound waiting to be re-added by whoever finds the field and wonders what it is for. What a
run actually did is read off the transition history (§3.6), which is where the metrics look
anyway.

The distinction that matters: `stop_hook_active` and `has_attempted_reactive_compact` stay.
They are not counters. They are latches recording that something has already been tried on
*this* context, which is a fact about state rather than a budget, and removing them is how
the infinite loop below comes back.

- Code: `claude-code/src/query.ts` → `MAX_OUTPUT_TOKENS_RECOVERY_LIMIT`
- Code: `claude-code/src/services/compact/autoCompact.ts` → `autoCompactIfNeeded`, `AutoCompactTrackingState`

**The reset/preserve discipline is the load-bearing part.** A recovery-attempt flag may be
cleared only by an event that *changed the context*, never by one that merely re-entered the
loop. The in-source comment records the bug that established this:

> "Resetting to false here caused an infinite loop: compact → still too long → error → stop
> hook blocking → compact → … burning thousands of API calls."

Consequences to port verbatim: `turn_count` advances only at `next_turn`, so a recovery
iteration costs nothing against anything that later reads it; `has_attempted_reactive_compact`
survives the stop-hook continue and is cleared only by an iteration that appended real model
output; `stop_hook_active` is set at the stop-hook continue, preserved across `next_turn`, and
cleared at every other continue site.

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

#### Why there is no retry counter

Upstream bounds stop-hook retries with **no counter at all**. The protection is a single
boolean: the loop sets `stop_hook_active` when it continues on blocking errors, and that flag
is threaded into the hook's own input payload as `stop_hook_active`, so a hook that already
blocked once can see that it did and decline to block again. The budget lives in the hook, not
in the loop.

- Code: `claude-code/src/query/stopHooks.ts` → `handleStopHooks`
- Code: `claude-code/src/utils/hooks.ts` → `executeStopHooks`, `stop_hook_active`

TDDAgents keeps that mechanism unchanged and adds nothing to it. An earlier draft of this
document proposed a bounded `tdd_block_count` on `LoopState`, on the argument that an
unattended research run cannot rely on a hook behaving. That is dropped: this is loop
engineering, the loop runs without limitation, and a ceiling that exists to catch a
misbehaving hook is a ceiling that also truncates a legitimately long run — which is the
experiment, not a failure mode.

The cost is stated rather than hidden. A `tdd_phase_incomplete` hook that ignores
`stop_hook_active` and blocks unconditionally will keep the loop alive forever. Honouring the
flag is therefore part of that hook's contract rather than an optimisation, and Part D5 is
where it is written and tested.

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

## 4. Prompts and definitions as Markdown

### 4.1 Jinja2 is removed

All agent prompts today are Jinja2 templates under `app/prompts/agents/langgraph/<agent>/`.
They go. The replacement is plain Markdown in English, following claude-code's own
conventions.

The reason is not stylistic. The Jinja2 `Environment` in `prompt_loader.py` uses
non-strict undefined, so a misspelled kwarg **renders as an empty string with no error**.
The repository already carries one live instance of this: `developer.py` passes
`sub_requsite=` while the template expects `sub_requisite`, so the Developer's first prompt
has had a blank sub-requirement block for as long as that code has existed. A templating
engine that fails silently is the wrong substrate for the thing that defines agent behaviour.

Markdown with explicit placeholder substitution fails differently: an unresolved placeholder
survives into the text **visibly**, as `{{LIKE_THIS}}`, where it is obvious in a transcript.

- Code: `claude-code/src/skills/bundled/claudeApi.ts` → `SKILL_MODEL_VARS`

### 4.2 The file layout, taken from claude-code

claude-code authors every prompt-bearing unit as a **directory with a Markdown entry file and
optional reference files**, and inlines them at build time. The build comment states it
plainly: *"Each .md file is inlined as a string at build time via Bun's text loader."*

- Code: `claude-code/src/skills/bundled/verifyContent.ts` → `SKILL_MD`, `SKILL_FILES`
- Code: `claude-code/src/skills/bundled/claudeApiContent.ts` → `SKILL_FILES`

TDDAgents mirrors the structure without the bundler — files are read from disk:

```
app/prompts/
├── system/                       # composed into the system prompt (§4.3)
│   ├── identity.md
│   ├── tdd-contract.md
│   ├── tools.md
│   └── tone.md
├── agents/
│   └── refactorer/
│       ├── AGENT.md              # frontmatter + body
│       └── references/
│           └── refactoring-catalogue.md
└── skills/
    └── testing-patterns/
        ├── SKILL.md
        └── references/
            └── fixtures.md
```

Two contracts come with that layout:

**Reference files load on demand, not up front.** When a unit has reference files, its prompt
is prefixed with a base-directory line so the model can read them itself. The source
describes this as the *"same contract as disk-based skills"* — the entry file costs tokens
every time, the references cost tokens only when the body points at them.

- Code: `claude-code/src/skills/bundledSkills.ts` → `registerBundledSkill`, `BundledSkillDefinition`

**HTML comments are stripped before the model sees the text**, iteratively until stable. That
is what makes `<!-- ... -->` usable for authoring notes, maintenance markers and provenance
without paying for them in context.

- Code: `claude-code/src/utils/claudemd.ts` → `stripHtmlComments`

### 4.3 What belongs in a prompt file, and what does not

The system prompt is **composed from sections**, not rendered from one template. Sections are
registered with a name and a compute function, resolved once and memoised, and ordered
deliberately: everything before a boundary marker is stable and cacheable, everything after is
session-specific.

- Analysis: `analysis/decode/02.md#6-section-caching-mechanism-systempromptsectionsts`
- Code: `claude-code/src/constants/systemPromptSections.ts` → `systemPromptSection`, `resolveSystemPromptSections`
- Code: `claude-code/src/constants/prompts.ts` → `getSystemPrompt`, `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`

This is the §2.8 cache rule applied to prose: a section whose text varies per run must sit
*after* the boundary, or it fragments the cacheable prefix. So:

| Goes in a `.md` file | Does not |
|---|---|
| role, contract, worked examples, rules of engagement | anything that varies per run |
| stable prose that is identical across every run | model ids, paths, token counts, thresholds |
| `{{PLACEHOLDER}}` markers where a value belongs | the value itself |

### 4.4 Values are substituted, never written into prose

Placeholders are substituted at render time by a single regex pass; an unknown key is **left
as-is** rather than replaced with empty text, so a typo is visible instead of silent.

- Code: `claude-code/src/skills/bundled/claudeApi.ts` → `SKILL_MODEL_VARS`

claude-code treats literal values in prose as a maintenance liability even where it has them,
and marks each site so it gets updated at release time:

> `@[MODEL LAUNCH]: Update the model IDs/names below. These are substituted into {{VAR}}`
> `placeholders in the .md files at runtime before the skill prompt is sent.`
> `After updating these constants, manually update the two files that still hardcode models…`

Those two files are called out precisely *because* hardcoding them was a mistake that now
needs manual upkeep. TDDAgents takes the lesson rather than the exception: **no model id, no
path, no threshold, and no count is written into Markdown prose.** Each has one resolution
point in code and reaches the prompt as a placeholder.

### 4.5 The agent definition format

Frontmatter carries configuration; the body is the prompt. The parser's discipline is what
matters, and it is the direct answer to arbitrary values in the format.

- Code: `claude-code/src/tools/AgentTool/loadAgentsDir.ts` → `parseAgentFromMarkdown`
- Code: `claude-code/src/utils/frontmatterParser.ts` → `parseFrontmatter`, `parsePositiveIntFromFrontmatter`

**Four rules, all taken from the parser:**

1. **Omission means unset, never a default.** Every optional field parses to `undefined` when
   absent. No value is substituted at parse time.
2. **An invalid value is logged and ignored — never silently defaulted.** The parser records
   what was wrong and which values were valid, then proceeds as if the field were absent.
3. **Enumerations validate against an explicit list**, and the list appears in the error
   message.
4. **`inherit` is a sentinel, not a config lookup.** The definition never names a constant
   from the codebase.

**On `maxTurns` specifically.** It is `number | undefined` end to end, and the loop tests it as
`if (maxTurns && nextTurnCount > maxTurns)` — **falsy means unbounded**. No default is
substituted anywhere in the agent path. The single numeric literal in the whole path is one
named call site for one specific mode, not a format default.

- Code: `claude-code/src/query.ts` → `maxTurns`

So a definition carrying `max_turns: 8` states a number nobody chose, that no experiment
justified, and that silently becomes the thing future readers treat as tuned. Omit the field.
TDDAgents goes past omission: it sets no ceiling anywhere, in a definition file or in code.
There is no turn limit to write down, so `max_turns` is not a field of this format and
`max_turns` is not a terminal reason in its vocabulary (§2.3) — the falsy branch upstream
already takes is the only behaviour this system has.

#### The format

```markdown
---
name: refactorer
description: Improves structure without changing behaviour. Runs only after green.
phase: post_green
tools: [ReadFile, ListDir, Grep, WriteFile, RunTests, Skill]
permissionMode: workspace_write
memory: run
forkFrom: developer
revertOnRed: true
hooks:
  PreToolUse:
    - matcher: WriteFile
      hooks: [{ type: command, command: "scripts/hooks/snapshot_before_write.sh" }]
---

You are a senior engineer improving code that already passes its tests.

...
```

Every field absent from that block is absent on purpose:

| Field | Why it is not there |
|---|---|
| `max_turns` | no ceiling exists in this architecture, so there is no value to omit |
| `model` | omitted inherits; write `model: inherit` to say so explicitly, never a code constant |
| trailing `# unset → …` comments | the parser's behaviour is the contract, not a comment that drifts from it |

Fields that are TDDAgents-specific rather than ported — `phase`, `forkFrom`, `revertOnRed` —
follow the same four rules: validated against an explicit list, logged and ignored when
invalid, `undefined` when absent.

`phase` is the one field with teeth: it is what the phase-derived deny rules of §3.3 read, so
an invalid value must **fail the definition load loudly** rather than be ignored. That is the
one deliberate divergence from rule 2 in this format, and it exists because a silently ignored
`phase` would silently disable the TDD invariant.

### 4.6 Skills use the same shape

`SKILL.md` with frontmatter and a body, plus `references/` loaded on demand. Only name,
description and when-to-use reach the system prompt; the body loads on invocation; references
load only if the body points at them.

- Analysis: `analysis/01-dive-into-claude-code.md#61-four-extension-mechanisms`
- Code: `claude-code/src/skills/loadSkillsDir.ts` → `parseSkillFrontmatterFields`, `estimateSkillFrontmatterTokens`

### 4.7 Output styles replace a section, not the prompt

An output style is a named block that substitutes the response-formatting section while
leaving the rest of the system prompt intact — including, optionally, the coding instructions.
It is the mechanism for changing how an agent writes without rewriting what it knows.

- Code: `claude-code/src/constants/outputStyles.ts` → `OutputStyleConfig`, `OUTPUT_STYLE_CONFIG`

### 4.8 The system prompt is composed, not rendered

§4.1–4.7 cover prompts as *files*. This covers the architecture that turns those files into a
system prompt, which is where the sharpest recorded failure mode in the reference base lives.

- Analysis: `analysis/decode/02.md#6-section-caching-mechanism-systempromptsectionsts`
- Code: `claude-code/src/constants/systemPromptSections.ts` → `systemPromptSection`, `DANGEROUS_uncachedSystemPromptSection`, `resolveSystemPromptSections`

#### The registry

The prompt is not one template. It is an ordered list of **named sections**, each a name plus
a compute function plus one behavioural flag, resolved into strings at assembly time. The
whole registry is 68 lines.

Two constructors, and the difference between them is the entire design:

| Constructor | Behaviour |
|---|---|
| `systemPromptSection(name, compute)` | memoized — computed once, reused until the cache is cleared |
| `DANGEROUS_uncachedSystemPromptSection(name, compute, reason)` | recomputed every turn, breaking the cache when the value changes |

The second one **takes a mandatory `reason` argument that is never read at runtime**. It exists
so the cost has to be written down at the call site and argued with in review. A quietly
volatile section is invisible until someone measures cache-creation tokens and works backwards
to it; a section that must state its reason cannot hide.

In the whole of claude-code exactly one section is volatile — MCP instructions, because servers
connect and disconnect between turns.

#### The boundary, and why 2^N is the number that matters

A sentinel element sits in the section list. Everything before it is identical on every run for
every user and is sent with a global cache scope; everything after is session-specific and is
not cached at all.

- Code: `claude-code/src/constants/prompts.ts` → `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`, `getSystemPrompt`

It is a **sentinel array element, not a delimiter inside a string** — the splitter tests for it
by position in the list, and its doc comment warns against removing or reordering it without
updating the two functions downstream that consume the split.

The reason it exists is recorded on the section that would otherwise violate it:

> "Session-variant guidance that would fragment the `cacheScope:'global'` prefix if placed
> before `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`. Each conditional here is a runtime bit that would
> otherwise multiply the Blake2b prefix hash variants (2^N)."

That is the rule to internalise, and it is not intuitive: **a single boolean in the static
prefix does not cost a little, it doubles the number of distinct cache entries.** Ten
independent booleans produce a thousand. The placement rule follows mechanically — anything
whose text varies per run, per project, per permission mode or per tool pool goes *after* the
boundary, no exceptions.

Downstream, the split becomes text blocks carrying `cache_control`, under a hard constraint the
source states in capitals: the API caps total cache breakpoints at four. The boundary is not a
nicety; it is how a prompt fits inside that budget.

- Code: `claude-code/src/services/api/claude.ts` → `buildSystemPromptBlocks`, `getCacheControl`

#### Composition order for TDDAgents

| Position | Section | Why there |
|---|---|---|
| static | identity | never varies |
| static | system — how output, tools and reminders work | never varies |
| static | doing tasks — engineering conduct | never varies |
| static | **TDD contract** — Red before Green, what each phase permits | the invariant's prose; identical every run |
| static | using your tools | varies only with the tool roster, which is a release-time fact |
| static | executing actions with care | never varies |
| static | tone and output efficiency | never varies |
| — | **boundary** | |
| dynamic | `<env>` — cwd, python, pytest, installed packages | per run |
| dynamic | the specification under test | per run |
| dynamic | `CONVENTIONS.md` | per run, and mutates during the run |
| dynamic | workspace state — tree, test status | per turn |
| dynamic | phase ledger position | per turn |

The TDD contract sitting in the **static** half is the point worth noticing. The rules of the
cycle are prose that never changes; only the *position* in the cycle changes. Putting the
contract before the boundary and the ledger after it is what keeps the expensive half cacheable
across every run of every task.

#### Four details that are easy to get wrong

**`None` is a cached decision, not a cache miss.** A section that computes to "omit me" records
that result. Re-asking a section every turn whether it has anything to say is the same cost as
a volatile section, without the reason string that would have made it visible.

**Prefer an unconditional section phrased as a no-op over a conditional section.** claude-code's
token-budget section is cached unconditionally and worded so it is inert when no budget is
active. It used to be conditional, and the comment records the bill: *"busting ~20K tokens per
budget flip."* Wording your way out of a conditional is cheaper than branching.

**Cache invalidation has named points**, and it clears more than the sections — the same call
also resets the beta-header latches so a fresh conversation re-evaluates them. For TDDAgents the
equivalent points are conversation reset and post-compaction.

- Code: `claude-code/src/constants/systemPromptSections.ts` → `clearSystemPromptSections`

**Subagent prompts have no boundary.** A delegated agent's prompt is assembled by appending
environment details to its body, and the whole thing is one block. Main session and subagent
even render the environment differently — Markdown bullets for the main loop, an XML `<env>`
block for subagents.

- Code: `claude-code/src/constants/prompts.ts` → `enhanceSystemPromptWithEnvDetails`, `computeEnvInfo`, `computeSimpleEnvInfo`

The consequence for TDDAgents: delegated agents (§3.5) do not get the static/dynamic split, so
their prompts should stay short. A subagent prompt is paid for in full on every delegation.

#### One deliberate divergence

claude-code's section cache is **process-global**, living in bootstrap state. That is why its
post-compaction cleanup has to distinguish main thread from fork before resetting anything — a
subagent compacting would otherwise corrupt the parent's cached sections.

TDDAgents scopes the section cache **per run** instead, passed in rather than reached for. The
same isolation falls out of the structure rather than out of a conditional, and a delegated
agent cannot evict its parent's entries because it never had a reference to them. Recorded here
as a divergence so nobody later "fixes" it back toward the source.

---

## 5. Implementation roadmap

Twelve parts, 65 sub-phases. Each ends runnable and offline-testable. The order builds the
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
| A5 | the remaining terminal returns | each with its own test |
| A6 | the turn-count asymmetry | `turn_count` advances only at `next_turn`, so recovery iterations are free |
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
| D5 | `tdd_phase_incomplete` Stop hook, and the `stop_hook_active` contract that keeps it terminable |
| D6 | the two invariant property tests |

D6 **is** the paper's structural claim, so it is named here rather than left to §6:

1. No reachable tool pool in RED contains an implementation-writing tool, for any ledger state
   the loop can produce.
2. No sequence of model outputs reaches `completed` without the ledger showing Red-then-Green.

### Part E — Context systems (9)

| # | Ships | Why it is ordered here |
|---|---|---|
| E1 | token counting | everything downstream consumes it |
| E2 | API-invariant slicing | pure functions, highest test value |
| E3 | instruction-file loading | |
| E4 | Markdown prompt loader: frontmatter, `{{VAR}}` substitution, comment stripping | the §4 substrate; replaces Jinja2 |
| E4b | section registry, memoized/volatile split, boundary + cache-block mapping | §4.8; composes the §4.3 files |
| E5 | cache breakpoints and the latch rule | |
| E6 | attachments and the delta pattern | |
| E7 | compaction: summarise + head truncation | |
| E8 | post-compact cleanup | |

E2 carries three constraints that produce hard API errors rather than degraded quality, and
each needs a test before anything depends on it: never split a `tool_use`/`tool_result` pair,
never orphan a thinking block, never leave an assistant message first after head truncation.

E5's rule: anything feeding the cache key must be **latched for the session**. A value that
flips mid-run silently busts the cache.

E4 is where Jinja2 is deleted. It ships before anything that authors a prompt, so no new
template is ever written against the old engine. Its acceptance test is the failure mode that
motivated the change (§4.1): an unresolved placeholder must survive visibly into the rendered
text, never render as empty.

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

`I1` agent definitions in the §4.5 format — parser discipline first: omission means unset,
invalid is logged and ignored, `phase` fails loudly · `I2` per-agent tool resolution · `I3` the `Agent` tool
with an independently assembled worker pool · `I4` context forking with incomplete-call
filtering · `I5` run-scoped agent memory, discarded at run end so each run stays an
independent sample.

### Part J — Skills (4)

`J1` `SKILL.md` loader on the §4.6 shape · `J2` progressive disclosure budget · `J3` path-conditional
activation · `J4` the TDD skill roster.

### Part K — Session shell (3)

`K1` LangGraph reduced to checkpointing · `K2` `interrupt()` for the analyst loop ·
`K3` plan-item iteration.

### Part L — Metrics and cleanup (3)

`L1` the event log · `L2` F1/F2 derived from transition history · `L3` deletion of the
dissolved graph modules listed in §3.4.

---

## 6. Verification

### 6.1 Per sub-phase
The `CLAUDE.md` quality gate, in order: unit tests written with the change, flake8 and mypy
clean on touched files, mutation testing with every survivor either killed or written down.

### 6.2 The structural claim
Parts A7 and D6 are the two gates that matter beyond ordinary correctness:

- **A7** — one test per continue site asserting which fields reset and which persist. Without
  it, the recovery machinery degrades silently into infinite loops.
- **D6** — the two property tests in Part D. These are the paper's structural argument in
  executable form. If they cannot be written, the architecture does not support the claim and
  that must be discovered in Part D, not at writing-up time.

### 6.3 End to end
The seam modules named in `CLAUDE.md` — the sandbox adapter, the model factory, the agent
runtime — stay exempt from unit and mutation testing and are verified by running the pipeline
end to end. That remains the only check of the system as a whole.

### 6.4 Against the reference
Any claude-code claim added to this document must cite a path and symbol that exist in
`reference/claude-code/`. Check with:

```bash
python3 scripts/reference-checks/verify_paths.py docs/transition_elaboration_plan.md
python3 scripts/reference-checks/verify_anchors.py docs/transition_elaboration_plan.md
```
