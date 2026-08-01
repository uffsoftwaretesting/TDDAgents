# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A research prototype (UFF / SBES 2026): a LangGraph multi-agent pipeline that turns a natural-language request into tested Python code by executing the Red-Green phases of TDD. Agent-generated code never runs locally — it runs in an E2B cloud sandbox. Graph state is checkpointed in PostgreSQL.

**Language convention:** all code comments, log messages, CLI text, and Jinja2 prompt templates are in Brazilian Portuguese. Match that when editing existing files. Docstrings are mixed (pt-BR in agents/nodes, English in `app/errors/agents/handler.py` and `app/utils/token_metrics.py`).

## Commands

```bash
# Infra (Postgres checkpointer) — required before running
docker compose up -d

# Run the pipeline. MUST be run from the repo root: main.py resolves
# the spec menu via the relative path Path("app/prompts/specs").
python -m app.main
python -m app.main --thread-id "tdd-1a2b3c4d..."   # resume from a Postgres checkpoint
python -m app.main --fresh                          # force a random thread_id

# Mutation testing over the frozen experimental workspaces
python mutation_tests/<task>/run_mutation_tests.py   # cep_validator | discount_calculator | palindrome | web_cpf | web_md_html

# SonarQube quality oracle (see readme.md for the full UI setup + token flow)
docker compose -f docker-compose.sonarqube.yaml up -d
```

There is **no test suite for this repository itself** — `pytest` only ever runs inside the E2B sandbox (via `app/agents/langgraph/runner.py`) or against a generated workspace (via `backup/analyze.sh`). Adding a change here is validated by running the pipeline, not by a local test command.

Interactive flow: `python -m app.main` prints a numbered menu of `app/prompts/specs/*.txt`; after the Analyst prints its checklist, only the literal string `/sim` advances to the Engineer (`app/graph/nodes/requirements_user_input.py:44`). Anything else reopens analysis.

## Architecture

### Two independent graphs, run sequentially by `app/main.py`

1. **`RequirementsOrchestrator`** ([requirements_orchestrator_subgraph.py](app/graph/subgraphs/requirements_orchestrator_subgraph.py)) — `analyst ⇄ user_input → engineer`. Uncheckpointed, human-in-the-loop via blocking `input()`. Produces `final_specification` (Markdown tech spec) + `conversation_history`.
2. **`TDDOrchestrator`** ([orchestrator.py](app/graph/orchestrator.py)) — `planner → tdd_execution (subgraph) → evaluator`, with `evaluator` looping back into `tdd_execution` once per sub-requirement. Checkpointed.

The inner TDD subgraph ([build_tdd_subgraph.py](app/graph/subgraphs/build_tdd_subgraph.py)) is `tester → runner_red → developer → runner_green`, where each node can also self-loop (infra retry) or route backwards (Reviewer verdict).

### The `status` string is the control bus

Every node returns `{**state, "status": "..."}` and every edge is a conditional edge that switches on that string. Adding a node means adding its status values to the relevant `route_after_*` function, or the graph silently falls through to the default branch. Status families:

- `infra_error_<node>` → self-loop, retry (bounded by `Config.MAX_INFRA_RETRIES`, sleeps 3s)
- `red_confirmed` / `green_passed` → advance
- `test_review_needed` → route back to `tester`
- `green_failed` → route back to `developer`
- `max_retries_exceeded` / `sandbox_failed` / `tester_failed` / `developer_failed` → exit the subgraph; the evaluator records a failed requirement and aborts the plan

### Three-layer separation

| Layer | Responsibility |
|---|---|
| `app/agents/langgraph/*.py` | Pure LLM calls. Build a message history, `llm.with_structured_output(...)`, return `(result, updated_history)`. No state, no routing, no error swallowing — they call `handle_llm_exception` which always raises. |
| `app/graph/nodes/*.py` | Read `AgentState`, call the agent, catch `TransientInfraError` / `FatalInfraError`, translate to a `status`, log, return the merged state dict. |
| `app/graph/subgraphs/build_tdd_subgraph.py` | Thin `wrapper_*` functions around each node that increment the resilience counters (`test_faults`, `implementation_faults`, `autonomously_corrected_failures`) and stamp `is_flow_type`. Metrics bookkeeping lives here, not in the nodes. |

### State (`app/config/config.py`)

`AgentState` is a flat `TypedDict`; only `tester_messages`, `developer_messages`, `reviewer_messages`, and `audit_log` use the `add_messages` reducer (append-only). Everything else is last-write-wins, which is why nodes spread `{**state, ...}`.

`file_system: dict[str, str]` is the in-state mirror of the sandbox filesystem. `read_all_files_from_state()` serialises it into every agent prompt — the LLMs see the whole workspace as text, not via tool calls. `app/main.py` extracts this dict to disk at the end of the run.

Between sub-requirements the evaluator wipes the three agent histories with `RemoveMessage` ([execute_progress_evaluator.py:23](app/graph/nodes/execute_progress_evaluator.py#L23)) so each slice starts with a clean context — only `file_system` and the metrics carry over.

### Sandbox lifecycle

`TDDOrchestrator.run` creates exactly one `Sandbox` and kills it in a `finally`. Only `sandbox_id` travels in the state; `apply_agent_action_to_sandbox` and `run_pytest_in_sandbox` each `Sandbox.connect(...)` to that id. All sandbox commands run as `user="root"`; tests run with `PYTHONPATH=.`.

`run_pytest_in_sandbox` distinguishes "tests failed" (E2B `CommandExitException` → returns `(output, False)`, normal TDD signal) from real infra failure (→ `handle_e2b_exception`, which raises Transient/Fatal). Don't collapse those paths.

### Reviewer verdict is a string prefix

`analyze_failures` returns a string prefixed with `[ERRO NO TESTE]` or `[ERRO NA IMPLEMENTAÇÃO]`, and the runner nodes route by substring match on `[ERRO NO TESTE]`. The Pydantic `is_test_fault` bool is only used to build that prefix. Changing the wording breaks routing.

### Prompts

`load_prompt(name, **vars)` renders Jinja2 templates rooted at `app/prompts/`. Path convention: `agents/langgraph/<agent>/sys_prompt_1.jinja2` / `hum_prompt_1.jinja2` — except `tester/`, which has `{sys,hum}_prompt_{normal,review}.jinja2` selected by the `is_review_mode` flag, and `orchestrator/`, which holds canned feedback snippets injected into agent histories (e.g. the "green in red" alert).

### Model / provider configuration

Hardcoded as class attributes in `Config`, **not** environment-driven: `CHAT_MODEL = "openai"`, `MODEL = "o4-mini"`, `TEMPERATURE = 1.0`, `MAX_ITERATIONS = 15`, `MAX_INFRA_RETRIES = 3`. Swapping providers means editing `Config` (and `get_chat_model` supports `openai` / `anthropic` / `gemini` / `together`).

Note `config.py` raises at **import time** unless `OPENAI_API_KEY`, `E2B_API_KEY`, and `POSTGRES_URL` are all set — even when targeting a different provider. Also note `app/errors/agents/handler.py` classifies OpenAI SDK exception types specifically; other providers fall through to the "unclassified → Fatal" branch.

Postgres is best-effort: on `OperationalError` the orchestrator falls back to `InMemorySaver`, so a run without Docker works but is not resumable.

## Output layout

Each run writes `./workspace_output_<thread_id>/` (thread_id already carries the `tdd-` prefix) containing the extracted `src/` + `tests/` plus `metrics_and_logging/` (`execution_logs.txt`, `planner.txt`, `token_usage.txt`, `resilience_metrics.txt`, `subreq_results.txt`, the spec, and the user/analyst dialogue). The thread_id is derived from a SHA-1 of the specification text unless `--fresh`/`--thread-id` is given, so re-running the same spec resumes the same checkpoint.

## Directories that are research artifacts, not source

`experimental_executions/` (15 frozen generated workspaces referenced by absolute-ish path from the mutation runners), `mutation_tests/*/results.json` and `*.md` reports, and `backup/` (the `analyze.sh` + `sonar-project.properties` templates copied into a workspace). The mutation runners hardcode workspace directory names and per-workspace `src.` prefix-stripping quirks — renaming anything under `experimental_executions/` breaks them.
