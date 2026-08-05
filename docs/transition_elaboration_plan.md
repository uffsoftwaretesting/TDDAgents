# TDDAgents → claude-code-style Agent, Tool, and Skill Architecture

## Context

TDDAgents drives Red–Green TDD through a fixed LangGraph pipeline: `planner → (tester → runner_red → developer → runner_green) → evaluator`. Every transition is a hardcoded `status` string literal read by a router function; every agent is a single `.with_structured_output(AgentAction)` call emitting `files_to_write` / `bash_commands` / `dependencies`; and every agent's only view of the workspace is `AgentState["file_system"]` — a dict containing *only files the agents themselves wrote*.

Three structural consequences follow:

1. **Agents are blind.** Files created by `bash_commands` never enter the mirror, are never shown back to the model, and are never extracted to `workspace_output_*`. The `execution_logs` returned by `apply_agent_action_to_sandbox` are discarded at both call sites ([execute_tester.py:48](app/graph/nodes/execute_tester.py#L48), [execute_developer.py:39](app/graph/nodes/execute_developer.py#L39)) — an agent can never see the output of a command it ran.
2. **Context growth is quadratic.** Every turn re-renders the whole codebase into the human message *and* appends the full JSON action (with complete file contents) as the AI message, up to `MAX_ITERATIONS=15` per sub-requirement.
3. **Roles and knowledge are fixed at compile time.** Adding a specialization means a new node, new status literals, and new router branches across two files. There is no mechanism for domain knowledge at all.

The target is to port claude-code's agent/tool/skill architecture onto TDDAgents: declarative agent definitions, a real tool protocol with per-agent scoping, delegation as a scoped tool, three-level skills, layered context injection, and explicit agent lifecycles — while the Red→Green invariant the research measures stays structurally guaranteed rather than prompt-guaranteed.

**Decisions locked with the user across six rounds:**

| | |
|---|---|
| Migration | Full refactor, one evolved architecture, no compatibility branch |
| Delegation | `Agent` tool scoped by frontmatter with allowed target types |
| Tools | Real tool loop, per-agent scoping, `maxResultSizeChars` + `isConcurrencySafe` |
| Isolation | Shared sandbox, context firewall, selective forking at three sites |
| TDD core | Sealed: ordering and Red-before-Green enforced by the graph |
| Hooks | Research before planning, research on failure, refactor after each green |
| Mirror | `file_system` demoted to an export ledger |
| Metrics | Redesigned as an event log around delegation |
| Validation | No separate agent — tester + runners are the validation phase |
| Requirements | Folded into one checkpointed graph via `interrupt()` |
| Model | `model:` field honored, every definition defaults to `Config.MODEL` |
| `permissionMode` | Sandbox write scope: `read_only` \| `workspace_write` \| `full` |
| Skills | 7 skills; model-invoked `Skill` tool + path-conditional activation + supervisor selection |
| Skill depth | `SKILL.md` + `references/` + `scripts/`; 2 authored, 5 scaffolded |
| Extensions in | Frontmatter hooks, run-scoped agent memory, context forking |
| Extensions out | MCP servers, async/background agents (sequential delegation only) |
| Language | English everywhere — prompts, logs, docstrings, comments, CLI, benchmark specs |

`experimental_executions/` and `mutation_tests/` stay on disk untouched as frozen paper artifacts; they will no longer be re-runnable from the new code, which the user accepted.

---

## 1. Architectural Comparison

| Concern | claude-code | TDDAgents today | Target |
|---|---|---|---|
| Agent definition | Markdown + YAML frontmatter → `AgentDefinition` (`loadAgentsDir.ts`): `tools`, `disallowedTools`, `model`, `maxTurns`, `permissionMode`, `skills` | Implicit: a node function + a Jinja2 prompt pair | `app/agents/definitions/<name>.md` — same field set, English body |
| Delegation | `AgentTool` — LLM emits `subagent_type` + `prompt`; `runAgent.ts` builds an isolated context | None | `Agent` tool in the registry, granted per agent with allowed targets |
| Tools | `Tool` protocol (`Tool.ts:362`): `call`, `inputSchema`, `isReadOnly`, `isConcurrencySafe`, `maxResultSizeChars` | Three fields on one Pydantic model, applied by the node | `app/tools/base.py::Tool` — same protocol |
| Tool scoping | `resolveAgentTools()` — allowlist ∩ available, minus denylist, `*` wildcard | None; every agent has identical powers | Same resolution, plus `permissionMode` as an enforcement floor |
| Context isolation | Own message list per subagent; only a final report returns | Three `add_messages` channels shared for the whole sub-requirement | Self-contained `run_agent`; summary-only return + forking at 3 sites |
| Environment injection | `computeEnvInfo()` → `<env>`, appended by `enhanceSystemPromptWithEnvDetails()`; plus `getSystemContext()` / `getUserContext()` | The rendered file mirror, nothing else | `<env>` + spec + `CONVENTIONS.md` + workspace state summary |
| Lifecycle | `runAgent`'s `finally`: hook clearing, file-state release, task kill, registry eviction | None | `AgentRunResult` + explicit teardown |
| Budget | `maxTurns` per agent in frontmatter | `MAX_ITERATIONS=15` cycle-wide | `max_turns` per agent + cycle budget retained |
| **Skills** | `skills/<name>/SKILL.md`, 3-level progressive disclosure; `paths:` conditional activation | **Nothing** | 7 domain skills, same 3 levels, same activation model |

Two patterns are imported in spirit, not just in shape:

- **Capability as structure, not instruction.** The Developer's prompt currently says *"never invoke the test runner yourself."* Under tool scoping, `RunTests` simply is not in the Developer's tool list — the same way Explore's read-only guarantee comes from `disallowedTools` ([exploreAgent.ts:67](/home/pedroamaro/claude-code/src/tools/AgentTool/built-in/exploreAgent.ts#L67)) while its prompt merely explains it. Every prompt rule that can become a tool boundary should.
- **Pay for knowledge only when used.** The skill listing costs ~7 lines; a full `SKILL.md` costs real tokens and loads only on invoke; `references/` load only if the body says to. This is what makes 7 domain skills affordable in a 15-iteration loop.

---

## 2. Target Design

### 2.1 File organization

```
app/
  main.py                    # single entrypoint, one graph
  config/config.py           # Config + unified AgentState (RequirementsState deleted)
  graph/
    orchestrator.py          # the one graph
    routers.py               # ALL status routing, one StrEnum
    nodes/                   # thin state adapters only
    subgraphs/tdd_core.py    # the sealed cycle
  agents/
    definition.py            # AgentDefinition dataclass + frontmatter parser
    registry.py              # discovery, caching, phase filtering, resolve_tools()
    runtime.py               # run_agent(): tool loop, hooks, memory, forking, teardown
    memory.py                # run-scoped agent memory
    definitions/*.md         # analyst, engineer, planner, supervisor, tester,
                             # developer, reviewer, researcher, refactorer
  tools/
    base.py                  # Tool protocol, ToolContext, ToolResult, result persistence
    registry.py              # name → Tool; resolve_tools(definition)
    agent_tool.py            # delegation
    skill_tool.py            # skill invocation
    read_file.py list_dir.py grep.py write_file.py bash.py run_tests.py
    host_read.py             # read-only, allowlisted host access (researcher)
    web.py                   # WebSearch / WebFetch (researcher)
  skills/
    backend/SKILL.md + references/ + scripts/        # fully authored
    testing-patterns/SKILL.md + references/ + scripts/  # fully authored
    frontend/ design/ database/ security/ performance/  # scaffolded
    loader.py                # discovery, listing budget, path-conditional activation
  context/
    env.py                   # <env> block
    project.py               # spec + CONVENTIONS.md + workspace state summary
  metrics/                   # events.py, collector.py, reports.py
```

**Deleted:** [app/utils/workspace.py](app/utils/workspace.py) and [app/utils/spec_loader.py](app/utils/spec_loader.py) (both already dead), [app/graph/subgraphs/requirements_orchestrator_subgraph.py](app/graph/subgraphs/requirements_orchestrator_subgraph.py), the four `wrapper_*` functions in [build_tdd_subgraph.py](app/graph/subgraphs/build_tdd_subgraph.py), `AgentAction` in [app/schema/schema.py](app/schema/schema.py), `apply_agent_action_to_sandbox`, the whole `app/prompts/agents/langgraph/` tree (including three unreferenced `feedback_*.jinja2` templates), `Config.WORKSPACE_PATH`, `Config.PLAN_KEY`.

Retiring Jinja2 for agents also removes the silent-undefined bug class documented in CLAUDE.md — including the live `sub_requsite=` typo at [developer.py:35](app/agents/langgraph/developer.py#L35) that currently blanks the Developer's sub-requirement on its first attempt of every plan item.

### 2.2 Agent definition format

```markdown
---
name: refactorer
description: Improves structure without changing behavior; runs only on green.
phase: post_green              # pre_plan | pre_cycle | recovery | post_green
tools: [ReadFile, ListDir, Grep, WriteFile, RunTests, Skill]
permission_mode: workspace_write
model:                         # unset → Config.MODEL
max_turns: 8
memory: run                    # run-scoped only
fork_from: developer           # inherit the Developer's context
revert_on_red: true
hooks:
  on_start: [inject_coverage_report]
---
You are a senior software engineer improving code that already passes its tests.
...
```

`phase` is the delegation guard: each hook passes the phases legal there, and the registry filters candidates — the port of `allowedAgentTypes`. `model` is honored but unset everywhere, so runs stay single-model and comparable.

### 2.3 Tool protocol, scoping, and `permissionMode`

```python
class Tool(Protocol):
    name: str
    description: str
    args_schema: type[BaseModel]
    is_read_only: bool
    is_concurrency_safe: bool
    max_result_chars: int
    def call(self, args: BaseModel, ctx: ToolContext) -> ToolResult: ...
```

`ToolContext` carries `sandbox_id`, the ledger, `agent_id`, `permission_mode`, workdir, and remaining budget. `resolve_tools(definition)` mirrors `resolveAgentTools`: allowlist ∩ registry, minus denylist, `*` for wildcard.

**`permission_mode` is the enforcement floor, not a duplicate of the allowlist.** `read_only` rejects any tool whose `is_read_only` is False at `ToolContext` level, even if a definition mistakenly lists `WriteFile`. This makes "the Researcher is read-only" auditable rather than declarative — defense in depth exactly where a prompt-level rule would silently fail.

**Result governance.** A result exceeding `max_result_chars` is written to a sandbox file; the agent receives a head/tail preview plus the path, which it can `ReadFile` selectively. This matters most for `RunTests`: `-vv --tb=long --showlocals` output is both the largest and the most important thing an agent reads. Tools with `is_concurrency_safe = True` (all read tools) execute in parallel within one turn.

**Ledger sync fixes the lost-files bug.** Any write-capable tool triggers reconciliation (`find . -type f -newer <marker>` + read back) into `file_system`, so files created by `Bash` finally land in the ledger and therefore in `workspace_output_*`.

Per-agent scoping:

| Agent | Tools | permission_mode |
|---|---|---|
| supervisor | `Agent(researcher,refactorer)`, ReadFile, ListDir | read_only |
| researcher | ReadFile, ListDir, Grep, Bash, HostRead, WebSearch, WebFetch, Skill | read_only |
| tester | ReadFile, ListDir, Grep, WriteFile, Skill — **no RunTests** | workspace_write |
| developer | ReadFile, ListDir, Grep, WriteFile, Bash, Skill — **no RunTests** | full |
| refactorer | ReadFile, ListDir, Grep, WriteFile, RunTests, Skill | workspace_write |
| reviewer | ReadFile, ListDir, Grep | read_only |

`HostRead` is read-only and path-allowlisted to the project root plus configured doc directories; it must reject paths outside the allowlist and never write.

### 2.4 Delegation

`Agent(subagent_type, prompt, description)` is an ordinary registry tool. It is granted only where frontmatter lists it, and the grant carries its targets using the `Agent(researcher,refactorer)` spec syntax that `permissionRuleValueFromString` already parses in `resolveAgentTools`. Tester and Developer are never granted it, so the sealed core cannot fan out. Delegation is **sequential** — the call blocks until the subagent returns.

### 2.5 `run_agent` — lifecycle

```python
def run_agent(definition, task_prompt, ctx) -> AgentRunResult:
    tools = resolve_tools(definition)
    messages = build_initial_messages(definition, ctx)   # system + env + project context
    messages += run_start_hooks(definition, ctx)         # injected context
    if definition.fork_from:
        messages = fork_context(ctx, definition.fork_from) + messages
    if definition.memory:
        messages[0] += load_memory(definition.name, ctx.thread_id)
    llm = get_chat_model(Config.CHAT_MODEL, model=definition.model or Config.MODEL).bind_tools(tools)
    try:
        for turn in range(definition.max_turns):
            ai = llm.invoke(messages); messages.append(ai)
            if not ai.tool_calls:
                break
            messages += execute_tools(ai.tool_calls, tools, ctx)   # parallel if all concurrency-safe
        return AgentRunResult(summary=..., turns=..., tool_calls=..., files_touched=...)
    finally:
        clear_agent_hooks(definition, ctx)
        release_forked_context(ctx)
        flush_events(ctx)
        kill_background_commands(ctx)     # any lingering sandbox process
        evict_result_cache(ctx)
```

Only `AgentRunResult` crosses back into `AgentState`. The three `add_messages` channels are removed from state entirely — cross-iteration continuity is carried by explicit `feedback` fields and `agent_summaries`. This eliminates the quadratic growth and makes `_clear_agent_histories` / `RemoveMessage` unnecessary.

**Forking is the deliberate exception** to the firewall, at exactly three sites: the recovery researcher forks the failing cycle (it exists because the Developer already failed — the dead ends are the point), the refactorer forks the Developer (to tell deliberate constraints from accidental shape), and the reviewer forks the runner (better fault attribution). Forked messages are filtered for incomplete tool calls, exactly as `filterIncompleteToolCalls` does.

**Memory is run-scoped only.** It lives under the run's `thread_id`, accumulates lessons across the sub-requirements of one run, and is discarded at run end — so every run stays an independent sample and your 3-runs-per-task design survives.

### 2.6 Skills

Structure is claude-code's verbatim:

```
app/skills/backend/
  SKILL.md              # frontmatter + playbook body
  references/
    api-contracts.md    # loaded only when SKILL.md points at it
    validation.md
  scripts/
    scaffold_service.py # runnable in the sandbox via ${SKILL_DIR}
```

```markdown
---
name: backend
description: API contracts, service/repository layering, error handling, and how to test each.
when_to_use: Building or testing HTTP endpoints, services, or persistence layers.
paths: ["**/api/**", "**/services/**", "**/routers/**", "**/test_*api*.py"]
allowed_tools: [ReadFile, WriteFile, Bash]
---
```

Three loading paths, all three in use:

1. **Model-invoked** — `Skill("backend")` loads the body on demand. Only `name: description` appears in the system prompt, capped per entry and against a total character budget (claude-code uses 250 chars/entry and 1% of context; port both constants).
2. **Path-conditional** — `paths:` activates a skill when an agent reads or writes a matching file. The Tester writing `tests/test_api.py` pulls in `backend` with nobody deciding.
3. **Supervisor selection** — at the `pre_cycle` hook the supervisor reads the sub-requirement and names the skills this plan item's agents should have available.

Frontmatter *preloading* (`skills:` on an agent) is deliberately **not** implemented.

Granted to tester, developer, refactorer, researcher. Roster: `backend`, `frontend`, `design`, `database`, `testing-patterns`, `security`, `performance` — `backend` and `testing-patterns` fully authored with references and one working script each; the other five ship valid frontmatter and a real but shorter body.

### 2.7 Context injection

Three layers, mirroring `enhanceSystemPromptWithEnvDetails` + `getSystemContext` + `getUserContext`:

- **`<env>`** (`context/env.py`) — workdir, Python version, pytest version and plugins, installed packages, live file tree, plan position. Cached per sub-requirement, invalidated after writes.
- **System context** (`context/project.py`) — the Engineer's `final_specification`, formalized as a single cached block rather than re-rendered ad hoc per prompt.
- **User context** (`context/project.py`) — `CONVENTIONS.md`, seeded by the Planner/Researcher inside the generated workspace (chosen layout, naming, import style, test conventions), read by every later agent and maintained as decisions accumulate; plus a workspace state summary (tree + current test status + coverage), the `gitStatus` analogue, always fresh.

`CONVENTIONS.md` ships out in `workspace_output_*` as genuine project documentation, and is the durable shared decision record that the per-sub-requirement context reset currently destroys.

### 2.8 Graph topology

```
START → analyst ⇄ user_input (interrupt) → engineer
      → supervisor[pre_plan]   ──Agent(researcher)?──►
      → planner
      → supervisor[pre_cycle]  ──Agent(researcher)? + skill selection──►
      → ┌───────── SEALED TDD CORE ─────────────────────┐
        │ tester → runner_red → developer → runner_green │
        └────────────────────────────────────────────────┘
              │ retry edges (test_review_needed / green_failed, ≥2nd attempt)
              └──► supervisor[recovery] ──Agent(researcher, fork)──┘
      → supervisor[post_green] ──Agent(refactorer, fork)──► runner_verify
      → evaluator ──next_req──► supervisor[pre_cycle] │ END
```

**What "sealed" means, precisely:** the ordering `tester → runner_red → developer → runner_green` and the `red_confirmed`-before-`green_passed` invariant are enforced by the graph and unreachable by any LLM decision. The F2 "green in red" path keeps its current behavior — the runner still routes to the Developer with the injected warning, so the cycle is never skipped. The `recovery` hook sits on the *retry* edge only: it enriches feedback before an already-failing sub-requirement is retried; it cannot reorder or skip a phase. This is the only reconciliation of "sealed core" with "research on failure", and it holds both.

**Refactor safety:** the refactorer runs only after `green_passed`, must call `RunTests` itself, and `runner_verify` re-confirms green. On red its writes are reverted from the ledger snapshot taken before it ran (`revert_on_red: true`), recorded as `RefactorReverted`.

### 2.9 Routing and state cleanups carried by this refactor

- The Reviewer returns its `ReviewAnalysis` object and routing switches on `is_test_fault`. The `[ERRO NO TESTE]` / `[ERRO NA IMPLEMENTAÇÃO]` string-prefix contract — load-bearing across [reviewer.py:64](app/agents/langgraph/reviewer.py#L64) and both runner nodes — is deleted.
- Status literals move to one `StrEnum` in `graph/routers.py`; nodes and routers can no longer drift.
- Nodes return partial updates, not `{**state, ...}`.
- `CommandExitException` from a bad *agent-authored* shell command is returned to the agent as a tool error rather than classified as `TransientInfraError`. Today it burns three full LLM retries and then hard-fails the pipeline ([errors/sandbox/handler.py:31](app/errors/sandbox/handler.py#L31)).
- `main.py`'s dead success-status checks (`completed_with_review`, `completed_successfully` — produced nowhere) are removed.

### 2.10 Metrics

Event-sourced in `app/metrics/`. Nodes, `run_agent`, and every tool emit typed events: `AgentStarted`, `ToolCalled`, `ToolResultTruncated`, `SkillInvoked`, `SkillActivatedByPath`, `AgentFinished`, `DelegationDecided`, `RedConfirmed`, `GreenPassed`, `RefactorReverted`, `SubReqCompleted`. A collector derives reports at the end.

The four `wrapper_*` functions and the sparse `is_flow_type` list indexed by `plan_index` are deleted; flow classification becomes a derivation over the event log. New dimensions: delegation tree per sub-requirement; turns/tool-calls/tokens per agent; skill invocation frequency and which skills correlate with green-on-first-try; tool failure rates; refactor accept-vs-revert rate.

---

## 3. Implementation Roadmap

Ordered so the system is runnable at every checkpoint.

**Phase 0 — English migration + conventions.** Translate all prompts, logger messages, docstrings, comments, the `main.py` menu and console output, and `app/prompts/specs/*.txt`. Rewrite CLAUDE.md: invert the language convention and update Architecture as the refactor lands. First, so everything after is written in English from the start.

**Phase 1 — Tool layer.** `tools/base.py` (protocol, `ToolContext`, `ToolResult`, result persistence), `tools/registry.py` (`resolve_tools`, permission-mode enforcement, parallel dispatch), then `ReadFile`, `ListDir`, `Grep`, `WriteFile`, `Bash`, `RunTests`, `HostRead`, `WebSearch`, `WebFetch`. Reuse `run_pytest_in_sandbox` from [runner.py](app/agents/langgraph/runner.py) as the body of `RunTests` — its `_PYTEST_FLAGS` and `CommandExitException` interception are already correct. Add ledger sync. Independently testable with no graph changes.

**Phase 2 — Agent registry + runtime.** `definition.py`, `registry.py`, `runtime.py::run_agent` (loop + teardown; hooks/memory/forking stubbed). Port tester/developer/reviewer to `definitions/*.md` and the tool loop. Delete `AgentAction`, `apply_agent_action_to_sandbox`, and the three message channels from `AgentState`. **Routing stays byte-identical here** — this isolates "agents became tool-loop agents" from "topology changed" so a regression is attributable.

**Phase 3 — Context layers.** `context/env.py` and `context/project.py`; stop rendering `read_all_files_from_state` into prompts; `CONVENTIONS.md` seeding and the workspace state summary; `file_system` becomes the export ledger only.

**Phase 4 — Skills.** `skills/loader.py` (discovery, listing budget, path-conditional activation), `tools/skill_tool.py`. Author `backend` and `testing-patterns` fully; scaffold the other five.

**Phase 5 — Delegation.** `tools/agent_tool.py` with target scoping, the four supervisor hook sites with phase-filtered candidates, `researcher.md` and `refactorer.md`, `runner_verify`, ledger snapshot/revert.

**Phase 6 — Lifecycle extensions.** Frontmatter hooks with `on_start` context injection; run-scoped `agents/memory.py`; context forking at the three sites with incomplete-tool-call filtering.

**Phase 7 — Unify the requirements phase.** Fold analyst/user_input/engineer into the main graph, replace the blocking `input()` at [requirements_user_input.py:29](app/graph/nodes/requirements_user_input.py#L29) with `interrupt()` + `Command(resume=...)`, delete `RequirementsState` and the second orchestrator. The whole run becomes checkpointed and resumable end to end.

**Phase 8 — Metrics.** Event bus, collector, report writers; delete the wrappers and `is_flow_type`.

**Phase 9 — Cleanup.** Remove dead modules and config constants; confirm CLAUDE.md matches the shipped architecture.
