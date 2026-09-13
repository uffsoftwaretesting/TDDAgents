# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Start-of-session ritual (mandatory)

A massive refactor of TDDAgents is under way. Before doing **any** work in this repository — reading, planning, answering a question, or editing — always:

1. **Read this entire `CLAUDE.md`.** It is the map of the current architecture and of the gotchas the refactor has to preserve or deliberately break.
2. **Read the relevant source code before changing it.** Never edit or reason about `app/` from memory or from this document alone; open the actual files. This file describes intent, the source is the truth, and during the refactor the two will drift.
3. **Start from `reference/claude-code-map.md`** whenever the task touches Claude Code behavior, agent/tool design, or a pattern worth mirroring in the refactor. The map is short, covers every subsystem, and routes you to the analysis and the code. Follow both routes — see [Claude Code reference](#claude-code-reference). Never answer a question about Claude Code internals from memory when the source is sitting in `reference/claude-code/`.
4. **Run the [Quality gate](#quality-gate-mandatory) before any code you create or refactor is considered done.** It is not optional and not a final polish step: the tests are written *with* the change, not after it.

Only after these steps should you propose a plan or start editing.

## Upcoming refactor — read before extending anything

**The architecture has pivoted.** TDDAgents is being rebuilt on claude-code's *loop
engineering architecture*: one `while True` loop as the system centre, with permissions,
hooks, context assembly, compaction, recovery and tool orchestration as systems arranged
around it. The plan is `docs/transition_elaboration_plan.md` — **read it before designing
anything new in `app/`.**

This supersedes the previous plan, which ported claude-code's *features* onto a LangGraph
pipeline while the graph stayed the enforcement mechanism.

### What this means for work in `app/` right now

| | |
|---|---|
| The graph below still runs | The [Architecture](#architecture) section describes the code as it exists today. It is accurate. Do not break it. |
| But do not invest in it | `execute_tester`, `execute_developer`, both runners, `build_tdd_subgraph`, the `wrapper_*` functions, `is_flow_type` and both router functions **dissolve into the loop**. Extending them is wasted work. |
| Shipped layers are being replaced | **At the user's explicit direction, `app/tools/`, `app/hooks/`, `app/workspace/` and `app/sync/` will be dropped** and rebuilt against the loop. They are not a foundation to build on. |
| Jinja2 is going | Prompts become Markdown (see [Prompt conventions](#prompt-conventions-incoming)). Do not author new `.jinja2` templates. |
| The quality gate does not change | It still applies to everything you write, including throwaway work. |

If a task asks you to extend something on that list, say so and propose the loop-shaped
alternative from the plan rather than quietly adding to code that is scheduled for deletion.

### The three things that change most

1. **Routing by `status` string literals is replaced** by a closed vocabulary of seven
   continue reasons and ten terminal reasons, ported from `src/query.ts`. No more router
   functions comparing strings.
2. **Red→Green stops being enforced by graph topology.** It becomes a phase ledger plus
   phase-derived deny rules plus a Stop hook that refuses to let the loop terminate — three
   independent barriers, none reachable by a model decision. This is §3.3 of the plan and it
   is the part a thesis reviewer will press on.
3. **Prompts stop being templates and become composed Markdown**, following claude-code's own
   file conventions.

### Prompt conventions (incoming)

Detailed in §4 of the plan. The short version, because it changes how you write every prompt:

- **No templating engine.** Markdown in English, with `{{PLACEHOLDER}}` substitution. An
  unknown placeholder is left visible rather than rendered empty — the opposite of the Jinja2
  failure mode documented under [Conventions and gotchas](#conventions-and-gotchas).
- **Directory per unit**: `AGENT.md` / `SKILL.md` entry file plus `references/` loaded on
  demand, mirroring `reference/claude-code/src/skills/bundled/`.
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
P=/home/amaro/tdd-agents/.venv/bin      # this repo has no venv of its own; see Commands

# 1. Unit tests — written alongside the change, never bolted on afterwards
$P/pytest tests/ -q

# 2. Lint — zero findings on the files you touched
$P/flake8 <touched files>

# 3. Types — zero errors on the files you touched
$P/mypy

# 4. Mutation testing — do the tests actually assert anything?
$P/mutmut run          # NOT `python -m mutmut`; see the note below
$P/mutmut results
```

**Step 1 — write the tests with the code.** A module that sits behind a protocol seam is
tested against a fake, never against a live LLM or sandbox. `tests/conftest.py` already
provides `FakeWorkspace` and the `make_fake_workspace` factory; reuse them instead of
writing new doubles.

**Steps 2 and 3 — touched files only.** Legacy modules carry ~250 pre-existing flake8
findings and are excluded from `[tool.mypy] files`. Clean them up when a phase rewrites
them, not before. Config lives in `setup.cfg` (flake8, `max-line-length = 120`) and
`pyproject.toml` (mypy `strict = true`, pytest, mutmut).

**Step 4 — the gate is ≥ 90% killed**, computed as `killed / (total − timeout)`. Then
**triage every survivor**: kill it with a test, or write down why it is equivalent or
unreachable. A survivor left unexamined is the one case this whole gate exists to catch.

### Exempt: the seam modules

Three places genuinely cannot be unit tested offline, because they *are* the boundary to
an external service rather than something sitting behind one:

| Module | Boundary |
|---|---|
| `app/sandbox/adapter.py` | the E2B SDK |
| `app/utils/chat_model_factory.py` | the LLM provider |
| `app/agents/langgraph/*` | the LLM itself |

Each names its exemption in its own docstring. In place of unit and mutation testing they
require **a static surface check** — assert every SDK method and kwarg they call still
exists on the installed package — **and an end-to-end pipeline run**. Everything layered
*above* them is fully testable and is not exempt: `app/workspace/e2b.py` delegates to the
adapter and is tested against a fake adapter, which is the pattern to copy.

### Recorded baseline

Measured against `app/workspace/`, `app/sync/`, `app/errors/`, `app/tools/` and
`app/hooks/`, 942 tests:

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

### Gotchas

- **Invoke mutmut as `mutmut run`, never `python -m mutmut`.** Under `-m` the module is
  `__main__`, so `mutmut.__main__` is absent from `sys.modules`; the mutation trampoline
  re-imports it in each forked child, re-runs `set_start_method('fork')`, and the whole
  run dies with `RuntimeError: context has already been set`.
- **`also_copy = ["app/"]` in `pyproject.toml` is load-bearing.** mutmut copies the
  project into `mutants/` and runs the suite there, but it walks only `paths_to_mutate`.
  Without `also_copy`, `app/config/` is never copied, every mutant dies of `ImportError`
  rather than of a real assertion, and the run reports a meaningless 100%.
- **Growing `paths_to_mutate` is part of the gate.** New tested modules must be added to
  it, or they are silently never mutated.
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
- `tests/conftest.py` seeds `OPENAI_API_KEY` / `E2B_API_KEY` / `POSTGRES_URL` before any
  `app.*` import, because `config.py` raises at import time without them.

## What this is

TDDAgents is a research prototype (UFF / SBES 2026): a multi-agent system that turns a natural-language problem description into tested Python code by driving the Red–Green phases of TDD. Agent-generated code is written and executed inside a remote **E2B cloud sandbox**, never on the host. State is checkpointed to PostgreSQL.

It is **a LangGraph pipeline today and a loop tomorrow** — the refactor rebuilds it on claude-code's loop architecture, and LangGraph is reduced to a session shell (checkpointing, `interrupt()`, plan iteration). See [Upcoming refactor](#upcoming-refactor--read-before-extending-anything).

`pytest`, `flake8`, `mypy` and `mutmut` apply to **`app/` itself**, not only to the generated workspaces. `tests/` holds an offline suite for `app/` that needs no API keys, no sandbox and no database, and runs in under a second; `setup.cfg` and `pyproject.toml` configure the four tools. See [Quality gate](#quality-gate-mandatory) for the mandatory procedure and the current baseline.

*(This reverses what this file used to say. Until Phase 1A the repository had no tests of its own and verification was purely end-to-end. That is no longer true, and treating it as true is how untested code lands.)*

Coverage is deliberately partial. The suite covers the modules that can be exercised offline; the three seam modules that talk to E2B and to the LLM are exempt and are still verified by **running the pipeline end to end**, which remains the only way to check the system as a whole. There is no build step.

## Commands

```bash
# Setup
cp .env.example .env          # then fill OPENAI_API_KEY, E2B_API_KEY, POSTGRES_URL
docker compose up -d          # PostgreSQL checkpoint store on :5432
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run the pipeline (interactive; must be run from the repo root)
python -m app.main

# Resume a previous run from its Postgres checkpoint
python -m app.main --thread-id "tdd-<hash>"

# Force a brand-new run instead of the spec-derived thread_id
python -m app.main --fresh
```

### Quality tooling

**This repository has no venv of its own.** Use `/home/amaro/tdd-agents/.venv`, which has
the full dependency set. (`/home/amaro/tdd-agents/venv` also exists but is missing
`langchain_together`, which `app/utils/chat_model_factory.py` imports at module scope, so
importing anything under `app/graph/` fails there.)

```bash
P=/home/amaro/tdd-agents/.venv/bin

$P/pytest tests/ -q                                     # offline suite, no credentials
$P/flake8 app/workspace app/sync app/sandbox app/tools app/hooks tests   # touched files
$P/mypy                                                 # strict; config in pyproject.toml
$P/mutmut run && $P/mutmut results                      # mutation testing; see the gotchas
```

The full procedure and its pass criteria are in [Quality gate](#quality-gate-mandatory).

`app.main` is interactive: it prints a menu built from `app/prompts/specs/*.txt`, then the Analyst loop reads from stdin. Type `/yes` to approve the requirements checklist and advance to the Engineer; `/exit` (or `/quit`) exits. Because the menu resolves `Path("app/prompts/specs")` relatively, the CWD must be the repo root.

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

> **This section describes the code as it runs today, and it is accurate.** Much of it is
> scheduled to dissolve into the loop — the graph topology, the `status` routing vocabulary,
> the node wrappers and the tool layer. Read
> [Upcoming refactor](#upcoming-refactor--read-before-extending-anything) first so you know
> which parts to preserve and which not to build on.

### Two graphs, run sequentially by `app/main.py`

1. **`RequirementsOrchestrator`** (`graph/subgraphs/requirements_orchestrator_subgraph.py`) — state `RequirementsState`, no checkpointer. Cycle: `analyst → user_input → (analyst | engineer)`. Human-in-the-loop is a plain blocking `input()` inside the `user_input` node, not a LangGraph interrupt. Produces `final_specification` (Markdown, contracts only).
2. **`TDDOrchestrator`** (`graph/orchestrator.py`) — state `AgentState`, checkpointed. Top level is `planner → tdd_execution (subgraph) → evaluator`, with `evaluator` looping back into `tdd_execution` for each item of `plan`.

### The TDD subgraph

`graph/subgraphs/build_tdd_subgraph.py` builds `tester → runner_red → developer → runner_green` with conditional edges. Every node is wrapped (`wrapper_tester`, `wrapper_runner_red`, …) — the wrappers exist solely to fold **resilience metrics** into the returned state (failure counters, `is_flow_type`), keeping the node functions themselves metric-free.

Two success flows are recorded per sub-requirement:
- **F1** — clean TDD: the test failed first (Red confirmed), then passed.
- **F2** — "green in red": the test passed immediately; `runner_red` still routes to the Developer with an injected warning prompt so the cycle is never skipped.

### Routing is entirely by `state["status"]` string

*(Replaced by the refactor with a closed continue/terminal vocabulary — §3.1 and §5 Part A2 of
the plan. Accurate for the current code.)*

There is no shared enum. Router functions read `status` and branch on literals. The vocabulary spans three families and **must be kept consistent between the node that sets it and the router that reads it**:
- flow: `red_confirmed`, `green_passed`, `green_failed`, `test_review_needed`, `tests_written`, `code_written`, `next_req`, `plan_complete`, `plan_complete_with_failures`
- retryable infra: `infra_error_{planner,tester,red,developer,green,analyst,engineer}` — routes the node back to itself
- terminal: `sandbox_failed`, `tester_failed`, `developer_failed`, `plan_failed`, `max_retries_exceeded`

### Layering

`graph/nodes/*` (state in → state out, error handling, logging) → `agents/langgraph/*` (build message history, call the LLM with `.with_structured_output(...)`, return `(pydantic_action, updated_history)`) → `utils/prompt_loader.py` (Jinja2) and `utils/sandbox_utils.py` (E2B I/O). Nodes never call the LLM directly; agents never touch `AgentState`.

Agents return the **full** conversation history; the node slices `updated_history[existing_len:]` and returns only the new turns, letting LangGraph's `add_messages` reducer append them. Returning the whole list would duplicate messages on every re-entry.

### Sandbox and the `file_system` mirror

`AgentState["file_system"]` is a `dict[filepath, content]` mirror of the sandbox. `apply_agent_action_to_sandbox` writes files to E2B *and* updates the mirror; `read_all_files_from_state` renders the whole mirror into every agent prompt (this is the agents' only view of the codebase). `main.py` extracts the mirror to `workspace_output_<thread_id>/` at the end — nothing is pulled back off the sandbox, so anything an agent creates via `bash_commands` rather than `files_to_write` is lost.

The sandbox is created once in `TDDOrchestrator.run` and killed in its `finally`; nodes only `Sandbox.connect(sandbox_id)`.

### The tool layer (Phase 1B) — built, never wired, now scheduled for replacement

> **Superseded.** This layer is one of the four the user has decided to drop and rebuild
> against the loop — see [Upcoming refactor](#upcoming-refactor--read-before-extending-anything).
> It is documented here because it still exists on disk and its design notes are the clearest
> record of what was learned porting `Tool.ts`; several of its decisions carry over to the
> rebuild. **Do not extend it.**

`app/tools/` and `app/hooks/` are complete and fully tested, and **nothing in the running
graph calls them**. The legacy `AgentAction` path is still what executes, so this layer
changes no pipeline behavior.

- **`tools/base.py`** — the `Tool` protocol, ported from claude-code's `Tool.ts`. Per-input
  predicates (`is_read_only`, `is_concurrency_safe`, `is_destructive`,
  `required_capability`), per-tool `max_result_chars`, and a `description(input)`/`prompt()`
  split. `build_tool()` + `TOOL_DEFAULTS` fill omitted members with fail-closed defaults.
- **`execute_tool()` is the only supported way to call a tool.** It owns the whole pipeline:
  parse → `validate_input` → PreToolUse hook → `check_permissions` → capability gate →
  workspace gate → `call` → result governance → PostToolUse hook → sync checkpoint.
  Calling `tool.call` directly bypasses all of it.
- **Two orthogonal enforcement axes**, checked at `ToolContext` level rather than during
  tool resolution, so a mistaken frontmatter entry cannot grant capability.
  `permission_mode` gates *what* on a three-level ladder (`read` < `write` < `execute`);
  `workspace` gates *where*. `Bash` reports its capability **per input**, which is what
  lets the `read_only` researcher hold it: `Bash("ls")` is a read, `Bash("pip install")`
  is refused. `RunTests` is exempt from the ladder (it runs no agent-authored command),
  which is what keeps the refactorer at `workspace_write`.
- **Three sandbox pins**: `RunTests`, `BashOutput` and `KillShell` bypass the router and
  talk to `E2BAdapter` directly. `HostRead` is the only tool pinned to `local`, and it
  carries no path allowlist — the `workspace` field is the boundary, deliberately.
- **Result governance**: a result over its tool's limit is written to
  `.tddagents/tool_results/` in the sandbox and returned as head/tail plus a path the agent
  can `ReadFile`. `.tddagents/` is in `SYNC_EXCLUDE_FALLBACK`, so tooling scratch never
  reaches `workspace_output_*`. `ReadFile` alone is unbounded — persisting it would create
  a ReadFile → file → ReadFile loop.
- **`tools/executor.py`** partitions a turn's calls exactly as `toolOrchestration.ts` does:
  consecutive concurrency-safe calls run together on a thread pool capped at
  `Config.MAX_TOOL_CONCURRENCY`, everything else runs alone. Results are re-sorted into the
  model's original call order, so the message list and event log are replayable even though
  wall-clock interleaving is not. A failing tool does **not** abort its siblings.
- **`app/hooks/`** — PreToolUse/PostToolUse as ordinary shell commands, configured in three
  merged scopes (`~/.tddagents/settings.json` → `.tddagents/settings.json` →
  `.tddagents/settings.local.json`; later scopes append, so a personal file can add a veto
  but never silence a project one). JSON payload on stdin; exit 0 proceeds, exit 2 vetoes a
  PreToolUse call or flags a PostToolUse result, anything else is logged and ignored. A
  typed JSON document on stdout refines the verdict. **This supersedes the plan document's
  Phase 6 "on_start only" scope**, at the user's explicit direction.
  - PostToolUse cannot un-run a tool: the output stands as ground truth, the hook's stderr
    rides along as feedback, and `hook_stopped_continuation` is set for Phase 2 to halt on.
  - Hooks run on the host, outside the `workspace` boundary. They are operator
    configuration, like the shell that launched the pipeline.

`CommandExitException` is reclassified **on the tool path only**: a non-zero exit is a
`ToolResult` carrying `exit_code`. `utils/sandbox_utils.py::_run_or_raise` still escalates
it to `TransientInfraError` for the legacy nodes, and goes away with them in Phase 2.

### Error taxonomy

`errors/exceptions.py` defines `TransientInfraError` (retry, capped by `Config.MAX_INFRA_RETRIES`, with a 3s sleep) and `FatalInfraError` (abort to a terminal status). Two classifiers map SDK exceptions onto them: `errors/agents/handler.py` for LLM/LangChain/LangGraph errors and `errors/sandbox/handler.py` for E2B. Both **always raise** — call them from inside `except` blocks and never expect a return. `handler.py` deliberately re-raises `GraphBubbleUp` untouched so LangGraph control flow is not swallowed. Note the E2B `CommandExitException` (non-zero exit from pytest) is *expected* in TDD and is intercepted in `agents/langgraph/runner.py` before reaching the classifier.

### State and config

`config/config.py` holds both `Config` and the two `TypedDict` state schemas. It **raises at import time** if `OPENAI_API_KEY`, `E2B_API_KEY`, or `POSTGRES_URL` are unset — importing anything under `app/` without a `.env` fails immediately. Model selection is hardcoded there (`CHAT_MODEL = "openai"`, `MODEL = "o4-mini"`), not read from the environment; switching providers means editing `Config` and passing the right kwargs through `utils/chat_model_factory.py`.

Postgres is not required: `TDDOrchestrator.run` falls back to `InMemorySaver` on `OperationalError`, losing cross-restart resumability.

`node_execute_progress_evaluator` clears all three agent histories with `RemoveMessage` when advancing to the next sub-requirement — each sub-requirement gets a fresh context window.

## Conventions and gotchas

- **Everything is in English** — prompts, logs, docstrings, comments, identifiers, and type names. (Phase 0 of the refactor migrated this codebase from a prior Portuguese-prompts/English-identifiers split; match English everywhere when adding code.)
- **Prompt templates are the behavior.** Agent role definitions live in `app/prompts/agents/langgraph/<agent>/{sys,hum}_prompt_*.jinja2`, not in Python. The Tester has separate `normal` and `review` variants; the orchestrator has standalone `feedback_*.jinja2` templates that are injected into `reviewer_messages` as synthetic feedback. **This is the current state, not the target** — Jinja2 is removed by the refactor and prompts become Markdown. Do not author new `.jinja2` templates; see [Prompt conventions](#prompt-conventions-incoming).
- The Jinja2 `Environment` in `prompt_loader.py` uses default (non-strict) undefined, so a misspelled kwarg **silently renders as an empty string**. There is already one such live bug: `agents/langgraph/developer.py:35` passes `sub_requsite=` while `developer/hum_prompt_1.jinja2` expects `sub_requisite`, so the Developer's first prompt has a blank sub-requirement block. Verify kwarg names against the template when touching either side. **This bug is the reason the refactor drops Jinja2** — §4.1 of the plan cites it as the motivating failure mode.
- The Reviewer signals fault attribution by prefixing its feedback with the literal strings `[TEST ERROR]` / `[IMPLEMENTATION ERROR]`, and the runner nodes route on `"[TEST ERROR]" in analysis`. These strings are load-bearing across `agents/langgraph/reviewer.py` and both runner nodes.
- `utils/workspace.py` is dead code — it references `Config.TEST_FILE` and `Config.IMPLEMENTATION_MODULE`, which no longer exist. Nothing imports it.
- `experimental_executions/` and `mutation_tests/` are **frozen research artifacts** backing the paper's results. Do not regenerate or reformat them.
