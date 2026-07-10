# TDDAgents

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Orchestration-FF6B35?style=for-the-badge)
![E2B](https://img.shields.io/badge/E2B-Cloud_Sandbox-00C7B7?style=for-the-badge)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-State_Persistence-336791?style=for-the-badge&logo=postgresql&logoColor=white)

**An autonomous, multi-agent system that executes the full Test-Driven Development lifecycle — transforming natural language requirements into validated, production-quality code through an orchestrated Red-Green-Refactor cycle.**

*Research prototype developed at [Universidade Federal Fluminense (UFF)](https://www.uff.br) and presented at [SBES 2026](https://cbsoft.sbc.org.br/2026/).*

</div>

---

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
  - [Phase 1 — Requirements Gathering](#phase-1--requirements-gathering)
  - [Phase 2 — Autonomous TDD Cycle](#phase-2--autonomous-tdd-cycle)
- [Key Features](#key-features)
- [The Agent Team](#the-agent-team)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
- [Usage](#usage)
  - [Run the main entry point](#run-the-main-entry-point)
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

**TDDAgents** is an open-source, multi-agent software engineering pipeline that autonomously executes the full TDD lifecycle. It takes a natural language problem description as input and produces validated, tested Python code as output — without human intervention after the initial requirements are confirmed.

The workflow is structured into two well-defined phases:

1. **Interactive Requirements Gathering** — An AI Analyst collaborates with the user to eliminate ambiguities and produce a structured Technical Specification.
2. **Autonomous TDD Cycle** — A team of specialized agents executes an adapted Red-Green-Refactor cycle for each decomposed sub-requirement.

```
  🔴 RED                     🟢 GREEN                  🔵 REFACTOR

  ──────────────             ─────────────────          ──────────────────

  Test Engineer writes  →    Developer writes      →    Reviewer analyzes
  failing tests              minimum code to            logs, classifies
  (no implementation)        satisfy the tests          fault, routes fix
```

All agent-generated code runs inside isolated **E2B Linux containers**. Orchestration state is persisted in **PostgreSQL** via LangGraph checkpointing, enabling resumable, fault-tolerant long-running executions.

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

### Phase 2 — Autonomous TDD Cycle

The Technical Specification feeds into the **Planner Agent**, which decomposes it into an ordered list of atomic, testable sub-requirements (`tdd_plan`). The first item is always an environment setup step. Subsequent items represent incremental functional slices ordered by complexity.

Each sub-requirement is processed by a dedicated **TDD Subgraph** that encapsulates the full Red-Green-Refactor loop:

<div align="center">
  <img src="assets/subgrafo.png" alt="TDD Subgraph" width="60%"/>
</div>

**Execution flow inside the TDD Subgraph:**

1. **Test Engineer (Tester):** Writes failing tests for the current sub-requirement before any implementation exists. Produces an `AgentAction` with test files, dependencies to install, and optional bash setup commands.

2. **Executor Red:** Runs the test suite via pytest inside the E2B container. Three outcomes are handled:
   - `red_confirmed` — tests fail due to missing implementation → advance to Developer.
   - `test_review_needed` — Reviewer flags a malformed test (`[ERRO NO TESTE]`) → Test Engineer rewrites the suite.
   - `green_in_red` — tests pass immediately (prior iteration already satisfied the requirement) → Reviewer injects a warning and Developer inspects for false positives.

3. **Developer:** Writes the minimum production code to make the current test suite pass. Respects the TDD minimum-implementation principle: no speculative features.

4. **Executor Green:** Re-runs the full test suite. If all pass, `green_passed` is emitted and the cycle advances to the next sub-requirement. If tests fail, the Reviewer classifies the fault and routes back to either the Developer (implementation error) or Test Engineer (test error).

5. **Reviewer:** Acts as external judge throughout the cycle. Receives the full workspace state, the current sub-requirement, and the complete Technical Specification as context. Emits a binary diagnosis: implementation fault vs. test fault. This role separation is an intentional architectural decision to prevent biased self-correction.

The self-healing loop runs for a configurable maximum number of iterations. Resilience metrics (total failures, failures by type, auto-recovery rate) are collected throughout.

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Real Execution Environment** | Every test runs inside an isolated E2B Linux container. Dependencies installed via `pip`. Results are deterministic. |
| **Intelligent Fault Attribution** | The Reviewer distinguishes between bad implementation and bad tests by analyzing the full traceback — routing the fix to the correct agent automatically. |
| **State Persistence & Resumability** | PostgreSQL checkpoints the full LangGraph state after every node. Stop at any time and resume from the exact checkpoint using a Thread ID. |
| **Self-Healing Retry Loop** | When tests fail, agents read the error output, reason about the root cause, apply a targeted fix, and re-execute — iterating until resolution or hitting the retry limit. |
| **Multi-Provider LLM Support** | Supports OpenAI, Anthropic, Gemini, and DeepSeek via a unified `chat_model_factory`. Swap the provider in `.env` without changing any agent code. |
| **Structured Output Delivery** | All generated source files and test suites are extracted from the E2B sandbox and saved locally to `./workspace_output/`. |

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
  ║  Test Engineer   ║  QA Engineer   ║  Writes failing test files before any implementation ║
  ║  (Tester)        ║                ║  exists. Rewrites tests when Reviewer flags errors.  ║
  ╠══════════════════╬════════════════╬══════════════════════════════════════════════════════╣
  ║  Developer       ║  Software Eng. ║  Writes minimum production code to satisfy tests.    ║
  ║                  ║                ║  Applies targeted fixes guided by Reviewer feedback. ║
  ╠══════════════════╬════════════════╬══════════════════════════════════════════════════════╣
  ║  Runner          ║  Executor      ║  Manages E2B sandbox lifecycle. Executes Red and     ║
  ║  (Red / Green)   ║                ║  Green phases. Returns exact stdout/stderr logs.     ║
  ╠══════════════════╬════════════════╬══════════════════════════════════════════════════════╣
  ║  Reviewer        ║  Judge/Debugger║  Analyzes stack traces. Emits binary fault diagnosis ║
  ║                  ║                ║  (implementation vs. test). Routes fix accordingly.  ║
  ╚══════════════════╩════════════════╩══════════════════════════════════════════════════════╝
```

---

## Project Structure

```
TDDAgentsUFF/
│
├── app/
│   ├── agents/
│   │   └── langgraph/                   # Agent logic — LLM calls & structured outputs
│   │       ├── analyst.py               # Requirements Analyst
│   │       ├── developer.py             # Developer: production code generation
│   │       ├── engineer.py              # Engineer: Technical Specification authoring
│   │       ├── planner.py               # Planner/Architect: TDD plan decomposition
│   │       ├── reviewer.py              # Reviewer/Judge: fault attribution
│   │       ├── runner.py                # Runner: E2B sandbox execution (Red & Green)
│   │       └── tester.py                # Test Engineer: pytest file generation
│   │
│   ├── config/
│   │   └── config.py                    # App settings & LangGraph AgentState TypedDicts
│   │
│   ├── errors/                          # Structured error handling
│   │   ├── agents/handler.py
│   │   ├── sandbox/handler.py
│   │   └── exceptions.py
│   │
│   ├── graph/
│   │   ├── nodes/                       # Individual LangGraph node implementations
│   │   │   ├── execute_developer.py
│   │   │   ├── execute_progress_evaluator.py
│   │   │   ├── execute_runner_green.py
│   │   │   ├── execute_runner_red.py
│   │   │   ├── execute_tester.py
│   │   │   ├── plan_task.py
│   │   │   ├── requirements_analyst.py
│   │   │   ├── requirements_engineer.py
│   │   │   └── requirements_user_input.py
│   │   ├── subgraphs/
│   │   │   ├── build_tdd_subgraph.py    # Inner TDD Red-Green-Refactor subgraph
│   │   │   └── requirements_orchestrator_subgraph.py
│   │   └── orchestrator.py              # Main LangGraph graph construction & compilation
│   │
│   ├── prompts/
│   │   ├── agents/langgraph/            # Jinja2 system & human prompt templates per agent
│   │   └── specs/                       # Example problem specifications for testing
│   │
│   ├── schema/
│   │   └── schema.py                    # Pydantic models for structured LLM output
│   │
│   ├── utils/
│   │   ├── chat_model_factory.py        # Multi-provider LLM client factory
│   │   ├── pass_rate.py
│   │   ├── prompt_loader.py
│   │   ├── resilience_metrics.py        # Fault tracking & recovery metrics
│   │   ├── sandbox_utils.py
│   │   ├── spec_loader.py
│   │   ├── token_metrics.py             # Token usage tracking
│   │   └── workspace.py
│   │
│   └── main.py                          # CLI entry point
│
├── assets/
│   ├── arquitetura_geral.png            # Overall architecture diagram (two-phase flow)
│   └── subgrafo.png                     # TDD Subgraph internal structure
│
├── mutation_tests/                      # Mutation testing results
├── backup/                              # SonarQube analysis scripts & config
├── docker-compose.yaml                  # PostgreSQL service
├── docker-compose.sonarqube.yaml        # SonarQube service for quality analysis
├── requirements.txt                     # Python package dependencies
└── .env.example                         # Environment variable template
```

---

## Getting Started

### Prerequisites

| Requirement | Version | Purpose |
|-------------|---------|---------|
| **Python** | 3.10+ | Runtime |
| **Docker** | Latest | Hosts PostgreSQL locally |
| **LLM API Key** | — | OpenAI, Anthropic, Gemini, or DeepSeek |
| **E2B API Key** | e2b_309a046e57b5b145e9855b913af9745d824b2e52 | Cloud sandbox code execution (research api key token) |

> **Get your own E2B API key** at [e2b.dev](https://e2b.dev) — required for all code execution. (they give you $100 dollars of credit).

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
# ─── LLM Provider (pick one or more) ──────────────────────────────
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
DEEPSEEK_API_KEY=...

# ─── E2B Cloud Sandbox ─────────────────────────────────────────────
E2B_API_KEY=e2b_...

# ─── Database (Docker) ─────────────────────────────────────────────
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
- Once you see the Requirements displayed in the terminal screen, type `/sim` as an answer. This will trigger the execution graph to move to the Engineer phase.

Note: The Analyst can ask more questions. You can also ask more features or corrections to be made. However, for research purposes, we only consider executions where the Analyst immediatelly collects and displays the requirements with no questions asked. Also, no other features are asked after the requirements are defined.

---

## Execution Flags

### Resume Execution *(continue from checkpoint)*

```bash
python -m app.main --thread-id "tdd-1a2b3c4d..."
```

Resumes a previous session from the LangGraph checkpoint stored in PostgreSQL. Useful for recovering from crashes or manual interrupts.

> **Thread IDs** are printed to the console at the start of every run. Save them to resume long-running sessions.

---

## Output & Deliverables

Upon completion, each run produces a `workspace_output_tdd_<thread_id>/` directory (with the specific thread_id of the execution) containing:

```
workspace_output/
├── src/                                # Generated production code
├── tests/                              # Generated pytest test suites
└── metrics_and_logging/
    ├── initial_user_prompt.txt         # Initial prompt sent by the User
    ├── confirmed_user_requirements.txt # Analyst-generated requirements approved by the user
    ├── engineer_specifications.txt     # Specs generated by the Engineer Agent based on the confirmed_user_requirements.txt
    ├── planner.txt                     # tdd_plan decomposition
    ├── execution_logs.txt              # Full agent trace
    ├── resilience_metrics.txt          # Fault counts by type
    ├── token_usage.txt                 # Token usage
    ├── subreq_results.txt              # Number of satisfied requirements and general TDD workflow data
    └── user_analyst_dialogue.txt       # Full dialogue between the user and the Analyst Agent.

```
---

## Experiments

TDDAgents was evaluated on **15 executions** across 5 different programming tasks (3 independent runs per task), as part of a study submitted to [SBES 2026](https://cbsoft.sbc.org.br/2026/sbes/). The evaluation results are available in the `experimental_executions/` and `mutation_tests/` directories. 

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

Copy the required files into the **root of the TDD output workspace** (`workspace_output_tdd_<thread_id>/`):

```bash
# From the repository root
cp backup/analyze.sh  workspace_output_tdd_<thread_id>/
cp backup/sonar-project.properties  workspace_output_tdd_<thread_id>/
chmod +x workspace_output_tdd_<thread_id>/analyze.sh
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
cd workspace_output_tdd_<thread_id>/
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
