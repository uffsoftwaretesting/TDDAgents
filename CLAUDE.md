# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Start-of-session ritual (mandatory)

A massive refactor of TDDAgents is under way. Before doing **any** work in this repository — reading, planning, answering a question, or editing — always:

1. **Read this entire `CLAUDE.md`.** It is the map of the current architecture and of the gotchas the refactor has to preserve or deliberately break.
2. **Read the relevant source code before changing it.** Never edit or reason about `app/` from memory or from this document alone; open the actual files. This file describes intent, the source is the truth, and during the refactor the two will drift.
3. **Consult the `claude-code-explorer` MCP server** (`node /home/pedroamaro/claude-code/mcp-server/dist/src/index.js`) to read Claude Code's own source and files. Use its tools — `list_directory`, `read_source_file`, `search_source`, `get_architecture`, `list_tools` / `get_tool_source`, `list_commands` / `get_command_source` — whenever the task touches Claude Code behavior, agent/tool design, or patterns worth mirroring in the refactor. Do not answer questions about Claude Code internals from memory when this server can show the real code.
4. **Run the [Quality gate](#quality-gate-mandatory) before any code you create or refactor is considered done.** It is not optional and not a final polish step: the tests are written *with* the change, not after it.

Only after these steps should you propose a plan or start editing.

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

Measured against `app/workspace/`, `app/sync/` and `app/errors/`, 323 tests:

| | |
|---|---|
| Mutants | 983 |
| Killed | 895 |
| Survived | 88 |
| **Mutation score** | **91.0%** |
| flake8 / mypy | 0 findings, 0 errors (mypy `--strict`) |

`app/errors/` was brought under the gate after the fact and is worth reading as a worked
example of why the gate exists. Both classifiers decide whether each infrastructure
failure is retried or aborts the run, and both had **zero tests**. Bringing them in
surfaced a live bug: the `EmptyInputError` / `EmptyChannelError` branch of
`handle_llm_exception` rendered the literal text `{exc}` in its message, because the
continuation line of an implicit string concatenation had lost its `f` prefix — the same
silent-interpolation failure class as the Jinja2 gotcha below. It also removed a
duplicated `__init__` from both `TDDWorkflowError` subclasses.

The 88 survivors are dominated by mutations with no behavioral effect: codec aliases
(`"utf-8"` → `"UTF-8"`), log-message wording, and case-flips of string literals no
contract depends on. Anything behavioral that survived a mutation run has been either
killed or written down — treat that as the standard to hold, not 90% as a ceiling.

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
- `tests/conftest.py` seeds `OPENAI_API_KEY` / `E2B_API_KEY` / `POSTGRES_URL` before any
  `app.*` import, because `config.py` raises at import time without them.

## What this is

TDDAgents is a research prototype (UFF / SBES 2026): a LangGraph multi-agent pipeline that turns a natural-language problem description into tested Python code by driving the Red–Green phases of TDD. Agent-generated code is written and executed inside a remote **E2B cloud sandbox**, never on the host. Graph state is checkpointed to PostgreSQL.

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
$P/flake8 app/workspace app/sync app/sandbox tests      # touched files; config in setup.cfg
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

## Architecture

### Two graphs, run sequentially by `app/main.py`

1. **`RequirementsOrchestrator`** (`graph/subgraphs/requirements_orchestrator_subgraph.py`) — state `RequirementsState`, no checkpointer. Cycle: `analyst → user_input → (analyst | engineer)`. Human-in-the-loop is a plain blocking `input()` inside the `user_input` node, not a LangGraph interrupt. Produces `final_specification` (Markdown, contracts only).
2. **`TDDOrchestrator`** (`graph/orchestrator.py`) — state `AgentState`, checkpointed. Top level is `planner → tdd_execution (subgraph) → evaluator`, with `evaluator` looping back into `tdd_execution` for each item of `plan`.

### The TDD subgraph

`graph/subgraphs/build_tdd_subgraph.py` builds `tester → runner_red → developer → runner_green` with conditional edges. Every node is wrapped (`wrapper_tester`, `wrapper_runner_red`, …) — the wrappers exist solely to fold **resilience metrics** into the returned state (failure counters, `is_flow_type`), keeping the node functions themselves metric-free.

Two success flows are recorded per sub-requirement:
- **F1** — clean TDD: the test failed first (Red confirmed), then passed.
- **F2** — "green in red": the test passed immediately; `runner_red` still routes to the Developer with an injected warning prompt so the cycle is never skipped.

### Routing is entirely by `state["status"]` string

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

### Error taxonomy

`errors/exceptions.py` defines `TransientInfraError` (retry, capped by `Config.MAX_INFRA_RETRIES`, with a 3s sleep) and `FatalInfraError` (abort to a terminal status). Two classifiers map SDK exceptions onto them: `errors/agents/handler.py` for LLM/LangChain/LangGraph errors and `errors/sandbox/handler.py` for E2B. Both **always raise** — call them from inside `except` blocks and never expect a return. `handler.py` deliberately re-raises `GraphBubbleUp` untouched so LangGraph control flow is not swallowed. Note the E2B `CommandExitException` (non-zero exit from pytest) is *expected* in TDD and is intercepted in `agents/langgraph/runner.py` before reaching the classifier.

### State and config

`config/config.py` holds both `Config` and the two `TypedDict` state schemas. It **raises at import time** if `OPENAI_API_KEY`, `E2B_API_KEY`, or `POSTGRES_URL` are unset — importing anything under `app/` without a `.env` fails immediately. Model selection is hardcoded there (`CHAT_MODEL = "openai"`, `MODEL = "o4-mini"`), not read from the environment; switching providers means editing `Config` and passing the right kwargs through `utils/chat_model_factory.py`.

Postgres is not required: `TDDOrchestrator.run` falls back to `InMemorySaver` on `OperationalError`, losing cross-restart resumability.

`node_execute_progress_evaluator` clears all three agent histories with `RemoveMessage` when advancing to the next sub-requirement — each sub-requirement gets a fresh context window.

## Conventions and gotchas

- **Everything is in English** — prompts, logs, docstrings, comments, identifiers, and type names. (Phase 0 of the refactor migrated this codebase from a prior Portuguese-prompts/English-identifiers split; match English everywhere when adding code.)
- **Prompt templates are the behavior.** Agent role definitions live in `app/prompts/agents/langgraph/<agent>/{sys,hum}_prompt_*.jinja2`, not in Python. The Tester has separate `normal` and `review` variants; the orchestrator has standalone `feedback_*.jinja2` templates that are injected into `reviewer_messages` as synthetic feedback.
- The Jinja2 `Environment` in `prompt_loader.py` uses default (non-strict) undefined, so a misspelled kwarg **silently renders as an empty string**. There is already one such live bug: `agents/langgraph/developer.py:35` passes `sub_requsite=` while `developer/hum_prompt_1.jinja2` expects `sub_requisite`, so the Developer's first prompt has a blank sub-requirement block. Verify kwarg names against the template when touching either side.
- The Reviewer signals fault attribution by prefixing its feedback with the literal strings `[TEST ERROR]` / `[IMPLEMENTATION ERROR]`, and the runner nodes route on `"[TEST ERROR]" in analysis`. These strings are load-bearing across `agents/langgraph/reviewer.py` and both runner nodes.
- `utils/workspace.py` is dead code — it references `Config.TEST_FILE` and `Config.IMPLEMENTATION_MODULE`, which no longer exist. Nothing imports it.
- `experimental_executions/` and `mutation_tests/` are **frozen research artifacts** backing the paper's results. Do not regenerate or reformat them.
