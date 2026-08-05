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

Ordered so the system is runnable at every checkpoint. Each phase below cites the actual claude-code source (`~/claude-code/src/...`, readable via the `claude-code-explorer` MCP server) behind the mechanism being ported — read the cited file before implementing rather than working from the paraphrase here.

### Phase 0 — English migration + conventions *(completed)*

Translate all prompts, logger messages, docstrings, comments, the `main.py` menu and console output, and `app/prompts/specs/*.txt`. Rewrite CLAUDE.md: invert the language convention and update Architecture as the refactor lands.

**Steps (as executed):**
1. Translate all 19 Jinja2 prompt templates under `app/prompts/agents/langgraph/`.
2. Translate the 5 spec files under `app/prompts/specs/*.txt`, keeping Brazil-specific domain terms (CEP, CPF) untranslated as subject matter.
3. Translate LLM-facing `Field(description=...)` strings in `app/schema/schema.py` and inline in `agents/langgraph/*.py`.
4. Translate all remaining Python source: `config/`, `errors/`, `agents/langgraph/*.py`, `graph/nodes/*.py`, `graph/orchestrator.py`, `graph/subgraphs/*.py`, `utils/*.py`, `main.py`.
5. Move the fault-attribution literals `[ERRO NO TESTE]`/`[ERRO NA IMPLEMENTAÇÃO]` to `[TEST ERROR]`/`[IMPLEMENTATION ERROR]` in lockstep across `reviewer.py` and both runner nodes.
6. Move the CLI commands `/sim`/`/sair` to `/yes`/`/exit` in lockstep across `requirements_user_input.py` and `main.py`.
7. Update CLAUDE.md's language-convention bullet and Commands section to match.

No claude-code reference applies — this phase is a pure-text migration internal to TDDAgents.

### Phase 1 — Tool layer

`tools/base.py` (protocol, `ToolContext`, `ToolResult`, result persistence), `tools/registry.py` (`resolve_tools`, permission-mode enforcement, parallel dispatch), then `ReadFile`, `ListDir`, `Grep`, `WriteFile`, `Bash`, `RunTests`, `HostRead`, `WebSearch`, `WebFetch`. Independently testable with no graph changes.

**Steps:**
1. Define the `Tool` protocol in `app/tools/base.py`: `name`, `description`, `args_schema` (Pydantic — the analogue of claude-code's Zod `inputSchema`), `call(args, ctx) -> ToolResult`, `is_read_only(input)`, `is_concurrency_safe(input)`, `is_destructive(input)`, `max_result_chars`, `check_permissions(input, ctx)`.
2. Port claude-code's `buildTool()` default-filling pattern as a Python factory: every tool definition can omit the commonly-stubbed methods and get safe, fail-closed defaults (`is_concurrency_safe → False`, `is_read_only → False`, `check_permissions → allow`).
3. Define `ToolContext` — a trimmed `ToolUseContext`: `sandbox_id`, the ledger (`file_system` equivalent), `agent_id`, `permission_mode`, workdir, remaining turn budget.
4. Define `ToolResult` and implement result persistence exactly as documented for `maxResultSizeChars`: a result exceeding the limit is written to a sandbox file, the caller gets a head/tail preview plus the path. Set this to effectively unbounded for tools where persisting would create a circular read loop.
5. Build `tools/registry.py::resolve_tools(definition)`: allowlist ∩ registry, minus denylist, `*` wildcard, dedup by name, stable ordering.
6. Enforce `permission_mode` as a floor at the `ToolContext` level, independent of the per-tool allowlist — `read_only` must reject any tool whose `is_read_only` is `False` even if a definition mistakenly lists it.
7. Implement `ReadFile`, `ListDir`, `Grep` as read-only + concurrency-safe; `WriteFile`, `Bash` as neither — matching how claude-code's own read tools (file read, grep, glob) vs. write/exec tools are classified.
8. `RunTests` reuses `run_pytest_in_sandbox` from `agents/langgraph/runner.py` verbatim as its body — its `_PYTEST_FLAGS` and `CommandExitException` interception are already correct.
9. Add ledger sync (`find . -type f -newer <marker>` + read-back) triggered by any write-capable tool call, so files created by `Bash` land in `file_system` too.

**claude-code reference:**
- `~/claude-code/src/Tool.ts` — the `Tool` type (search for `call`, `checkPermissions`, `isReadOnly`, `isConcurrencySafe`, `maxResultSizeChars`) and the `buildTool()`/`TOOL_DEFAULTS` factory near the end of the file.
- `~/claude-code/src/tools.ts` — `getAllBaseTools()`, `filterToolsByDenyRules()`, `getTools(permissionContext)`, `assembleToolPool()` (dedup + cache-stable sort) — the direct model for `resolve_tools`.
- `~/claude-code/src/tools/FileReadTool/`, `GrepTool/`, `BashTool/` — concrete `isReadOnly`/`isConcurrencySafe` classifications to mirror.

### Phase 2 — Agent registry + runtime

`definition.py`, `registry.py`, `runtime.py::run_agent` (loop + teardown; hooks/memory/forking stubbed). Port tester/developer/reviewer to `definitions/*.md` and the tool loop. Delete `AgentAction`, `apply_agent_action_to_sandbox`, and the three message channels from `AgentState`. **Routing stays byte-identical here** — this isolates "agents became tool-loop agents" from "topology changed" so a regression is attributable.

**Steps:**
1. `agents/definition.py::AgentDefinition` — a dataclass with `tools`, `model`, `permission_mode`, `max_turns`, `skills`, `memory`, `hooks`, plus TDDAgents-specific `phase`, `fork_from`, `revert_on_red`.
2. Write the Markdown+YAML frontmatter parser: read frontmatter, validate required fields (`name`, `description`), and skip-with-log on a malformed file rather than crashing the whole registry — co-located reference docs or typos in one agent file must not take down agent discovery for the rest.
3. `agents/registry.py::discover()` — memoized load of all `definitions/*.md`, then `resolve_tools(definition)` (Phase 1's function) including the special-case handling needed for `Agent(researcher,refactorer)` target scoping (see Phase 5).
4. `runtime.py::run_agent()` — resolve model, build initial messages (system + env + project context — stub these for now, real content lands in Phase 3), tool-call loop bound by `max_turns`, a teardown block.
5. Mirror the teardown shape exactly: kill any lingering sandbox process the agent spawned, clear any per-agent caches — this is the direct analogue of killing background shell tasks and clearing invoked-skill tracking when an agent finishes.
6. Port tester/developer/reviewer to `definitions/*.md` + the tool loop, using a single-purpose, explicitly-scoped built-in agent as the concrete template: a short disallow list plus a banner-style prompt section that states capability boundaries in plain language (defense in depth alongside the tool-scoping itself).
7. Delete `AgentAction`, `apply_agent_action_to_sandbox`, and the three message-channel fields from `AgentState`.
8. Keep routing byte-identical — same status literals, same router functions — so a regression is attributable to "agents became tool-loop agents," not "topology changed."

**claude-code reference:**
- `~/claude-code/src/tools/AgentTool/loadAgentsDir.ts` — `BaseAgentDefinition` (frontmatter field set), `parseAgentFromMarkdown()` (frontmatter → typed definition, graceful skip on parse failure), `getAgentDefinitionsWithOverrides()` (memoized discovery, later sources override earlier by agent-type key via `getActiveAgentsFromList()`).
- `~/claude-code/src/tools/AgentTool/agentToolUtils.ts` — `resolveAgentTools()` (wildcard expansion, disallow set, the `Agent`-tool special case that carries `allowedAgentTypes`).
- `~/claude-code/src/tools/AgentTool/runAgent.ts` — the lifecycle shape (model resolution, message assembly, teardown block that kills spawned shell tasks and clears per-agent skill/cache state).
- `~/claude-code/src/tools/AgentTool/built-in/exploreAgent.ts` — a fully worked example of a narrowly-scoped built-in agent: explicit `disallowedTools`, and a system prompt with an explicit "READ-ONLY MODE" capability banner backing up the tool-level restriction.

### Phase 3 — Context layers

`context/env.py` and `context/project.py`; stop rendering `read_all_files_from_state` into prompts; `CONVENTIONS.md` seeding and the workspace state summary; `file_system` becomes the export ledger only.

**Steps:**
1. `context/env.py::build_env_block()` — render a compact `<env>`-style block (workdir, Python/pytest versions and plugins, installed packages, live file tree, plan position) appended once per turn as its own cacheable layer, not string-spliced into the rest of the prompt.
2. Cache the env block per sub-requirement and invalidate it explicitly after writes — mirrors how claude-code invalidates its own cached context by clearing the memoization cache when underlying state changes.
3. `context/env.py` also mirrors the "existing prompt + notes + env block, concatenated as an array of independently-cacheable pieces" pattern rather than a single flattened string.
4. `context/project.py::get_system_context()` — the Engineer's `final_specification`, computed once and cached for the run instead of re-rendered ad hoc per prompt (same "compute once, cache for the duration" idea as claude-code's git-status block).
5. `context/project.py::get_user_context()` — `CONVENTIONS.md` (TDDAgents' CLAUDE.md-equivalent, seeded by the Planner/Researcher, read by every later agent) plus a workspace-state summary (tree + test status + coverage) that is *always* recomputed fresh, never cached — mirroring the split between a memoized CLAUDE.md block and an always-fresh timestamp-style field.
6. Stop rendering `read_all_files_from_state` into every prompt; `file_system` becomes the export ledger only, consulted by tools (`ReadFile`, `Grep`) on demand instead of being force-fed into every turn.

**claude-code reference:**
- `~/claude-code/src/context.ts` — `getSystemContext()` (memoized git-status block, cache cleared via `.cache.clear()`), `getUserContext()` (CLAUDE.md hierarchy walk + an always-fresh `currentDate` field) — the exact split between "compute once" and "always fresh" TDDAgents needs.
- `~/claude-code/src/constants/prompts.ts` — `computeEnvInfo()` (the literal `<env>` block contents) and `enhanceSystemPromptWithEnvDetails()` (appends notes + env block onto the system-prompt array, keeping layers independently cacheable).

### Phase 4 — Skills

`skills/loader.py` (discovery, listing budget, path-conditional activation), `tools/skill_tool.py`. Author `backend` and `testing-patterns` fully; scaffold the other five.

**Steps:**
1. `app/skills/<name>/SKILL.md` — directory-only format (no bare `.md` files), matching claude-code's own restriction that only `skill-name/SKILL.md` is a valid skill.
2. `skills/loader.py::parse_frontmatter()` — extract `when_to_use`, `allowed_tools`, `paths` from the SKILL.md frontmatter.
3. Path-conditional activation: gitignore-style pattern matching against files an agent just read or wrote, walking from the touched path upward, with nested/deeper skill directories taking precedence over shallower ones.
4. Listing budget: only `name` + `description` + `when_to_use` ever appear in the system-prompt skill listing; the full body loads only on `Skill(name)` invocation. Port claude-code's exact two constants for the budget (250 chars/entry, 1% of context) rather than inventing new numbers.
5. `tools/skill_tool.py::Skill` — given a name, returns the skill body, resolving a `${SKILL_DIR}`-style placeholder to the skill's own directory so bundled scripts can be referenced from the body.
6. Author `backend` and `testing-patterns` fully (`SKILL.md` + `references/` + `scripts/`, one working script each); scaffold `frontend`, `design`, `database`, `security`, `performance` with valid frontmatter and a real but shorter body.

**claude-code reference:**
- `~/claude-code/src/skills/loadSkillsDir.ts` — `loadSkillsFromSkillsDir()` (directory-only format enforcement), `parseSkillFrontmatterFields()` (the frontmatter field set), `parseSkillPaths()` + `activateConditionalSkillsForPaths()` (the gitignore-style path-conditional activation, deepest-path-first precedence), `estimateSkillFrontmatterTokens()` (the listing-budget accounting — only frontmatter costs tokens until invoked), and the `${CLAUDE_SKILL_DIR}` substitution inside `getPromptForCommand`.
- `~/claude-code/src/tools/SkillTool/` — the model-invoked `Skill` tool shape.

### Phase 5 — Delegation

`tools/agent_tool.py` with target scoping, the four supervisor hook sites with phase-filtered candidates, `researcher.md` and `refactorer.md`, `runner_verify`, ledger snapshot/revert.

**Steps:**
1. `tools/agent_tool.py::Agent(subagent_type, prompt, description)` — an ordinary registry tool that dispatches to `run_agent()`, granted to a definition only where its frontmatter lists it.
2. Target scoping uses the literal `Agent(researcher,refactorer)` spec syntax — not a TDDAgents invention, a direct port of a real mechanism: parsing a tool spec string into a base name plus a parenthesized content payload, then splitting that payload on commas into the allowed-targets list.
3. Port the parsing function itself rather than reimplementing it — it already handles edge cases (escaped parentheses, empty-content-means-tool-wide-rule) that TDDAgents doesn't need today but shouldn't have to rediscover later if the spec syntax grows.
4. The four supervisor hook sites (`pre_plan`, `pre_cycle`, `recovery`, `post_green`) each get a phase-filtered candidate list — later-registered agents of the same name override earlier ones, filtered by `phase` instead of by source (built-in vs. plugin vs. user).
5. `researcher.md` and `refactorer.md` — model the roster-assembly pattern on how claude-code assembles its own built-in-agent list: a short, explicit list built per context, each entry gated by a condition (there, a feature flag; here, the current `phase`).
6. `runner_verify` + ledger snapshot/revert: snapshot the ledger before the refactorer runs; on red, revert its writes from that snapshot.
7. Delegation is sequential — the call blocks until the subagent returns; Tester and Developer are never granted `Agent`, so the sealed core cannot fan out.

**claude-code reference:**
- `~/claude-code/src/tools/AgentTool/AgentTool.tsx` — the `Agent` tool itself (`subagent_type` + `prompt` input, dispatch to the runtime).
- `~/claude-code/src/tools/AgentTool/agentToolUtils.ts` — the `Agent`-tool special case inside `resolveAgentTools()` that parses and carries `allowedAgentTypes`.
- `~/claude-code/src/utils/permissions/permissionRuleParser.ts` — `permissionRuleValueFromString()`, the exact string-to-`{toolName, ruleContent}` parser behind the `Agent(a, b)` syntax; port it rather than rewriting it.
- `~/claude-code/src/tools/AgentTool/builtInAgents.ts` — `getBuiltInAgents()`, the roster-assembly pattern (a short explicit list, each entry conditionally included) to model `researcher`/`refactorer` availability on.

### Phase 6 — Lifecycle extensions

Frontmatter hooks with `on_start` context injection; run-scoped `agents/memory.py`; context forking at the three sites with incomplete-tool-call filtering.

**Steps:**
1. Add a `hooks:` frontmatter field supporting only `on_start` — a deliberately narrow slice of a much larger hook-event vocabulary that exists elsewhere (pre/post-tool-use, session lifecycle, subagent lifecycle, etc.); TDDAgents needs exactly one of those events, so only port that one.
2. Hook shape: a named callback invoked at agent start that returns text to inject into the initial message list — skip the command-execution/`if`-condition-filter machinery entirely, since TDDAgents' hooks are Python callables, not shell commands.
3. `agents/memory.py` — run-scoped only: a single memory directory per run (`thread_id`), holding one file per agent type, loaded at agent start and discarded at run end. This deliberately drops the persistent user/project/local scope split that exists elsewhere — TDDAgents' 3-runs-per-task research design requires every run to start uncontaminated, so persistent scopes are explicitly out.
4. Context forking at the three sites (recovery researcher, refactorer, reviewer): build the forked message list by keeping the parent's full context and appending a directive for the fork target — cache-stable placeholder content for anything not meant to vary between forks, with the per-fork instruction appended last.
5. Filter incomplete tool calls before forking: drop any assistant message whose tool-use blocks don't all have a matching tool-result, so a fork is never handed a dangling tool call that would be rejected by the API. Port this filter function directly rather than reimplementing it — it's a small, easy-to-get-subtly-wrong piece of message-list surgery.

**claude-code reference:**
- `~/claude-code/src/schemas/hooks.ts` and `~/claude-code/src/entrypoints/sdk/coreSchemas.ts` (`HOOK_EVENTS`) — the full hook-event vocabulary and hook-command shape (`type: 'command'`, `if`, `timeout`, `once`, `async`); read these to confirm `on_start`'s closest analogue (subagent-start-time injection) and deliberately leave the rest unported.
- `~/claude-code/src/tools/AgentTool/agentMemory.ts` — `getAgentMemoryDir()` / `loadAgentMemoryPrompt()`, the direct structural model for `agents/memory.py`, collapsed from three persistent scopes down to one run-scoped one.
- `~/claude-code/src/tools/AgentTool/forkSubagent.ts` — `buildForkedMessages()`, the cache-stable fork-message-construction pattern (placeholder content + per-child directive appended last).
- `~/claude-code/src/tools/AgentTool/runAgent.ts` — `filterIncompleteToolCalls()`, ported directly: drops assistant messages with orphaned tool-use blocks before they're used as fork context.

### Phase 7 — Unify the requirements phase

Fold analyst/user_input/engineer into the main graph, replace the blocking `input()` at [requirements_user_input.py:29](app/graph/nodes/requirements_user_input.py#L29) with `interrupt()` + `Command(resume=...)`, delete `RequirementsState` and the second orchestrator. The whole run becomes checkpointed and resumable end to end.

**Steps:**
1. Fold `analyst`, `user_input`, and `engineer` into the main graph as ordinary nodes, removing the separate `RequirementsOrchestrator` graph entirely.
2. Replace the blocking `input()` call at `requirements_user_input.py:29` with LangGraph's `interrupt()`, which suspends graph execution and returns control to the caller instead of reading stdin inline.
3. `main.py` resumes the paused graph with `graph.invoke(Command(resume=user_response), config=...)` instead of a second, separate `orchestrator.run()` call.
4. Delete `RequirementsState` and `graph/subgraphs/requirements_orchestrator_subgraph.py`; the whole run — requirements gathering through TDD execution — becomes one checkpointed, resumable graph.

This phase is primarily LangGraph plumbing rather than a claude-code port, so no file-level citation is forced here. The one loose conceptual parallel worth keeping in mind while designing `Command(resume=...)`'s payload: a structured question/typed-response tool (offering discrete options rather than parsing free text) is a more robust human-in-the-loop shape than blocking on raw stdin — worth considering whether the resume payload should carry a structured choice (matching the `/yes`-vs-free-edit split Phase 0 already established) rather than an unstructured string.

### Phase 8 — Metrics

Event bus, collector, report writers; delete the wrappers and `is_flow_type`.

**Steps:**
1. `metrics/events.py` — typed event dataclasses (`AgentStarted`, `ToolCalled`, `ToolResultTruncated`, `SkillInvoked`, `SkillActivatedByPath`, `AgentFinished`, `DelegationDecided`, `RedConfirmed`, `GreenPassed`, `RefactorReverted`, `SubReqCompleted`), each a single typed emission point rather than an ad hoc print/log statement.
2. Emit events from nodes, `run_agent()`, and every tool call at the relevant lifecycle transition.
3. `metrics/collector.py` — aggregation that accumulates typed events and derives a summary object on demand: delegation tree per sub-requirement, turns/tool-calls/tokens per agent, skill-invocation frequency, tool failure rates, refactor accept-vs-revert rate.
4. Delete the four `wrapper_*` functions and the `is_flow_type` list; flow classification (F1/F2) becomes a derivation over the event log instead of being computed and threaded through state inline.
5. `metrics/reports.py` — report writers that read the finished event log at run end and produce the paper-facing artifacts.

**claude-code reference:**
- A typed single-emission-point-per-event pattern (name + flat metadata dict) is the shape to mirror for `metrics/events.py` — the same principle behind any structured analytics/telemetry call: one function, one event name, one metadata payload, no ad hoc string logging standing in for structured data.
- `~/claude-code/src/cost-tracker.ts` — per-model usage rollup (`getModelUsage`, `getUsageForModel`, total-cost aggregation) as the structural model for `metrics/collector.py`: accumulate typed events, then derive summaries on demand rather than maintaining running totals inline in business logic.

### Phase 9 — Cleanup

Remove dead modules and config constants; confirm CLAUDE.md matches the shipped architecture.

**Steps:**
1. Remove dead modules: `app/utils/workspace.py`, `app/utils/spec_loader.py` (confirmed unused since Phase 0; deletion deliberately deferred to here).
2. Remove dead config constants: `Config.WORKSPACE_PATH`, `Config.PLAN_KEY`.
3. Grep for any remaining references to symbols deleted across earlier phases (`AgentAction`, `apply_agent_action_to_sandbox`, `RequirementsState`, the `wrapper_*` functions) to confirm nothing dangling survived the incremental rollout.
4. Rewrite CLAUDE.md's Architecture section to match the shipped code — the first point since the refactor started where the document and the source are back in sync.

No claude-code reference applies — this is a TDDAgents-internal cleanup pass.
