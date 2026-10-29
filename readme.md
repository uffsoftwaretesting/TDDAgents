# TDDAgents

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Orchestration-FF6B35?style=for-the-badge)
![E2B](https://img.shields.io/badge/E2B-Cloud_Sandbox-00C7B7?style=for-the-badge)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-State_Persistence-336791?style=for-the-badge&logo=postgresql&logoColor=white)

**An autonomous, multi-agent system that executes the Red-Green phases from Test-Driven Development — transforming natural language requirements into validated, production-quality code through an orchestrated Red-Green cycle coupled with a self-correction loop.**

*Research prototype developed at [Universidade Federal Fluminense (UFF)](https://www.uff.br) and presented at [SBES 2026](https://cbsoft.sbc.org.br/2026/).*

</div>

---

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
  - [Phase 1 — Requirements Gathering](#phase-1--requirements-gathering)
  - [Phase 2 — Red-Green Development, in One Conversation](#phase-2--red-green-development-in-one-conversation)
- [Key Features](#key-features)
- [The Agent Team](#the-agent-team)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
- [Usage](#usage)
  - [Run the main entry point](#run-the-main-entry-point)
  - [The development session](#the-development-session)
- [Execution Flags](#execution-flags)
- [Output & Deliverables](#output--deliverables)
- [Experiments](#experiments)
  - [Mutation Tests](#mutation-tests)
  - [SonarQube — Code Quality Analysis](#sonarqube--code-quality-analysis)
    - [What is SonarQube?](#what-is-sonarqube)
    - [1. Start SonarQube](#1-start-sonarqube)
    - [2. First Login](#2-first-login)
    - [3. Configure the Universal Quality Gate](#3-configure-the-universal-quality-gate)
    - [4. Create a Project](#4-create-a-project)
    - [5. Configure the Project's Quality Gate](#5-configure-the-projects-quality-gate)
    - [6. Generate an Analysis Token](#6-generate-an-analysis-token)
    - [7. Run the Analysis](#7-run-the-analysis)

---

## Overview

**TDDAgents** is an open-source, multi-agent software engineering pipeline that autonomously executes the Red-Green phases from Test-Driven Development. It takes a natural language problem description as input and produces validated, tested Python code as output.

The workflow is structured into two well-defined phases:

1. **Interactive Requirements Gathering** — An AI Analyst collaborates with the user to eliminate ambiguities and produce a structured Technical Specification.
2. **Autonomous Red-Green Cycle** — A team of specialized agents executes an adapted Red-Green cycle for each decomposed sub-requirement.

The TDD cycle runs on the *loop engineering architecture* of Claude Code: one agent loop, with permissions, hooks, context assembly, compaction, recovery and tool orchestration arranged around it, laid out module for module like Claude Code's own source (`app/` mirrors its `src/`). All agent-generated code runs in a **per-session local workspace** with its own Python virtualenv (an E2B sandbox can be bound behind the same interface). Session state is persisted in **PostgreSQL** via LangGraph checkpointing (with an in-memory fallback), so a session resumes from its checkpoint and an interrupted sub-requirement resumes its transcript.

<div align="center">
  <img src="assets/arquitetura.png" alt="TDDAgents Architecture" width="80%"/>
</div>

---

## How It Works

### Phase 1 — Requirements Gathering

Before any code is written, the system interacts with the user to transform a vague or informal description into a formal, machine-readable Technical Specification.

| Step | Agent | Action |
|------|-------|--------|
| **1** | **Analyst** | Interviews the user iteratively. It identifies ambiguities and produces a validated requirements checklist. After the checklist is validated, the system waits for explicit user confirmation before proceeding to next phases. |
| **2** | **Engineer** | Consumes the conversation history and produces a structured Markdown Technical Specification — covering data structures, interfaces, edge cases, and environment dependencies. No implementation code is included; the spec defines contracts only. |

The Technical Specification is designed to be consumed by AI agents, not humans. It serves as the single source of truth for the entire autonomous TDD phase.

---

### Phase 2 — Red-Green Development, in One Conversation

The Technical Specification feeds into the **Planner Agent**, which decomposes it into an ordered list of atomic, testable sub-requirements (the TDD plan). The first item is always an environment setup step. Subsequent items represent incremental functional slices ordered by complexity.

The whole plan is then developed in **one conversation** with the agent loop (`app/cli/print.py` → `app/query_engine.py` → `app/query/query.py`), driven from an interactive session at the terminal (`app/screens/repl.py`). You can talk to the agents at any time; see [The development session](#the-development-session). The plan advances by evidence only: when the current item's ledger shows its whole Red → Green → Refactor cycle, it is marked done, the ledger restarts in RED and the agents are told the next item. The model works with file, Bash, `RunTests`, `TodoWrite`, `WebFetch`, skill and `Agent` tools (`ReadFile` also reads images, PDFs — page images through poppler's `pdftoppm` — and Jupyter notebooks), and can delegate to the TDD agents: **tester** (RED), **developer** (GREEN), **refactorer** (REFACTOR), plus the read-only **Explore** and **Plan** agents. Setting `TDDAGENTS_VERIFICATION_AGENT=1` adds Claude Code's **verification** agent (a background check that ends in `VERDICT: PASS`, `FAIL` or `PARTIAL`), with its contract; it is off by default, as in public Claude Code.

Each sub-requirement goes through one cycle:

- **RED:** robust unit tests covering the relevant scenarios and edge cases. They do not have to fail; a run of the item's new or changed tests ends RED.
- **GREEN:** the code the tests describe, until every test passes. The tests are locked.
- **REFACTOR (mandatory):** better code and stronger tests, and higher line + branch coverage of the production code (90% is a good rule of thumb, not a gate), until every test passes. `RunTests` measures coverage on every run (pytest-cov) and lists the uncovered lines.

The order is enforced by three independent barriers that no model output can reach (`app/tdd/`):

1. **The phase ledger.** `RunTests` executes the suite and is the only writer of observations. It records that RED's tests changed (it fingerprints the test files against the item's start), that GREEN passed, and that a REFACTOR run passed.
2. **Phase permissions.** In RED, writes to production code are denied (including through Bash redirections), and in GREEN, writes to tests; REFACTOR may change both. Spawning the developer requires RED's tests, and the refactorer a passing GREEN.
3. **The stop hook.** The loop cannot end `completed` until the ledger shows the whole cycle; until then the model is told what is missing and the turn continues.

The plan is saved beside the session transcript (`tdd-plan.json`), so a resumed session knows which items are done; the ledger is never saved, so the current item's cycle is observed again from RED.

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Real Execution Environment** | Every test runs with pytest in the session's own workspace and virtualenv, never inside TDDAgents' environment. |
| **Structurally Enforced TDD** | The ledger, the phase permission tier and the stop hook hold the cycle; the model cannot skip RED or declare success. |
| **State Persistence & Resumability** | PostgreSQL checkpoints the session graph; resume with `--thread-id`. An unfinished sub-requirement resumes from its transcript (the ledger is never restored — RED must be observed again). |
| **Self-Correction** | Failing test output goes back to the model as tool results; it fixes and re-runs until the cycle is observed. Model calls are retried with Claude Code's `withRetry` policy. |
| **Headless Mode** | `python -m app.main -p` runs a spec end to end with no questions, exiting 0 only when every sub-requirement completed its Red → Green → Refactor cycle. |
| **Structured Output Delivery** | Each project is its own folder (`~/.tddagents/workspaces/<thread_id>/project`) with its own virtual environment. |

---

## The Agent Team

```
  ╔══════════════════╦════════════════╦══════════════════════════════════════════════════════╗
  ║   AGENT          ║   ROLE         ║   RESPONSIBILITY                                     ║
  ╠══════════════════╬════════════════╬══════════════════════════════════════════════════════╣
  ║  Analyst         ║  Product Mgr   ║  Converses with user. Surfaces ambiguities.          ║
  ║                  ║                ║  Produces a validated requirements checklist.        ║
  ╠══════════════════╬════════════════╬══════════════════════════════════════════════════════╣
  ║  Engineer        ║  Tech Lead     ║  Converts checklist into a formal Markdown           ║
  ║                  ║                ║  Technical Specification (contracts only, no code).  ║
  ╠══════════════════╬════════════════╬══════════════════════════════════════════════════════╣
  ║  Planner         ║  Architect     ║  Decomposes the spec into ordered, atomic vertical   ║
  ║                  ║                ║  slices. Produces the tdd_plan execution sequence.   ║
  ╠══════════════════╬════════════════╬══════════════════════════════════════════════════════╣
  ║  tester          ║  RED           ║  Writes robust tests and runs them with RunTests.    ║
  ╠══════════════════╬════════════════╬══════════════════════════════════════════════════════╣
  ║  developer       ║  GREEN         ║  Implements the code the tests describe.             ║
  ╠══════════════════╬════════════════╬══════════════════════════════════════════════════════╣
  ║  refactorer      ║  REFACTOR      ║  Raises coverage and quality; tests kept green.      ║
  ╠══════════════════╬════════════════╬══════════════════════════════════════════════════════╣
  ║  verification,   ║  Read-only     ║  Review the result and explore or plan the codebase  ║
  ║  Explore, Plan   ║                ║  without writing.                                    ║
  ╚══════════════════╩════════════════╩══════════════════════════════════════════════════════╝
```

The Analyst, Engineer and Planner run in the LangGraph session shell (`app/session/`); the TDD agents are workers of the agent loop, spawned through the `Agent` tool (`app/prompts/agents/`).

---

## Project Structure

```
TDDAgents/
│
├── app/                        # laid out like Claude Code's src/, in snake_case
│   ├── main.py                 # CLI entry point (interactive and headless -p)
│   ├── cli/print.py            # One sub-requirement end to end (runHeadless)
│   ├── query_engine.py         # QueryEngine: transcript, result, cost
│   ├── query/                  # The agent loop (query.ts), its state, deps and stop hooks
│   ├── tool.py, tools/         # Tool contract; one directory per tool, prompt.md beside it
│   ├── services/               # api (model call, retry), tools (execution), compact, mcp
│   ├── utils/                  # permissions, hooks, settings, attachments, transcripts, ...
│   ├── state/ tasks/ skills/ coordinator/ constants/ schemas/ types/
│   ├── tdd/                    # TDDAgents' TDD layer: ledger, RunTests, phase rules, stop hook
│   ├── session/                # LangGraph session shell: analyst → engineer → planner → items
│   ├── workspace/, sandbox/    # Local workspace + venv; optional E2B sandbox
│   ├── prompts/                # System sections, TDD agents, skills, session prompts, specs
│   └── config/config.py        # Provider, model and environment settings
│
├── tests/                      # Offline test suite for app/ (no keys, sandbox or database)
├── reference/                  # Claude Code reference: source map and analyses
├── docs/                       # Architecture plan and phase record
├── assets/                     # Diagrams
├── experimental_executions/    # Recorded runs from the paper's evaluation
├── mutation_tests/             # Mutation testing results from the paper's evaluation
├── backup/                     # SonarQube analysis scripts & config
├── docker-compose.yaml         # PostgreSQL service
├── docker-compose.sonarqube.yaml
├── requirements.txt
└── .env.example
```

---

## Getting Started

### Prerequisites

| Requirement | Version | Purpose |
|-------------|---------|---------|
| **Python** | 3.11+ | Runtime (`enum.StrEnum`) |
| **Docker** | Latest | Hosts PostgreSQL locally |
| **OpenAI API Key** | — | The default model (`o4-mini`, set in `app/config/config.py`) |
| **E2B API Key** | optional | Only if you bind the E2B sandbox instead of the local workspace — get one at [e2b.dev](https://e2b.dev) |

---

### Installation

#### 1. Clone the Repository

```bash
git clone https://github.com/uffsoftwaretesting/TDDAgents.git
cd TDDAgents
```

#### 2. Configure Environment Variables

```bash
cp .env.example .env
```

Open `.env` and fill in your credentials:

```env
# ─── LLM Provider ──────────────────────────────────────────────────
OPENAI_API_KEY=sk-...

# ─── E2B Cloud Sandbox (optional) ──────────────────────────────────
E2B_API_KEY=

# ─── Database (Docker; optional, falls back to in-memory checkpoints)
POSTGRES_URL=postgresql://tdd_user:tdd_password@localhost:5432/tdd_db
```

#### 3. Start the Infrastructure

```bash
docker-compose up -d
```

Verify PostgreSQL is running:

```bash
docker-compose ps
```

#### 4. Create a Virtual Environment and Install Dependencies

```bash
python -m venv venv
source venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
```

---

## Usage

### Run the main entry point:

#### Step 1 — Start the Application

```bash
python -m app.main
```

Launches the main CLI entry point of the application.

---

#### Step 2 — Read the CLI Warning

Carefully read the warning message displayed by the CLI before continuing.

---

#### Step 3 — Select an Input Prompt

Choose one of the available options:

- Select a number to load a predefined `.txt` prompt used in the programming tasks experiments (e.g., CEP formatter, discount calculator, palindrome checker, CPF validator, or markdown to HTML).
- Or choose the last option to manually provide a custom prompt.
- The Analyst can ask follow-up questions. For evaluation/experiment runs, keep only the attempts where the initial prompt was enough to elicit requirements.
- Once you see the Requirements displayed in the terminal screen, type `/yes` as an answer. This approves the checklist and moves the session to the Engineer phase. Anything else is feedback to the Analyst; `/exit` (or `/quit`) leaves with the session paused at its checkpoint.

Note: The Analyst can ask more questions. You can also ask more features or corrections to be made. However, for research purposes, we only consider executions where the Analyst immediatelly collects and displays the requirements with no questions asked. Also, no other features are asked after the requirements are defined.

---

### The development session

After the Planner, the terminal becomes a conversation with the agents, in the manner of Claude Code. The input line stays live while they work.

- **Typing while they work** queues your message; the agents read it between tool calls.
- **Esc** (or Ctrl-C) interrupts the current turn. A pending permission question is answered *No*.
- **Permission questions** are numbered: type the number of your choice. After a *No* you may add what to do instead (`3 use the existing helper`). A *don't ask again* choice adds a rule for the session.
- **Questions from the agents** (`AskUserQuestion`) come one at a time with numbered options: type a number, several numbers (`1,3`) when more than one may be chosen, or your own answer. Esc declines and interrupts the turn.
- **The agents may propose plan changes** (the `UpdatePlan` tool). Each one is shown as a diff and asked; only its observed Red → Green → Refactor cycle marks an item done.

| Command | Effect |
|---------|--------|
| `/plan` | Show the TDD plan (`✔` done, `▶` current, `·` pending) |
| `/plan add <text>` | Add a sub-requirement at the end |
| `/plan edit <id> <text>` | Reword one; a done one is reopened |
| `/plan remove <id>` | Remove one |
| `/plan move <id> <position>` | Reorder |
| `/pause` | Let the agents stop after the current turn |
| `/resume` | Continue the plan |
| `/requirements <request>` | Re-run the Analyst, Engineer and Planner; done items stay done, pending ones are replaced |
| `/help` | List the commands |
| `/exit`, `/quit` | Leave (continue later with `--thread-id`) |

Slash commands typed while the agents work wait for the turn to end, except `/help`, `/plan`, `/pause` and `/exit`. Any change that moves the current item restarts its ledger in RED. The session stays open when the plan is done, so you can add more.

---

## Execution Flags

### Resume Execution *(continue from checkpoint)*

```bash
python -m app.main --thread-id "tdd-1a2b3c4d..."
```

Resumes a previous session from the LangGraph checkpoint stored in PostgreSQL. Useful for recovering from crashes or manual interrupts. A session that had reached development resumes its conversation from the transcript, with the plan's done items kept.

> **Thread IDs** are printed to the console at the start of every run. Save them to resume long-running sessions.

### Headless *(no questions)*

```bash
python -m app.main -p --spec app/prompts/specs/<file>.txt
echo "Build a calculator" | python -m app.main -p
```

The checklist is approved automatically; a clarifying question from the Analyst fails the session. Development runs the same conversation with nobody to answer: every permission question is denied (plan changes included), and a turn that ends before the current item is done stops the session, with the remaining items reported NOT_RUN. A question from the agents (`AskUserQuestion`) stops the session there and prints it; answer with `python -m app.main -p --thread-id <id> "<answer>"` (Postgres checkpointing only). The exit code is 0 only if every sub-requirement completed its Red → Green → Refactor cycle.

### Other flags

| Flag | Effect |
|------|--------|
| `--fresh` | Start a new session instead of the request-derived thread id |
| `--checkpoint memory\|<uri>` | Checkpoint store (default `POSTGRES_URL`, in-memory fallback) |
| `--permission-mode <mode>` | `default`, `acceptEdits` (shipped default), `plan`, `bypassPermissions`, `dontAsk` or `auto` |
| `--settings <file>` | An extra settings file (permissions, hooks) |
| `--dangerously-skip-permissions` | Bypass permission prompts (the TDD phase rules still apply) |
| `--coordinator` | Coordinator mode: the main thread only orchestrates the TDD agents |

---

## Output & Deliverables

Each session's project lives in its own folder, outside TDDAgents: `~/.tddagents/workspaces/<thread_id>/` (set `TDDAGENTS_WORKSPACES_DIR` to use another root). The project has its own virtual environment, active for every command the agents run; they install what the project needs as they judge best, as claude-code does. A run with skipped tests never counts as passing, and the session ends by running the project's suite once more in that environment (`final_test_run.txt`). TDDAgents never commits.

```
~/.tddagents/workspaces/<thread_id>/
├── project/                            # The generated project
│   ├── .venv/                          # Its own virtual environment
│   ├── src/ or modules, tests/         # Generated production code and pytest suites
│   └── .tddagents/settings.json        # The project's TDDAgents settings, seeded from the defaults
└── metrics_and_logging/
    ├── final_test_run.txt              # The project's whole suite, run once more in its own venv
    ├── initial_user_prompt.txt         # Initial prompt sent by the User
    ├── confirmed_user_requirements.txt # Analyst-generated requirements approved by the user
    ├── engineer_specifications.txt     # Specs generated by the Engineer Agent based on the confirmed_user_requirements.txt
    ├── planner.txt                     # tdd_plan decomposition
    ├── execution_logs.txt              # Full agent trace
    ├── token_usage.txt                 # Token usage
    ├── subreq_results.txt              # Each sub-requirement: DONE, FAILED (with the terminal) or NOT_RUN
    └── user_analyst_dialogue.txt       # Full dialogue between the user and the Analyst Agent.

```
---

## Experiments

TDDAgents was evaluated on **15 executions** across 5 different programming tasks (3 independent runs per task), as part of a study submitted to [SBES 2026](https://cbsoft.sbc.org.br/2026/sbes/). The evaluation results are available in the `experimental_executions/` and `mutation_tests/` directories. They were produced by the earlier LangGraph pipeline (Tester / Developer / Reviewer subgraph), before the rebuild on the agent loop, and are kept unchanged as the paper's record.

The 5 evaluated programming tasks are:
1. **CEP Formatter** (`cep_validator`): A function that formats and validates Brazilian postal codes (CEP) to the "XXXXX-XXX" pattern.
2. **Discount Calculator** (`discount_calculator`): A function to calculate product discounts, returning the saved and final values as a structured pair.
3. **Palindrome Checker** (`palindrome`): A function to check if a string is a palindrome after normalization (ignoring case, spaces, and punctuation).
4. **CPF Validator** (`web_cpf`): A FastAPI REST API to validate Brazilian CPFs following Clean Architecture.
5. **Markdown to HTML Converter** (`web_md_html`): A FastAPI service that converts Markdown text into HTML.

We collected several reports and metrics during run time. **If you wish to reproduce such metrics**, just initiate the workflow and they will be available in the generated folder at the end. **However, a few metrics are collected via scripts and testing tools**. In the following sections, we describe how to execute mutation tests and configure SonarQube tools to collect quality metrics.

All evaluation artifacts — including execution logs, sonar reports, coverage data, and convergence plots — are preserved in the `experimental_executions/` and `mutation_tests/` directories.

-----------------------------------------------------------------------------------------------------------------------------------------------------------------
### Mutation Tests

To rigorously assess the quality and fault-detection capabilities of the test suites generated by the autonomous TDD pipeline, we conducted mutation testing using `mutmut`. Mutation testing operates by injecting small faults (mutants) into the source code and verifying if the generated test suite fails (kills the mutant).

The mutation tests are structured as follows across the 5 programming tasks:

#### 1. Mutation Test Folders & Running Scripts
Inside the `mutation_tests/` directory, each programming task has a dedicated subdirectory containing the configuration templates and a runner script:
- `cep_validator/run_mutation_tests.py`
- `discount_calculator/run_mutation_tests.py`
- `palindrome/run_mutation_tests.py`
- `web_cpf/run_mutation_tests.py`
- `web_md_html/run_mutation_tests.py`

You can execute the mutation tests for a task by activating your virtual environment and running the respective Python script. For example:
```bash
python mutation_tests/web_cpf/run_mutation_tests.py
```
This script automates:
1. Cleaning previous caches and temporary configurations.
2. Setting up path mappings and environment variables for the 3 experimental workspaces (runs).
3. Temporarily renaming non-behavioral tests (such as structural or linting checks) so they do not add noise during mutation execution.
4. Copying the task-specific `config_w*.cfg` setup template.
5. Invoking `mutmut run` and `mutmut export-cicd-stats`.
6. Restoring the workspace files and saving the final results.

#### 2. Raw Results JSON (`results.json`)
After running the script, a `results.json` file is written to the task's mutation directory. This file stores structured statistics about:
- The count of mutants generated.
- The number of mutants killed.
- Mutants that survived or resulted in timeouts.
- The exact git-style diffs showing the code modifications made by each surviving mutant.

#### 3. Mutation Reports (`mutation_report_<task_name>.md`)
For each programming task, a consolidated report in Markdown compiles the results across the three workspaces (three independent agent runs):
- `mutation_tests/cep_validator/mutation_report_cep_validator.md`
- `mutation_tests/discount_calculator/mutation_report_discount_calculator.md`
- `mutation_tests/palindrome/mutation_report_palindrome.md`
- `mutation_tests/web_cpf/mutation_report_web_cpf.md`
- `mutation_tests/web_md_html/mutation_report_web_md_html.md`

These reports include:
- A tabular summary comparing total mutants, killed/survived counts, timeouts, and final mutation scores for each run.
- In-depth analysis of the test suite's robustness and detailed code snippets (diffs) of surviving mutants to pinpoint coverage gaps.

### SonarQube — Code Quality Analysis

#### What is SonarQube?

[SonarQube](https://www.sonarsource.com/products/sonarqube/) is an open-source static analysis platform that continuously inspects source code for bugs, code smells, security vulnerabilities, test coverage, code duplication, and technical debt. It provides a persistent dashboard where quality metrics are tracked across analysis runs.

In this project, SonarQube serves as the **external quality oracle** for the autonomous TDD pipeline. After the agent system generates code and tests for a given problem, SonarQube validates whether the output meets the research-defined quality thresholds — coverage, complexity, duplication, debt ratio, and test success rate. This provides an objective, reproducible quality signal that is independent of the agents themselves, and the `sonar_metrics_report.txt` artifact produced by each run is used directly in the paper's evaluation results.

---

#### 1. Start SonarQube

From the repository root, bring up the SonarQube container:

```bash
docker compose -f docker-compose.sonarqube.yaml up -d
```

Wait ~30 seconds for the service to initialize.

---

#### 2. First Login

1. Open [http://localhost:9000](http://localhost:9000) in your browser.
2. Log in with the default credentials:
   - **Login:** `admin`
   - **Password:** `admin`
3. You will be prompted to set a new password. Choose one and confirm it.

---

#### 3. Configure the Universal Quality Gate

This quality gate defines the acceptance thresholds that every generated workspace must satisfy. It will be applied to all projects.

Navigate to **Quality Gates** (top menu) → **Create** → give it a recognizable name (e.g., `TDDAgents Universal Gate`).

Then add the following conditions:

| Metric | Operator | Threshold |
|--------|----------|-----------|
| Cognitive Complexity | is less than or equal to | `15` |
| Cyclomatic Complexity | is less than or equal to | `10` |
| Code Smells Density *(per 100 LOC)* | is less than or equal to | `5` |
| Duplicated Lines (%) | is less than or equal to | `3` |
| Coverage | is greater than or equal to | `80` |
| Technical Debt Ratio | is less than or equal to | `5` |
| Unit Test Success (%) | is greater than or equal to | `100` |

> **Note:** SonarQube does not expose "Code Smells Density" as a native metric. The `analyze.sh` script computes this value as `(code_smells / ncloc) × 100` and evaluates it locally in `sonar_metrics_report.txt`. Add the remaining metrics as native conditions in the quality gate UI.

---

#### 4. Create a Project

1. From the SonarQube dashboard, click **Create Project → Manually**.
2. Fill in:
   - **Project display name** — a human-readable label (e.g., `CPF Validator Run 1`). This value goes into `sonar.projectName` in `sonar-project.properties`.
   - **Project key** — a unique machine identifier (e.g., `cpf-validator-run-1`). This value goes into `sonar.projectKey` in `sonar-project.properties`.
3. When asked how to set the **New Code definition**, select **"Follow the instance's default"** and click **Create project**.

---

#### 5. Configure the Project's Quality Gate

After the project is created, navigate to the project's **Project Settings → Quality Gate** and select the universal gate you created in step 3.

---

#### 6. Generate an Analysis Token

1. Inside the project, go to **Analysis Method → Locally**.
2. Under **Generate a token**, provide:
   - A token name (e.g., `tddagents-token`)
   - An expiration date
3. Click **Generate**.
4. **Copy the token value immediately** — it will not be shown again. You will pass it to `analyze.sh` via the `SONAR_TOKEN` environment variable.

---

#### 7. Run the Analysis

The `analyze.sh` script orchestrates the full pipeline: it runs pytest with coverage, sends the results to SonarQube via the scanner container, polls the API for metrics, and writes the final quality report.

##### Prepare the workspace

Copy the required files into the **root of the session's project** (`~/.tddagents/workspaces/<thread_id>/project/`):

```bash
# From the repository root
cp backup/analyze.sh  ~/.tddagents/workspaces/<thread_id>/project/
cp backup/sonar-project.properties  ~/.tddagents/workspaces/<thread_id>/project/
chmod +x ~/.tddagents/workspaces/<thread_id>/project/analyze.sh
```

##### Configure sonar-project.properties

Edit the `sonar-project.properties` file you just copied and set the project name and key from step 4:

```properties
sonar.projectKey=<your-project-key>
sonar.projectName=<your-project-name>
sonar.sources=src
sonar.tests=tests
sonar.python.coverage.reportPaths=coverage.xml
sonar.host.url=http://localhost:9000
```

##### Run the script

```bash
cd ~/.tddagents/workspaces/<thread_id>/project/
export SONAR_TOKEN=<paste-token-here>
./analyze.sh
```

##### Outputs

After the script completes, the following artifacts are written to the workspace root:

| File | Description |
|------|-------------|
| `coverage.xml` | Coverage report generated by pytest-cov |
| `test-results.xml` | JUnit-format test execution log |
| `sonar_metrics_report.txt` | Human-readable quality report with PASSED/FAILED verdict per metric |

All metrics are also visible in the SonarQube project dashboard at [http://localhost:9000](http://localhost:9000).

---

*This project is an open-source research prototype. Contributions and extensions are welcome.*
