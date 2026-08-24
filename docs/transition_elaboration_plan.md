# TDDAgents → claude-code-style Agent, Tool, and Skill Architecture

## Context

TDDAgents drives Red–Green TDD through a fixed LangGraph pipeline: `planner → (tester → runner_red → developer → runner_green) → evaluator`. Every transition is a hardcoded `status` string literal read by a router function; every agent is a single `.with_structured_output(AgentAction)` call emitting `files_to_write` / `bash_commands` / `dependencies`; and every agent's only view of the workspace is `AgentState["file_system"]` — a dict containing *only files the agents themselves wrote*.

Three structural consequences follow:

1. **Agents are blind.** Files created by `bash_commands` never enter the mirror, are never shown back to the model, and are never extracted to `workspace_output_*`. The `execution_logs` returned by `apply_agent_action_to_sandbox` are discarded at both call sites ([execute_tester.py:48](app/graph/nodes/execute_tester.py#L48), [execute_developer.py:39](app/graph/nodes/execute_developer.py#L39)) — an agent can never see the output of a command it ran.
2. **Context growth is quadratic.** Every turn re-renders the whole codebase into the human message *and* appends the full JSON action (with complete file contents) as the AI message, up to `MAX_ITERATIONS=15` per sub-requirement.
3. **Roles and knowledge are fixed at compile time.** Adding a specialization means a new node, new status literals, and new router branches across two files. There is no mechanism for domain knowledge at all.

The target is to port claude-code's agent/tool/skill architecture onto TDDAgents: declarative agent definitions, a real tool protocol with per-agent scoping, delegation as a scoped tool, three-level skills, layered context injection, and explicit agent lifecycles — while the Red→Green invariant the research measures stays structurally guaranteed rather than prompt-guaranteed.

**Decisions locked with the user** — rounds 1–6 settled the architecture as a whole; round 7 elaborated Phase 1 and is marked ⁽⁷⁾ below.

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
| `permission_mode` | Gates *what*: `read_only` \| `workspace_write` \| `full` |
| Skills | 7 skills; model-invoked `Skill` tool + path-conditional activation + supervisor selection |
| Skill depth | `SKILL.md` + `references/` + `scripts/`; 2 authored, 5 scaffolded |
| Extensions in | Frontmatter hooks, run-scoped agent memory, context forking |
| Extensions out | MCP servers, async/background agents (sequential delegation only) |
| Language | English everywhere — prompts, logs, docstrings, comments, CLI, benchmark specs |
| Execution targets ⁽⁷⁾ | Two: the E2B sandbox **and** the local host. Generated code still runs only in the sandbox |
| `workspace` ⁽⁷⁾ | Gates *where*: `sandbox` \| `local` \| `both`, declared per agent definition, inherited by its tools, no per-call override. Orthogonal to `permission_mode` |
| Local execution ⁽⁷⁾ | `LocalWorkspace` reaches full parity including `execute()`, unrestricted — see the risk note below |
| Sync ⁽⁷⁾ | Bidirectional local↔sandbox at deterministic checkpoints; no watchers, no background threads |
| Conflicts ⁽⁷⁾ | Sandbox wins during a run (timestamped local backup + logged event); local wins outside a run |
| Tool roster ⁽⁷⁾ | 18 tools; `Tool` protocol is the source of truth, `to_langchain_tool()` adapts it for `bind_tools` |
| E2B client ⁽⁷⁾ | `e2b_code_interpreter.Sandbox` everywhere; `run_code` exposed as `RunCode` |
| 1A scope ⁽⁸⁾ | Additive **+ repoint**: `orchestrator.py`, `runner.py`, `sandbox_utils.py`, `errors/sandbox/handler.py` move onto the adapter, so the single-seam invariant holds from 1A rather than from Phase 9 |
| Local root ⁽⁸⁾ | `.tddagents/runs/<thread_id>/workspace` is the live local mirror; `workspace_output_<thread_id>/` stays exactly what it is today, the end-of-run export |
| Sync wiring ⁽⁸⁾ | 1A ships the engine as a **library**; no checkpoint is called from the running pipeline until its consumer exists (tool calls in 1B, the rest in Phase 2) |
| Sandbox TTL ⁽⁸⁾ | `SANDBOX_TIMEOUT = 3600` (the E2B Hobby cap), slid forward by a lazy `refresh_timeout()` inside the adapter every `SANDBOX_REFRESH_INTERVAL = 600`s — no threads, so the cadence stays replayable |
| Command timeout ⁽⁸⁾ | `COMMAND_TIMEOUT = 300` adapter default with a per-call override; `TEST_TIMEOUT = 600` for pytest |
| Path semantics ⁽⁸⁾ | `SANDBOX_WORKSPACE_ROOT = "/home/user"` pinned and `cwd` passed explicitly; `user="root"` kept, since that is what every recorded run relied on |
| Sync exclusions ⁽⁸⁾ | A `.gitignore` inside the generated workspace when present, `Config.SYNC_EXCLUDE_FALLBACK` otherwise; `.git` excluded unconditionally |
| Verification ⁽⁸⁾ | An offline `pytest` suite under `tests/` — the repo's first tests. No credentials, no sandbox cost |
| Background cmds ⁽⁹⁾ | `BashOutput` / `KillShell` sandbox-pinned like `RunTests`; `Workspace` unchanged, `LocalWorkspace` stays foreground-only |
| Web tools ⁽⁹⁾ | Tavily via a thin `httpx` client; `WebFetch` via `httpx` + beautifulsoup4 + markdownify. Both self-disable without a key |
| `permission_mode` ⁽⁹⁾ | Tri-level capability `read` \| `write` \| `execute`, per-input for `Bash`. `RunTests` exempt, so the refactorer stays `workspace_write` |
| `HostRead` ⁽⁹⁾ | Unrestricted host reads, no path allowlist — the `workspace` field is the only boundary |
| Executor ⁽⁹⁾ | `partitionToolCalls` ported verbatim; concurrent batches capped at 10; **no sibling abort**; results re-sorted into the model's call order. Phase 1B ships no streaming; Phase 2 adds a streaming executor **beside** this one, not in place of it |
| Tool hooks ⁽⁹⁾ | Shell-based PreToolUse/PostToolUse dispatcher — **supersedes the Phase 6 "on_start only" scope**, see ⁽⁹⁾ there |
| Verification ⁽⁹⁾ | Offline suite is the gate; `scripts/verify_tools_e2b.py` is a separate opt-in live harness |
| Streaming ⁽¹⁰⁾ | `run_agent` becomes a **sync event-yielding generator** over `llm.stream()`; tools dispatch eagerly onto the existing ThreadPoolExecutor. Not async — Phase 1A's sync E2B/subprocess seam is not reopened |
| Dispatch signal ⁽¹⁰⁾ | `tool_call_chunk` **index advance** + end-of-stream, then strict `json.loads`. Never `.tool_calls`, which partial-parses and makes a truncated call look valid |
| Sibling abort ⁽¹⁰⁾ | Ported **exactly**: Bash errors only, cancelled siblings get synthetic results. Revises the Phase 1B "no sibling abort" decision, which rested on a misreading of the source |
| Event transport ⁽¹⁰⁾ | `get_stream_writer()` inside a graph, `ToolContext` sink callable outside one. Transient events never enter node state |
| Streaming fallback ⁽¹⁰⁾ | On stream failure: `discard()` → synthetic markers → retry the turn on the batch executor. Both executors share `execute_tool`, the partition predicates and the permission gate |

⁽⁸⁾ Settled in the Phase 1A implementation interview, after rounds 1–7.

⁽⁹⁾ Settled in the Phase 1B implementation interview.

⁽¹⁰⁾ Settled in the Phase 2 streaming interview, after Phase 1B shipped. Two of these rows
revise earlier decisions: the "no sibling abort" and "no streaming" positions were both
taken on the belief that claude-code had neither, which came from reading
`toolOrchestration.ts` and missing `StreamingToolExecutor.ts`. The source was re-read and
the decisions retaken on what it actually does.

⁽⁷⁾ **Accepted risk, recorded deliberately.** Unrestricted local `execute()` with no per-call override
means the only thing between an agent and an arbitrary host command is the `workspace:` value in its
definition file — a Phase 2 frontmatter typo becomes a host-execution event. The user chose this after
the trade-off was raised. The mitigation is structural, not procedural: `tester`, `developer`, and
`refactorer` are pinned to `sandbox`, and the phase-filtered registry is what enforces it.

`experimental_executions/` and `mutation_tests/` stay on disk untouched as frozen paper artifacts; they will no longer be re-runnable from the new code, which the user accepted.

---

## 1. Architectural Comparison

| Concern | claude-code | TDDAgents today | Target |
|---|---|---|---|
| Agent definition | Markdown + YAML frontmatter → `AgentDefinition` (`loadAgentsDir.ts`): `tools`, `disallowedTools`, `model`, `maxTurns`, `permissionMode`, `skills` | Implicit: a node function + a Jinja2 prompt pair | `app/agents/definitions/<name>.md` — same field set, English body |
| Delegation | `AgentTool` — LLM emits `subagent_type` + `prompt`; `runAgent.ts` builds an isolated context | None | `Agent` tool in the registry, granted per agent with allowed targets |
| Tools | `Tool` protocol (`Tool.ts:362`): `call`, `inputSchema`, `description`/`prompt`, per-input `isReadOnly`/`isConcurrencySafe`, per-tool `maxResultSizeChars`; ~40 tools | Three fields on one Pydantic model, applied by the node; 0 tools | `app/tools/base.py::Tool` — same protocol, 18 tools |
| Tool scoping | `resolveAgentTools()` — allowlist ∩ available, minus denylist, `*` wildcard | None; every agent has identical powers | Same resolution, plus `permission_mode` and `workspace` as two orthogonal enforcement floors |
| Turn execution | Streamed; each `tool_use` dispatched at its `content_block_stop`, so tool *n* runs while tool *n+1* is still arriving (`StreamingToolExecutor`) | One blocking `.with_structured_output()` call per node; nothing runs until the whole response lands | `llm.stream()` + eager dispatch on `tool_call_chunk` index advance, results buffered in receipt order |
| Execution environment | The host, directly | E2B sandbox only, reached ad hoc from three modules | `Workspace` abstraction over `E2BWorkspace` + `LocalWorkspace`, one adapter, checkpointed sync between them |
| Context isolation | Own message list per subagent; only a final report returns | Three `add_messages` channels shared for the whole sub-requirement | Self-contained `run_agent`; summary-only return + forking at 3 sites |
| Environment injection | `computeEnvInfo()` → `<env>`, appended by `enhanceSystemPromptWithEnvDetails()`; plus `getSystemContext()` / `getUserContext()` | The rendered file mirror, nothing else | `<env>` + spec + `CONVENTIONS.md` + workspace state summary |
| Lifecycle | `runAgent`'s `finally`: hook clearing, file-state release, task kill, registry eviction | None | `AgentRunResult` + explicit teardown |
| Budget | `maxTurns` per agent in frontmatter | `MAX_ITERATIONS=15` cycle-wide | `max_turns` per agent + cycle budget retained |
| **Skills** | `skills/<name>/SKILL.md`, 3-level progressive disclosure; `paths:` conditional activation | **Nothing** | 7 domain skills, same 3 levels, same activation model |

Two patterns are imported in spirit, not just in shape:

- **Capability as structure, not instruction.** The Developer's prompt currently says *"never invoke the test runner yourself."* Under tool scoping, `RunTests` simply is not in the Developer's tool list — the same way Explore's read-only guarantee comes from `disallowedTools` ([exploreAgent.ts:67](/home/amaro/claude-code/src/tools/AgentTool/built-in/exploreAgent.ts#L67)) while its prompt merely explains it. Every prompt rule that can become a tool boundary should.
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
  sandbox/
    adapter.py               # E2BAdapter — the ONLY module that imports e2b*
  workspace/
    base.py                  # Workspace protocol, CommandResult, WorkspaceError
    e2b.py                   # E2BWorkspace  → E2BAdapter
    local.py                 # LocalWorkspace → pathlib + subprocess
    router.py                # sandbox | local | both → a Workspace instance
  sync/
    engine.py                # checkpointed bidirectional sync + ledger reconciliation
    baseline.py              # {path: sha256} snapshot; conflict detection
  tools/
    base.py                  # Tool protocol, build_tool/TOOL_DEFAULTS, ToolContext,
                             # ToolResult, result persistence
    registry.py              # name → Tool; resolve_tools(definition)
    langchain.py             # to_langchain_tool() shim for llm.bind_tools()
    agent_tool.py            # delegation (Phase 5)
    skill_tool.py            # skill invocation (Phase 4)
    fs.py                    # ReadFile ListDir Glob Grep WriteFile Edit MultiEdit
                             # Delete Move
    exec.py                  # Bash BashOutput KillShell RunCode
    run_tests.py             # RunTests — sandbox-pinned, bypasses the router
    host_read.py             # HostRead — read-only host access (researcher)
    web.py                   # WebSearch / WebFetch (researcher)
    todo.py                  # TodoWrite — in-process scratchpad
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

### 2.3 Tool protocol, scoping, `permission_mode`, and `workspace`

```python
class Tool(Protocol):
    name: str
    args_schema: type[BaseModel]                       # Pydantic ≙ Zod inputSchema
    max_result_chars: int                              # per tool, NOT one global number
    def description(self, args: BaseModel) -> str: ... # short, input-aware
    def prompt(self) -> str: ...                       # the long model-facing text
    def call(self, args: BaseModel, ctx: ToolContext) -> ToolResult: ...
    def is_read_only(self, args: BaseModel) -> bool: ...        # per INPUT, not per tool
    def is_concurrency_safe(self, args: BaseModel) -> bool: ...
    def is_destructive(self, args: BaseModel) -> bool: ...
    def validate_input(self, args: BaseModel, ctx: ToolContext) -> ValidationResult: ...
    def check_permissions(self, args: BaseModel, ctx: ToolContext) -> PermissionResult: ...
```

Three details are taken from the real `Tool.ts` rather than from a paraphrase, because each one
changes an implementation decision:

- **`is_read_only` takes the input.** `BashTool.isReadOnly(input)` parses the command and returns
  whether it satisfies read-only constraints ([BashTool.ts:437](/home/amaro/claude-code/src/tools/BashTool/BashTool.tsx#L437));
  `isConcurrencySafe` then just delegates to it ([:434](/home/amaro/claude-code/src/tools/BashTool/BashTool.tsx#L434)).
  So `Bash` is not "never read-only" — `Bash("ls")` is read-only and parallelizable while
  `Bash("rm -rf")` is neither, and only a per-input predicate can express that.
- **`max_result_chars` is per tool.** Read `Infinity`, Grep `20_000`, Bash `30_000`, everything else
  `100_000`. Read is unbounded on purpose: persisting its output would create a circular
  Read→file→Read loop, and the tool already self-bounds ([Tool.ts:466](/home/amaro/claude-code/src/Tool.ts#L466)).
- **`description` and `prompt` are two different things** — a short input-aware line for display and
  logging, and the long text the model actually reads. Collapsing them into one string field loses
  the ability to log a call compactly.

`build_tool()` fills the commonly-stubbed methods with **fail-closed** defaults, so a tool definition
can omit them and still be safe: `is_enabled → True`, `is_concurrency_safe → False`,
`is_read_only → False`, `is_destructive → False`, `check_permissions → allow`,
`user_facing_name → name` ([Tool.ts:757](/home/amaro/claude-code/src/Tool.ts#L757)).

`ToolContext` carries the resolved `workspace`, `sandbox_id`, the ledger, `agent_id`,
`permission_mode`, workdir, and remaining budget. `resolve_tools(definition)` mirrors
`resolveAgentTools`: denylist first, wildcard short-circuit, then allowlist ∩ registry — and
**unknown tool names are collected, not fatal** (`ResolvedTools(has_wildcard, valid, invalid, resolved)`).

**Two orthogonal enforcement axes.** `permission_mode` gates *what*; `workspace` gates *where*.
Neither is a duplicate of the tool allowlist, and both are checked at `ToolContext` level so a
mistaken frontmatter entry cannot grant capability:

|  | `read_only` | `workspace_write` | `full` |
|---|---|---|---|
| **`sandbox`** | read sandbox | + write sandbox | + execute in sandbox |
| **`local`** | read host | + write host | + **execute on host** |
| **`both`** | read either | + write either | + execute either |

A `read_only` + `local` agent reads host files and can do nothing else with them. `full` + `local` is
the one cell that runs unrestricted commands on the developer's machine; it is granted to no agent in
the roster below. This makes "the Researcher is read-only" auditable rather than declarative — defense
in depth exactly where a prompt-level rule would silently fail.

**Result governance.** A result exceeding that tool's `max_result_chars` is written to a sandbox file;
the agent receives a head/tail preview plus the path, which it can `ReadFile` selectively. This matters
most for `RunTests`: `-vv --tb=long --showlocals` output is both the largest and the most important
thing an agent reads. Tools whose `is_concurrency_safe(input)` is True execute in parallel within one turn.

**Ledger reconciliation fixes the lost-files bug** — but it belongs to the sync engine (§Phase 1A),
not to each tool. A write-capable tool call is one of the four sync checkpoints, so files created by
`Bash` land in `file_system` and therefore in `workspace_output_*`.

Per-agent scoping:

| Agent | Tools | permission_mode | workspace |
|---|---|---|---|
| supervisor | `Agent(researcher,refactorer)`, ReadFile, ListDir | read_only | sandbox |
| researcher | ReadFile, ListDir, Glob, Grep, Bash, HostRead, WebSearch, WebFetch, Skill | read_only | both |
| tester | ReadFile, ListDir, Glob, Grep, WriteFile, Edit, Skill — **no RunTests** | workspace_write | **sandbox** |
| developer | ReadFile, ListDir, Glob, Grep, WriteFile, Edit, MultiEdit, Bash, RunCode, Skill — **no RunTests** | full | **sandbox** |
| refactorer | ReadFile, ListDir, Glob, Grep, WriteFile, Edit, MultiEdit, RunTests, Skill | workspace_write | **sandbox** |
| reviewer | ReadFile, ListDir, Glob, Grep | read_only | sandbox |

The three bolded `sandbox` pins are load-bearing: they are what keeps generated code executing only in
the sandbox now that a local execution target exists. `RunTests` bypasses the router entirely and is
sandbox-pinned at the tool level, so it stays sandbox-only even if an agent is granted `both`.

`HostRead` is read-only and never writes. It carries **no path allowlist** — an allowlist would be
theatre next to an unrestricted `Bash` on the same host, and pretending otherwise would misrepresent
the actual boundary. The boundary is the `workspace` field, and nothing else.

### 2.4 Delegation

`Agent(subagent_type, prompt, description)` is an ordinary registry tool. It is granted only where frontmatter lists it, and the grant carries its targets using the `Agent(researcher,refactorer)` spec syntax that `permissionRuleValueFromString` already parses in `resolveAgentTools`. Tester and Developer are never granted it, so the sealed core cannot fan out. Delegation is **sequential** — the call blocks until the subagent returns.

### 2.5 `run_agent` — lifecycle

`run_agent` is a **generator**: it yields transient `StreamEvent`s while a turn is in
flight and returns an `AgentRunResult` when the loop ends. Streaming is not decoration —
it is what lets a tool start executing while the *next* tool call's arguments are still
arriving on the wire. See §Phase 2 for the mechanism and its hazards.

```python
def run_agent(definition, task_prompt, ctx) -> Iterator[StreamEvent]:
    tools = resolve_tools(definition)
    messages = build_initial_messages(definition, ctx)   # system + env + project context
    messages += run_start_hooks(definition, ctx)         # injected context
    if definition.fork_from:
        messages = fork_context(ctx, definition.fork_from) + messages
    if definition.memory:
        messages[0] += load_memory(definition.name, ctx.thread_id)
    llm = get_chat_model(Config.CHAT_MODEL, model=definition.model or Config.MODEL).bind_tools(tools)
    executor = StreamingToolExecutor(registry, ctx)
    try:
        for turn in range(definition.max_turns):
            acc, dispatched = None, 0
            for chunk in llm.stream(messages):            # sync generator, not async
                acc = chunk if acc is None else acc + chunk
                yield TextDelta(chunk.content)
                # Dispatch on index-advance ONLY — never on `.tool_calls`, which
                # partial-parses. See the hazard note in §Phase 2.
                dispatched += executor.add_newly_complete(acc, after=dispatched)
            executor.flush_final(acc)                     # the last index has no successor
            messages.append(acc)
            yield from executor.drain_completed()         # receipt order, not completion order
            messages += executor.results_in_receipt_order()
            if not acc.tool_calls:
                break
        return AgentRunResult(summary=..., turns=..., tool_calls=..., files_touched=...)
    finally:
        clear_agent_hooks(definition, ctx)
        release_forked_context(ctx)
        flush_events(ctx)
        kill_background_commands(ctx)     # any lingering sandbox process
        evict_result_cache(ctx)
```

**Transient events and durable state are separate channels.** `StreamEvent`s go out through
`langgraph.config.get_stream_writer()` — or a `ToolContext` sink callable when there is no
graph, as in tests — and never enter node state. Only `AgentRunResult` crosses back into
`AgentState`. The three `add_messages` channels are removed from state entirely — cross-iteration continuity is carried by explicit `feedback` fields and `agent_summaries`. This eliminates the quadratic growth and makes `_clear_agent_histories` / `RemoveMessage` unnecessary.

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

Phase 2's streaming loop contributes five more, and they arrive free because the runtime is
already emitting `StreamEvent`s for the console: `StreamStarted`, `FirstTokenReceived`,
`ToolDispatchedEarly`, `SiblingCancelled`, `StreamingFallback`. Token counts come from stream
metadata (`stream_usage=True`) rather than a separate accounting pass.

The four `wrapper_*` functions and the sparse `is_flow_type` list indexed by `plan_index` are deleted; flow classification becomes a derivation over the event log. New dimensions: delegation tree per sub-requirement; turns/tool-calls/tokens per agent; skill invocation frequency and which skills correlate with green-on-first-try; tool failure rates; refactor accept-vs-revert rate. Streaming adds two more that speak to responsiveness rather than correctness: time-to-first-token per agent, and how often eager dispatch actually overlapped a tool with the generation of the next one.

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

Phase 1 has two execution environments to serve, not one: agent-authored code still runs only in the
E2B sandbox, but agents and their tools may also operate against the local host. That makes the phase
substantially larger than its neighbours, so it splits at its real dependency seam — tools sit on top
of a workspace, so the workspace lands first:

- **Phase 1A — execution substrate.** E2B adapter, sandbox lifecycle, the `Workspace` abstraction and
  its two implementations, the sync engine.
- **Phase 1B — agent-facing tools.** `Tool` protocol, registry and `resolve_tools`, the 18 tools.

Both are independently runnable and verifiable with no graph changes.

#### Phase 1 audit — the tool inventory

*This is the pre-implementation inventory. It records what exists, what each tool will be backed by,
and which capabilities the E2B SDK does not cover.*

**There is no prior tool layer to migrate.** No `app/tools/` exists. The entire agent capability
surface today is five latent operations, reachable only through the fields of one Pydantic model and
applied by one function — never as tools the model can call:

| # | Capability today | Site | E2B call | Becomes |
|---|---|---|---|---|
| 1 | `dependencies` → `pip install` | [sandbox_utils.py:27-30](app/utils/sandbox_utils.py#L27-L30) | `commands.run` | `Bash` |
| 2 | `files_to_write` → `mkdir -p` + write | [sandbox_utils.py:33-44](app/utils/sandbox_utils.py#L33-L44) | `files.write` | `WriteFile` (keeps the auto-`mkdir -p`) |
| 3 | `bash_commands` — **output discarded** at both call sites | [sandbox_utils.py:47-52](app/utils/sandbox_utils.py#L47-L52) | `commands.run` | `Bash`, with the result actually returned |
| 4 | `run_pytest_in_sandbox` — node-invoked, never agent-invoked | [runner.py:17](app/agents/langgraph/runner.py#L17) | `commands.run` | `RunTests`, sandbox-pinned |
| 5 | `read_all_files_from_state` — force-rendered into every prompt | [sandbox_utils.py:62](app/utils/sandbox_utils.py#L62) | none | `ReadFile` / `Grep` — pull, not push (Phase 3) |

There is no duplicated functionality to consolidate; the surface is too small for that. The one
consolidation available runs the other way — capabilities 1 and 3 are the same operation and collapse
into a single `Bash`.

**Roster categorization**, verified against the installed SDK (`e2b==2.13.3`,
`e2b-code-interpreter==2.4.1`) rather than against the TypeScript example that prompted this work:

*Native E2B — one SDK call, the adapter is a thin wrapper*
`ReadFile` (`files.read`) · `WriteFile` (`files.write`, `files.write_files` for batches) ·
`ListDir` (`files.list(path, depth)`) · `Delete` (`files.remove`) · `Move` (`files.rename`) ·
`Bash` (`commands.run`) · `BashOutput` (`commands.connect`) · `KillShell` (`commands.kill`) ·
`RunCode` (`run_code` — the stateful Jupyter-style REPL that `e2b_code_interpreter` adds over `e2b`)

*E2B-backed but composed — no SDK primitive exists*
`Grep` and `Glob` (over `commands.run` with `grep -rn` / `find`; the SDK has no content search) ·
`Edit` and `MultiEdit` (read → string-replace → write, because `files.write` is full-overwrite only) ·
`RunTests` (`commands.run` plus `_PYTEST_FLAGS`, reusing [runner.py](app/agents/langgraph/runner.py) verbatim)

*Local-only — no sandbox equivalent, or on the wrong side of the boundary*
`HostRead` · `WebSearch` · `WebFetch` · `TodoWrite` (in-process)

*Hybrid — one tool, both `Workspace` implementations*
Every tool in the first two groups, once an agent declares `workspace: both`. `RunTests` is the single
exception: it is hard-pinned to the sandbox regardless of the agent's `workspace`, because what it
measures is the research result.

**Three gaps the SDK does not cover**, each of which shapes a design decision below:

1. **No content search.** Grep and Glob shell out; they are not adapter wrappers, and their results
   need their own size discipline (Grep's `max_result_chars` is the tightest in the roster at 20 000).
2. **No partial write.** Every `Edit` is a read-modify-write round trip. This is what makes the
   conflict rule load-bearing rather than theoretical — an edit is not atomic against a concurrent
   change on the other side.
3. **No local↔sandbox transfer primitive.** The sync engine composes `files.read` / `files.write_files`
   for deltas and tar-over-`commands.run` for bulk moves.

#### Phase 1A — execution substrate

`app/sandbox/adapter.py`, `app/workspace/`, `app/sync/`. Nothing here is agent-facing; the deliverable
is that a `Workspace` can be handed a path or a command and behave identically whichever side it is.

**Steps:**
1. `app/sandbox/adapter.py::E2BAdapter` — the **only** module in the codebase that imports `e2b*`.
   Standardize on `e2b_code_interpreter.Sandbox` (a superset of `e2b.Sandbox`), retiring the split
   import between [runner.py:5](app/agents/langgraph/runner.py#L5) and
   [orchestrator.py:7](app/graph/orchestrator.py#L7). Surface: `create`, `connect`,
   `reuse_or_create`, `execute`, `read`, `write`, `write_many`, `list`, `remove`, `rename`, `exists`,
   `info`, `kill`. Agents and tools never see an E2B type; swapping the sandbox provider later means
   rewriting this file and nothing above it.
2. Own the sandbox lifecycle, fixing three live defects. **(a)** `Sandbox.create()` at
   [orchestrator.py:120](app/graph/orchestrator.py#L120) passes no `timeout`, so it takes the SDK
   default of 5 minutes while real runs last far longer — add `Config.SANDBOX_TIMEOUT` and refresh it
   with `set_timeout`. **(b)** `commands.run()` defaults to `timeout=60` in the installed SDK
   (`e2b/sandbox_sync/commands/command.py`) and **no call site overrides it**, so a ten-package
   `pip install` or a long test suite can be truncated at 60 seconds today — add
   `Config.COMMAND_TIMEOUT` and `Config.TEST_TIMEOUT`. **(c)** `reuse_or_create` probes
   `Sandbox.list()` / `is_running()` and, when the old sandbox is gone, provisions a fresh one;
   rehydrating it from the ledger plus the local directory needs the sync engine and is deferred
   with it.

   *Correction to an earlier draft:* defect (c) was recorded as "resuming with `--thread-id`
   reconnects to a `sandbox_id` the previous run already killed." It does not — see the Phase 2
   note below for what actually happens.
3. `app/workspace/base.py::Workspace` — `read_file`, `write_file`, `delete_file`, `list_files`,
   `exists`, `move`, `execute`. One signature per operation, one result type, one exception family,
   and identical path semantics (workspace-relative, POSIX separators) and encoding (UTF-8) on both
   sides. This is Requirement 8's compatibility guarantee expressed as a type.
4. `E2BWorkspace` delegating to `E2BAdapter`, and `LocalWorkspace` over `pathlib` + `subprocess.run`
   at full parity **including `execute()`**. Both return the same
   `CommandResult(stdout, stderr, exit_code, duration, workspace)`. The `workspace` field is what
   lets execution output from either environment travel back to the agent through a single channel,
   labelled with where it came from.
5. `app/workspace/router.py` — resolves `sandbox | local | both` to a concrete `Workspace`.
   **Forward dependency, recorded deliberately:** the mechanism lands here, but the value comes from
   `AgentDefinition.workspace`, which does not exist until Phase 2. Until then every resolution
   returns `sandbox`, which is exactly today's behavior — so Phase 1 changes no execution target on
   its own. `RunTests` never consults the router.
6. `app/sync/engine.py` — bidirectional sync at four **deterministic checkpoints** and nowhere else:
   run start (local → sandbox seed), after every write-capable tool call (sandbox → ledger
   reconciliation), each sub-requirement boundary (sandbox → local flush), and run end (full flush).
   No filesystem watchers and no background threads: a run's sync behavior has to be replayable from
   the event log, or the three-runs-per-task design stops comparing like with like.
7. `app/sync/baseline.py` — a `{path: sha256}` snapshot taken at the last successful sync, and the
   conflict rule built on it. One side changed since baseline → propagate. **Both changed → the
   sandbox wins for the duration of a run**: the local file is first copied to
   `<path>.local.<timestamp>`, the overwrite is logged, and a `SyncConflict` event is emitted for
   Phase 8 to collect. Outside a run, local wins. A deletion propagates only when the baseline proves
   the file was untouched on the other side. A sync that fails halfway leaves the baseline unadvanced,
   so the next checkpoint retries the same delta — the operation is idempotent rather than
   transactional, which is the achievable guarantee here.
8. Move ledger reconciliation (`find . -type f -newer <marker>` + read-back) into the sync engine
   rather than into each write-capable tool. This is what finally fixes the lost-files bug documented
   in CLAUDE.md: files an agent creates via `Bash` reach `file_system`, and therefore
   `workspace_output_*`, instead of vanishing with the sandbox.

**claude-code reference:** none applies. claude-code executes directly on the host and has no sandbox
adapter, workspace abstraction, or sync layer to port — this substrate is specific to TDDAgents.
The reference to read before implementing is the E2B Python SDK surface itself: `Sandbox.create`
(`timeout`, `envs`, `metadata`), `Sandbox.list` / `connect` / `is_running` / `set_timeout` /
`get_info`, `files.*`, and `commands.run(background=…)`.

#### Phase 1B — tool protocol, registry, and the 18 tools

**Steps:**
1. `app/tools/base.py::Tool` — port the field set from
   [Tool.ts:362](/home/amaro/claude-code/src/Tool.ts#L362), keeping the shape §2.3 lays out: the
   `description(input)` / `prompt()` split, and `is_read_only` / `is_concurrency_safe` /
   `is_destructive` as **per-input predicates**, not per-tool booleans.
2. Port `buildTool()` + `TOOL_DEFAULTS` ([Tool.ts:757](/home/amaro/claude-code/src/Tool.ts#L757)) as a
   Python factory with the same fail-closed defaults, so an omitted method is always the safe answer.
3. Define `ToolContext` — a trimmed `ToolUseContext`: the resolved `workspace`, `sandbox_id`, the
   ledger, `agent_id`, `permission_mode`, workdir, remaining turn budget.
4. Define `ToolResult` and implement result persistence with the **real per-tool limits** (Read
   unbounded, Grep 20 000, Bash 30 000, others 100 000), not one global number. Over-limit results are
   written to a sandbox file and returned as head/tail plus the path.
5. `app/tools/registry.py::resolve_tools(definition)` — port
   [agentToolUtils.ts:122](/home/amaro/claude-code/src/tools/AgentTool/agentToolUtils.ts#L122):
   build the denylist set first, short-circuit on `*` or an absent list, then intersect the allowlist
   with the registry. Return `ResolvedTools(has_wildcard, valid, invalid, resolved)` — unknown names
   are **collected and reported, not fatal**, so one typo cannot empty an agent's toolbelt silently.
   Dedup and sort as `assembleToolPool` does ([tools.ts:345](/home/amaro/claude-code/src/tools.ts#L345)):
   partitioned sort then `uniqBy`, for prompt-cache stability.
6. Enforce the two orthogonal axes of §2.3 at `ToolContext` level, independent of the allowlist:
   `permission_mode` rejects a tool whose `is_read_only(input)` is False under `read_only`;
   `workspace` rejects a target the agent was not granted.
7. `app/tools/langchain.py::to_langchain_tool()` — the shim `run_agent` feeds to `llm.bind_tools()`
   in Phase 2. The custom protocol stays the source of truth because LangChain has nowhere to put
   `max_result_chars`, `is_concurrency_safe`, or `check_permissions`.
8. Implement the 18 tools against `Workspace`, never against `E2BAdapter` directly. Classification
   follows the audit above: the read tools (`ReadFile`, `ListDir`, `Glob`, `Grep`, `HostRead`) are
   read-only and concurrency-safe; the write tools are neither; and **`Bash` decides per input** —
   parse the command and return whether it satisfies read-only constraints, exactly as
   [BashTool.ts:437](/home/amaro/claude-code/src/tools/BashTool/BashTool.tsx#L437) does, with
   `is_concurrency_safe` delegating to that result.
9. `RunTests` reuses `run_pytest_in_sandbox` from [runner.py](app/agents/langgraph/runner.py) as its
   body — `_PYTEST_FLAGS` and the `CommandExitException` interception are already correct — with one
   change: it calls the adapter rather than `Sandbox.connect` directly, and bypasses the router so it
   is sandbox-pinned.
10. Reclassify `CommandExitException`. A non-zero exit from an **agent-authored** command is a tool
    result carrying `exit_code`, returned to the agent to reason about — not infrastructure failure.
    Today [errors/sandbox/handler.py:31](app/errors/sandbox/handler.py#L31) raises
    `TransientInfraError` for it, burning three full LLM retries before hard-failing the pipeline.
    §2.9 promises this fix; Phase 1B is where it lands, because Phase 1B is where tool errors first
    have somewhere to go.

**claude-code reference:**
- `~/claude-code/src/Tool.ts` — the `Tool` type (`call`, `checkPermissions`, `isReadOnly`,
  `isConcurrencySafe`, `maxResultSizeChars`, and the `description`/`prompt` split) plus the
  `buildTool()` / `TOOL_DEFAULTS` factory at the end of the file.
- `~/claude-code/src/tools.ts` — `getAllBaseTools()`, `filterToolsByDenyRules()`,
  `getTools(permissionContext)`, `assembleToolPool()` (dedup + cache-stable sort) — the direct model
  for `resolve_tools`.
- `~/claude-code/src/tools/AgentTool/agentToolUtils.ts` — `resolveAgentTools()`, the resolution order
  and the `ResolvedAgentTools` return shape to mirror.
- `~/claude-code/src/tools/{FileReadTool,GrepTool,GlobTool,BashTool}/` — the concrete
  `isReadOnly` / `isConcurrencySafe` / `maxResultSizeChars` values, and `BashTool`'s per-input
  read-only parse.

#### Phase 1 verification

Phase 1 is the only phase provable in full before anything downstream exists — no graph, no LLM, no
agent definitions. A standalone harness should assert:

- **Tool surface.** Drive all 18 tools against one sandbox through `E2BAdapter`; assert input schema
  validation, result shape, error shape, exit-code handling, and path/encoding semantics.
- **Cross-environment compatibility.** Run the same assertions against `LocalWorkspace` in a temp
  directory and diff the outcomes. Requirement 8's guarantee is only real if this diff is empty.
- **Sync.** Seed local → sandbox, modify in the sandbox, flush, assert the local tree matches. Then
  modify the same file on both sides and assert the sandbox-wins rule, the `.local.<timestamp>`
  backup, and the emitted `SyncConflict`. Kill the process mid-flush and assert the next checkpoint
  retries the same delta rather than skipping it.
- **Lifecycle.** Assert a sandbox survives past the 5-minute default, and that `reuse_or_create`
  recovers when handed a killed `sandbox_id`.
- **Result governance.** Assert a >30 000-char `Bash` result is persisted and previewed, and that
  `ReadFile` output is never persisted.
- **Routing.** With no `AgentDefinition` present, assert every tool resolves to `sandbox` — Phase 1
  must not change any execution target on its own.

### Phase 2 — Agent registry + runtime

`definition.py`, `registry.py`, `runtime.py::run_agent` (loop + teardown; hooks/memory/forking stubbed). Port tester/developer/reviewer to `definitions/*.md` and the tool loop. Delete `AgentAction`, `apply_agent_action_to_sandbox`, and the three message channels from `AgentState`. **Routing stays byte-identical here** — this isolates "agents became tool-loop agents" from "topology changed" so a regression is attributable.

**Steps:**
1. `agents/definition.py::AgentDefinition` — a dataclass with `tools`, `model`, `permission_mode`, `max_turns`, `skills`, `memory`, `hooks`, plus TDDAgents-specific `phase`, `fork_from`, `revert_on_red`, and `workspace` (`sandbox` | `local` | `both`, defaulting to `sandbox`). `workspace` is the field Phase 1A's router has been waiting for; wiring it is what first makes local execution reachable, so land it together with the `sandbox` pins on tester, developer, and refactorer rather than after them.
2. Write the Markdown+YAML frontmatter parser: read frontmatter, validate required fields (`name`, `description`), and skip-with-log on a malformed file rather than crashing the whole registry — co-located reference docs or typos in one agent file must not take down agent discovery for the rest.
3. `agents/registry.py::discover()` — memoized load of all `definitions/*.md`, then `resolve_tools(definition)` (Phase 1's function) including the special-case handling needed for `Agent(researcher,refactorer)` target scoping (see Phase 5).
4. `runtime.py::run_agent()` — resolve model, build initial messages (system + env + project context — stub these for now, real content lands in Phase 3), **streaming** tool-call loop bound by `max_turns`, a teardown block. The loop is a generator over `llm.stream()`, not a `.invoke()` call; steps 10–14 below specify it.
5. Mirror the teardown shape exactly: kill any lingering sandbox process the agent spawned, clear any per-agent caches — this is the direct analogue of killing background shell tasks and clearing invoked-skill tracking when an agent finishes.
6. Port tester/developer/reviewer to `definitions/*.md` + the tool loop, using a single-purpose, explicitly-scoped built-in agent as the concrete template: a short disallow list plus a banner-style prompt section that states capability boundaries in plain language (defense in depth alongside the tool-scoping itself).
7. Delete `AgentAction`, `apply_agent_action_to_sandbox`, and the three message-channel fields from `AgentState`.
8. Keep routing byte-identical — same status literals, same router functions — so a regression is attributable to "agents became tool-loop agents," not "topology changed."
9. **Fix `--thread-id` resume, found during Phase 1A.** [orchestrator.py:123-150](app/graph/orchestrator.py#L123-L150)
   passes a fully-populated `initial_state` into `graph.invoke()` on *every* run, including
   `"file_system": {}`, `"plan": []`, `"plan_index": 0`, and `"status": "starting"`. LangGraph applies
   input keys as an update over the restored checkpoint, and none of those fields has a reducer — so a
   resume overwrites the checkpointed plan and file mirror with empties and keeps only the
   `add_messages` channels. `sandbox_id` is likewise replaced with a freshly created id, which is why
   the "reconnects to a killed sandbox" framing was wrong: the damage is upstream of the sandbox.
   The fix belongs here rather than in Phase 1A because it is a state-shape problem, and `AgentState`
   is being rewritten in this phase anyway: seed the full dict only when no checkpoint exists for the
   `thread_id`, and pass only what a resume genuinely needs otherwise.

#### Streaming — steps 10 to 14 ⁽¹⁰⁾

Phase 1B shipped the batch executor and explicitly no streaming. Phase 2 adds the streaming
path beside it, because streaming is where the tool layer stops being a request/response
loop: upstream, a tool begins executing while the *next* tool call's arguments are still
being generated.

**How claude-code actually does it** — read these three before implementing, because the
mechanism is not where you would first look for it:

- `src/services/api/claude.ts:2171` — the API stream yields **one `AssistantMessage` per
  `content_block_stop`**. Each `tool_use` block becomes its own message the moment *that
  block* closes, long before the turn ends.
- `src/query.ts:842` — `streamingToolExecutor.addTool(block, message)` fires per block as it
  arrives, gated by `config.gates.streamingToolExecution`.
- `src/services/tools/StreamingToolExecutor.ts` — the scheduler itself.

10. **`app/tools/streaming_executor.py::StreamingToolExecutor`.** Same admission rule as
    upstream `canExecuteTool`: a tool starts if nothing is executing, or if it is
    concurrency-safe and everything currently executing is too. `process_queue` walks queued
    tools in order and **stops at the first exclusive tool it cannot start yet**, so ordering
    is preserved for anything that must run alone. Surface: `add()`, `flush_final()`,
    `drain_completed()`, `remaining_results()`, `discard()`.

    Results are buffered and emitted in **receipt order, never completion order**
    (`getCompletedResults` yields in `this.tools` order and breaks at an executing exclusive
    tool). This is what protects the context window from out-of-order execution and keeps the
    message list identical across replays even though wall-clock overlap is not.

11. **Bash-scoped sibling abort.** A tool returning an error trips the sibling abort
    controller **only when it is `Bash`** (`StreamingToolExecutor.ts:359`). The rationale is
    upstream's and it transfers directly: shell commands chain implicitly — a failed `mkdir`
    makes everything after it pointless — while a failed `Grep` says nothing about a
    concurrent `ReadFile`. Cancelled siblings receive a **synthetic** result
    (`Cancelled: parallel tool call Bash(...) errored`); they are never dropped, because a
    message list with a `tool_use` and no matching `tool_result` is rejected by the provider
    outright. Three abort reasons, each with its own synthetic message: `sibling_error`,
    `user_interrupted`, `streaming_fallback`.

    *Correcting the record:* the Phase 1B interview settled on "no sibling abort", and
    `app/tools/executor.py` was built that way. That decision rested on an incorrect claim
    that no such mechanism existed upstream — only `toolOrchestration.ts` had been read, and
    `StreamingToolExecutor.ts` was missed. It was revisited once the source was read properly.
    The **batch** executor keeps its no-abort behavior; abort belongs to the streaming path.

12. **Dispatch signal — the one place this port cannot follow claude-code.** Upstream gets
    `content_block_stop` from the wire and knows precisely when a tool call is complete.
    LangChain gives no such event, and the obvious substitute is a trap:

    > **`AIMessageChunk.tool_calls` partial-parses.** It runs accumulated arguments through
    > `parse_partial_json`
    > ([langchain_core/messages/ai.py:543](/home/amaro/tdd-agents/.venv/lib/python3.12/site-packages/langchain_core/messages/ai.py#L543)).
    > A half-streamed `Grep(pattern="x", pa…` therefore presents as a **valid**
    > `Grep(pattern="x")` — no exception, nothing in `invalid_tool_calls`. Dispatching on
    > that signal would run write tools with silently truncated arguments: `WriteFile` with
    > half a file body, `Edit` with half an `old_string`.

    The completeness signal is therefore **`index` advance plus end-of-stream**: a
    `tool_call_chunk` arriving with index *n+1* proves index *n* is finished, and
    `flush_final()` releases the last index when the stream closes. Before dispatch, parse
    the accumulated argument string with **strict `json.loads`**, never the partial parser;
    a strict-parse failure is a malformed call and becomes an error result rather than an
    execution with guessed arguments.

13. **Typed `StreamEvent`s, on their own channel.** `TextDelta`, `ToolDispatched`,
    `ToolCompleted`, `ToolCancelled`, `TurnFinished`. Transport is
    `langgraph.config.get_stream_writer()` from inside a node — so `graph.stream(...,
    stream_mode="custom")` surfaces them in `main.py` without any node changing its return
    value — falling back to an optional sink callable on `ToolContext` when there is no graph,
    as in tests and `scripts/verify_tools_e2b.py`. Transient events never enter node state;
    state carries only completed, durable turn results. Token counts come from stream
    metadata (`stream_usage=True`).

14. **Streaming fallback.** `o4-mini` is a reasoning model and streaming is restricted on
    unverified OpenAI organizations — a failure mode invisible from the code. On a stream
    error or refusal: fire `on_streaming_fallback`, `discard()` the executor so every
    in-flight tool gets a synthetic `streaming_fallback` result (no dangling `tool_use_id`),
    then retry the turn on the **batch** executor from Phase 1B. The run degrades to today's
    behavior instead of dying, and the transcript stays append-only and consistent.

    Both executors stay: they share `execute_tool`, the partition predicates and the
    permission gate, and differ only in scheduling. The batch path is the fallback target and
    the strictly deterministic path for tests over the multi-agent state machine — which is
    why it is worth keeping rather than replacing.

**Routing is still byte-identical.** Streaming changes how a turn is *executed and observed*,
not the graph: same status literals, same router functions. Step 8's isolation property
holds.

**claude-code reference:**
- `~/claude-code/src/tools/AgentTool/loadAgentsDir.ts` — `BaseAgentDefinition` (frontmatter field set), `parseAgentFromMarkdown()` (frontmatter → typed definition, graceful skip on parse failure), `getAgentDefinitionsWithOverrides()` (memoized discovery, later sources override earlier by agent-type key via `getActiveAgentsFromList()`).
- `~/claude-code/src/tools/AgentTool/agentToolUtils.ts` — `resolveAgentTools()` (wildcard expansion, disallow set, the `Agent`-tool special case that carries `allowedAgentTypes`).
- `~/claude-code/src/tools/AgentTool/runAgent.ts` — the lifecycle shape (model resolution, message assembly, teardown block that kills spawned shell tasks and clears per-agent skill/cache state).
- `~/claude-code/src/tools/AgentTool/built-in/exploreAgent.ts` — a fully worked example of a narrowly-scoped built-in agent: explicit `disallowedTools`, and a system prompt with an explicit "READ-ONLY MODE" capability banner backing up the tool-level restriction.
- `~/claude-code/src/services/tools/StreamingToolExecutor.ts` — the whole streaming scheduler: `addTool()`, `canExecuteTool()`, `processQueue()`, the Bash-only `siblingAbortController.abort()` at :359, `createSyntheticErrorMessage()` for the three abort reasons, and `getCompletedResults()` emitting in receipt order.
- `~/claude-code/src/query.ts:555-600, 820-860, 1010-1030` — where the executor is constructed behind its gate, where `addTool` is called per streamed block, and where `getRemainingResults()` is drained so nothing is left unemitted.
- `~/claude-code/src/services/api/claude.ts:2171` — `content_block_stop` yielding one assistant message per finished block, which is the upstream signal this port has to reconstruct from `tool_call_chunk` indices.

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

Run-scoped `agents/memory.py`; context forking at the three sites with incomplete-tool-call
filtering; and the one remaining piece of the hook system — the agent-level `on_start`
callback.

**⁽⁹⁾ The tool-level hooks moved to Phase 1B and are already built.** This section
originally scoped hooks to a single `on_start` Python callable and ruled the
pre/post-tool-use events out entirely, on the reasoning that "TDDAgents' hooks are Python
callables, not shell commands". The user reversed that during the Phase 1B implementation
interview, after the conflict with this section was raised explicitly. Read this section as
what is *left* to do, not as the whole hook design.

What `app/hooks/` already ships, and what Phase 6 must therefore not re-specify:

- **Two events**, `PreToolUse` and `PostToolUse`, wrapping every call in `execute_tool`.
- **Shell commands, not Python callables.** A hook is any executable. The dispatcher writes
  a JSON description of the event to its **stdin** and reads the verdict from the process
  **exit code**: `0` proceed, `2` veto (PreToolUse) or flag (PostToolUse), anything else
  logged and ignored so a broken hook cannot break the pipeline.
- **Three merged settings scopes** — `~/.tddagents/settings.json` → `.tddagents/settings.json`
  → `.tddagents/settings.local.json`. Later scopes **append**, so a personal file can add a
  veto but never silence a project one.
- **A typed JSON document on stdout** refines the exit code: `permissionDecision`
  (allow/deny — no `ask`, there is no interactive prompt), `permissionDecisionReason`,
  `updatedInput` on PreToolUse, `additionalContext`, and `continue: false` on PostToolUse.
  Unparseable stdout degrades to a log line rather than an error.
- **PostToolUse cannot un-run a tool.** The side effect already landed, so the output stands
  as ground truth, the hook's stderr rides along as feedback, and the step is marked
  `hook_stopped_continuation` for the agent runtime to halt on.
- Hooks run **on the host, outside the `workspace` boundary** — they are operator
  configuration, like the shell that launched the pipeline. The merged config is snapshotted
  at construction so a run's event log can say which hooks were live.

Fields supported: `type: "command"`, `matcher`, `command`, `timeout`, `if`, `statusMessage`.
Deliberately absent: `once` (hidden dispatcher state breaks replay) and `prompt` / `http`
(an LLM call and a network dependency inside the synchronous tool path).

See CLAUDE.md § "The tool layer (Phase 1B)" for the shipped description.

**Steps:**
1. Add a `hooks:` frontmatter field supporting only `on_start` — the **agent**-level hook,
   which stays a Python callable and is deliberately *not* a shell hook. It is a different
   thing from the tool-level dispatcher above: it runs once when an agent starts, inside the
   process, and its only job is to return text for the initial message list.
2. Hook shape: a named callback invoked at agent start that returns text to inject into the
   initial message list. No `if`-condition filtering and no command execution — that
   machinery exists already in `app/hooks/dispatcher.py` and is not duplicated here.
3. `agents/memory.py` — run-scoped only: a single memory directory per run (`thread_id`), holding one file per agent type, loaded at agent start and discarded at run end. This deliberately drops the persistent user/project/local scope split that exists elsewhere — TDDAgents' 3-runs-per-task research design requires every run to start uncontaminated, so persistent scopes are explicitly out.
4. Context forking at the three sites (recovery researcher, refactorer, reviewer): build the forked message list by keeping the parent's full context and appending a directive for the fork target — cache-stable placeholder content for anything not meant to vary between forks, with the per-fork instruction appended last.
5. Filter incomplete tool calls before forking: drop any assistant message whose tool-use blocks don't all have a matching tool-result, so a fork is never handed a dangling tool call that would be rejected by the API. Port this filter function directly rather than reimplementing it — it's a small, easy-to-get-subtly-wrong piece of message-list surgery. Phase 2's streaming loop makes this sharper, not softer: a turn abandoned mid-stream can leave exactly such an orphan, which is why the streaming executor emits a synthetic result for every dispatched tool rather than dropping any.

**claude-code reference:**
- `~/claude-code/src/schemas/hooks.ts` and `~/claude-code/src/entrypoints/sdk/coreSchemas.ts` (`HOOK_EVENTS`) — the full hook-event vocabulary and hook-command shape (`type: 'command'`, `if`, `timeout`, `once`, `async`). Phase 1B ported the two tool-level events from here; what remains unported is the rest of the vocabulary (session lifecycle, subagent lifecycle) and the `once` / `async` modifiers.
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
1. `metrics/events.py` — typed event dataclasses (`AgentStarted`, `ToolCalled`, `ToolResultTruncated`, `SkillInvoked`, `SkillActivatedByPath`, `AgentFinished`, `DelegationDecided`, `RedConfirmed`, `GreenPassed`, `RefactorReverted`, `SubReqCompleted`, plus `SyncCheckpoint` and `SyncConflict` from Phase 1A), each a single typed emission point rather than an ad hoc print/log statement.
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
4. Confirm no module outside `app/sandbox/adapter.py` imports `e2b*` — the adapter's whole value is that it is the single seam to the provider, and a stray direct import silently voids it.
5. Retract CLAUDE.md's claim that agent code is executed "inside a remote E2B cloud sandbox, never on the host." Phase 1A retired it: generated code still runs only in the sandbox, but agents and tools reach the host through `LocalWorkspace`, unrestricted, gated solely by each definition's `workspace` field. State the new boundary plainly rather than deleting the old sentence — a reader who remembers the guarantee needs to be told it is gone.
6. Rewrite CLAUDE.md's Architecture section to match the shipped code — the first point since the refactor started where the document and the source are back in sync.

No claude-code reference applies — this is a TDDAgents-internal cleanup pass.
