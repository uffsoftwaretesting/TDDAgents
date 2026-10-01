# Claude Code Architecture & Internals: Comprehensive Reverse Engineering Analysis

> **Sources & References:**
> - [Vikash Rungta: *Claude Code Architecture (Reverse Engineered) — Inside The Architecture of an Autonomous Agent*](https://vrungta.substack.com/p/claude-code-architecture-reverse)
> - [Kir Shatrov: *Reverse engineering Claude Code — Prompts, Internals, and Tool Calling*](https://kirshatrov.com/posts/claude-code-internals)
> - Upstream System Prompts Repository: `~/claude-code-system-prompts` (Claude Code v2.1.286+)

---

## Executive Summary

This document synthesizes deep architectural reverse-engineering and empirical network traffic analysis of **Claude Code** (Anthropic's agentic coding CLI). 

While traditional AI coding assistants operate either as stateless chatbots or rigid workflow directed acyclic graphs (DAGs), Claude Code represents a paradigm shift into **Superagents**: autonomous, model-driven agentic loops wrapped inside a sophisticated local system harness. Rather than relying on hardcoded chains of execution or dozens of brittle API integrations, Claude Code delegates executive control entirely to the frontier model (the "CEO"), providing it with primitive OS capabilities (Bash, file read/write, grep, glob), a rigorous safety/permission barrier, layered persistent memory, and proactive context compaction.

```
                    ┌────────────────────────────────────────────────────────┐
                    │                      User Prompt                       │
                    └───────────────────────────┬────────────────────────────┘
                                                │
                                                ▼
                    ┌────────────────────────────────────────────────────────┐
                    │            Topic & Intent Classification               │
                    │      (claude-3-5-haiku detects isNewTopic / title)     │
                    └───────────────────────────┬────────────────────────────┘
                                                │
                                                ▼
     ┌──────────────────────────────────────────────────────────────────────────────────────┐
     │                             Claude Code Local Harness                                │
     │                                                                                      │
     │   ┌──────────────────────────────────────────────────────────────────────────────┐   │
     │   │                       Layered Memory & Prompt Assembler                      │   │
     │   │  - Baked-in Agent Identity & Rules         - ~/.claude/CLAUDE.md (User)      │   │
     │   │  - ./CLAUDE.md / .cursorrules (Project)    - Auto-Memory & Learned Patterns  │   │
     │   │  - Dynamic Context Injection (Git, Tree)   - Ephemeral History & Scratchpad  │   │
     │   └──────────────────────────────────────┬───────────────────────────────────────┘   │
     │                                          │                                           │
     │                                          ▼                                           │
     │   ┌──────────────────────────────────────────────────────────────────────────────┐   │
     │   │                       Core Agentic Loop (TAOR Engine)                        │   │
     │   │                                                                              │   │
     │   │          ┌──────────┐         ┌──────────┐         ┌─────────────┐           │   │
     │   │          │ Thought  │  ────►  │  Action  │  ────►  │ Observation │           │   │
     │   │          └──────────┘         └────┬─────┘         └──────┬──────┘           │   │
     │   │               ▲                    │                      │                  │   │
     │   │               │                    ▼                      │                  │   │
     │   │               │            ┌───────────────┐              │                  │   │
     │   │               │            │ Security Gate │              │                  │   │
     │   │               │            │  (Prefix/Inj) │              │                  │   │
     │   │               │            └───────┬───────┘              │                  │   │
     │   │               │                    ▼                      │                  │   │
     │   │               │            ┌───────────────┐              │                  │   │
     │   │               │            │ Tool Exec OS  │ ─────────────┘                  │   │
     │   │               │            └───────────────┘                                 │   │
     │   │               │                                                              │   │
     │   │               └──────────────── Reflection ◄─────────────────────────────────┘   │
     │   │                                                                                  │   │
     │   │   * Budgeted by maxTurns & Auto-Compaction (~50% context watermark)              │   │
     │   └──────────────────────────────────────┬───────────────────────────────────────┘   │
     │                                          │                                           │
     │                                          ▼                                           │
     │   ┌──────────────────────────────────────────────────────────────────────────────┐   │
     │   │                         Delegation & Tool Extensions                         │   │
     │   │  - Primitive Tools (Bash, Glob, Grep, Replace, Write)                        │   │
     │   │  - Sub-Agents: Explore, Plan, Custom Agents (Isolated Contexts)              │   │
     │   │  - Model Context Protocol (MCP) Tools & Resources                            │   │
     │   │  - Agent Teams & Lifecycle Quality Gate Hooks                                │   │
     │   └──────────────────────────────────────────────────────────────────────────────┘   │
     └──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 1: High-Level Architecture & Paradigm Shifts (Vikash Rungta)

### 1.1 The Three Eras of LLM Applications

1. **Era 1: Chatbots (Stateless Q&A)**
   - Single-turn or ephemeral chat.
   - The user carries all cognitive load, prompts repeatedly, pastes code snippets manually.
   - Zero execution capability; completely disconnected from the local OS and repository state.
2. **Era 2: Workflows (Rigid Code-Driven Chains)**
   - Hardcoded directed acyclic graphs (DAGs), e.g., LangChain chains, n8n workflows, predefined script pipelines.
   - Code controls the LLM: "If step A outputs X, run step B, then call LLM C."
   - Extremely brittle in dynamic software projects where unexpected build errors, compiler nuances, or divergent repo structures break the rigid DAG.
3. **Era 3: Autonomous Agents / "Superagents" (Model-Driven Loops)**
   - Model controls the loop: The LLM evaluates state, selects tools, inspects outputs, self-corrects, and decides termination.
   - The runtime is an ultra-reliable, minimal "dumb harness" (providing shell, filesystem, process lifecycle, security gates).
   - Claude Code represents the archetype of Era 3.

### 1.2 The 6 Foundational Architectural Shifts

| Shift | From | To | Why It Matters |
| :--- | :--- | :--- | :--- |
| **1. Control Flow** | Workflows (Code controls Model) | Loops (Model controls Code/Loop) | Real codebases are unpredictable. The model must dynamically decide its next action based on actual feedback. |
| **2. Scaffolding** | Monolithic Prompt | Local System Harness ("The Body") | The model is the brain; the harness provides sensory input (terminal output, git status) and physical limbs (exec, edit). |
| **3. Tool Strategy** | 100 Specialized API Plugins | Universal Capability Primitives | Primitives (`Bash`, `Grep`, `Glob`, `Edit`) compose infinitely and never break when third-party API contracts change. |
| **4. Context Strategy** | Greedily Stuffing Tokens | Context as a Finite Economic Resource | Token exhaustion leads to cognitive degradation. Proactive compaction and sub-agent isolation preserve attention. |
| **5. Failure Handling**| Bugs to be patched with more code | Structural constraints to be managed | Runaway loops, amnesia, and permissions are handled by `maxTurns`, layered memory, and prefix sandboxing. |
| **6. Scaffolding Evolution** | Expanding Scaffold Complexity | Scaffold Minimization ("Co-Evolution") | The harness is designed to shrink over time as foundational models grow more capable, not grow more complex. |

---

## Part 2: The 8 Universal Agent Failure Modes & Claude Code's Solutions

Every autonomous coding agent faces eight fundamental failure modes. Claude Code's entire harness is engineered to solve these structural hazards:

| # | Failure Mode | Manifestation | Claude Code Architectural Solution |
| :--- | :--- | :--- | :--- |
| **1** | **Runaway Loops** | Agent repeats identical failing commands or hallucinates nonexistent files infinitely. | Hard `maxTurns` limit combined with model-driven stopping criteria and loop reflection checks. |
| **2** | **Context Collapse** | Context fills with verbose compiler outputs, test traces, or files; attention degrades. | Proactive auto-compaction at ~50% context watermark (~100k tokens), output truncation, and sub-agent isolation. |
| **3** | **Permission Roulette** | Either dangerous commands execute unchecked, or the user suffers severe prompt fatigue. | 6 permission modes, granular command prefix allowlists, command injection heuristics, and path-based globs. |
| **4** | **Agent Amnesia** | Agent loses architectural memory, test commands, or coding conventions across restarts. | 6-layer memory hierarchy: global `~/.claude/CLAUDE.md`, project `./CLAUDE.md`, auto-memory, and session transcripts. |
| **5** | **Monolithic Context** | Agent attempts wide-ranging exploration and deep implementation in a single context window. | Sub-agent delegation: `Explore` runs in a disposable sub-agent context, returning only concise answers to the main loop. |
| **6** | **Hard-Coded Fragility** | Hard-coded steps fail when encountering diverse languages, build systems, or environments. | Declarative extensibility: Skills, Custom Agents, Hooks, MCP, and Plugins configured via Markdown and YAML. |
| **7** | **The Black Box Problem**| Developer has zero auditability or visibility into agent execution and decisions. | Deterministic lifecycle hooks (`PreToolUse`, `PostToolUse`), fine-grained logging, and "Collapsed but Available" UI. |
| **8** | **Single-Threaded Stalls** | Slow tests, git operations, or searches block the user and agent entirely. | Sub-agents with background execution support and experimental Agent Teams multi-session parallelism. |

---

## Part 3: Deep Dive into the Local Execution Harness

### 3.1 The TAOR Agentic Loop
The core execution engine runs the **TAOR** loop:
1. **Thought**: The model evaluates conversation history, memory layers, and previous tool outputs to formulate a hypothesis.
2. **Action**: The model emits a structured tool invocation (e.g., `Bash`, `Replace`, `GlobTool`).
3. **Observation**: The local harness intercepts the call, validates permissions/safety, executes it in the local environment, and streams stdout/stderr back into the conversation context.
4. **Reflection**: The model reads the observation, evaluates whether the outcome matches expectations, updates its internal plan, and either decides the next action or terminates.

### 3.2 Capability Primitives vs. Specialized Integrations
Rather than implementing high-level tools like `JiraIssueCreator`, `GitCommitHelper`, or `PythonLinter`, Claude Code relies strictly on low-level primitives:
- `Bash`: Arbitrary command-line execution (compiling, running test runners, git commands, package management).
- `GlobTool`: Fast file-pattern matching across the directory tree.
- `GrepTool`: Fast regex content searching across files.
- `FileRead` / `View`: Reading file contents with slice notation and line ranges.
- `FileEdit` / `Replace`: Surgical string replacements matching exact contiguous text blocks.
- `Write`: Creating new files or overwriting existing files completely.
- `TodoWrite`: Maintaining task state and progress tracking.

*Why this matters:* A human developer accomplishes 99% of tasks through terminal commands and file edits. By providing primitives, the agent can use `pytest`, `cargo`, `mvn`, `npm`, `git rebase`, or internal proprietary tools without requiring custom plugin code.

### 3.3 Composable Permissions & Sandboxing
Claude Code supports 6 distinct permission tiers:
1. **Default Mode**: Prompts user for approval on any state-modifying or potentially destructive action.
2. **Accept Non-Destructive**: Auto-approves safe read/search/status commands; prompts for modifications.
3. **Accept File Edits**: Allows automatic file modifications and safe commands; prompts for dangerous bash operations.
4. **Accept All (Bypass / Dangerous)**: Completely autonomous execution without confirmation gates.
5. **Strict / Ask All**: Prompts for every single tool invocation.
6. **Plan Only**: Disables write and execution tools, restricting the agent to read-only research and planning.

### 3.4 The 6-Layer Memory Architecture
Claude Code never starts from zero. Memory is injected at multiple scopes:
- **Layer 1: Base System Identity**: Fixed system prompt defining role, safety constraints, coding standards, and tool usage rules.
- **Layer 2: User Global Memory**: `~/.claude/CLAUDE.md` and user-level preference configs loaded across all projects.
- **Layer 3: Project Instructions**: `./CLAUDE.md`, `.cursorrules`, and `.github/copilot-instructions.md` containing repo-specific build, test, and style commands.
- **Layer 4: Auto-Memory**: Dynamically persisted patterns, key project paths, and architecture notes learned by the agent during past sessions.
- **Layer 5: Scratchpad / Task State**: Ephemeral planning structures (`TODO.md`, session notes) tracking multi-step milestones.
- **Layer 6: Active Context**: The ongoing conversation turns, user messages, and tool input/output observations.

### 3.5 Context Economy & Auto-Compaction
In a 200,000 token context window, reasoning quality declines as the window saturates ("Context Collapse"). Claude Code manages context aggressively:
- **Proactive Compaction Threshold**: When context reaches ~50% capacity (~100,000 tokens), compaction triggers automatically.
- **Summarization Strategy**: Previous conversation turns are condensed into an information-dense summary preserving architectural decisions, file changes, and current goals.
- **Output Truncation**: Verbose compiler traces, directory listings, and diffs are truncated with clear byte and line indicators.
- **Sub-Agent Offloading**: Exploratory searches that produce thousands of tokens of file scans are delegated to sub-agents whose contexts are discarded after returning the synthesis.

---

## Part 4: Declarative Extensibility: Skills, Sub-Agents, Teams & Hooks

```
                           Declarative Extension Hierarchy
                           
                  ┌─────────────────────────────────────────┐
                  │                 Plugins                 │
                  │  (Distributable archive / npm package)  │
                  └────────────────────┬────────────────────┘
                                       │
        ┌──────────────┬───────────────┼───────────────┬──────────────┐
        │              │               │               │              │
        ▼              ▼               ▼               ▼              ▼
  ┌───────────┐  ┌───────────┐   ┌───────────┐   ┌───────────┐  ┌───────────┐
  │  Skills   │  │Sub-Agents │   │Agent Teams│   │   Hooks   │  │    MCP    │
  │  (Prompt  │  │ (Isolated │   │ (Parallel │   │ (Lifecyle │  │(Ext Tools │
  │  Macros)  │  │  Workers) │   │   Peers)  │   │ Triggers) │  │& Servers) │
  └───────────┘  └───────────┘   └───────────┘   └───────────┘  └───────────┘
```

### 4.1 Skills (Prompt Macros)
Skills are modular directories containing `SKILL.md` with YAML frontmatter. When invoked, their contents are dynamically injected into the system prompt to guide specialized workflows (e.g., end-to-end testing, frontend design, database migrations) without requiring agent code changes.

### 4.2 Sub-Agents (Isolated Workers)
Sub-agents fork isolated TAOR loops with their own context windows:
- **Built-in Sub-Agents**:
  - `Explore`: Read-only agent with `GlobTool`, `GrepTool`, and `FileRead` designed to scout the codebase without polluting the main context.
  - `Plan`: Dedicated agent for requirements analysis, gap analysis, and phased implementation planning.
- **Custom Sub-Agents**: Defined declaratively via markdown files with YAML frontmatter:
  ```markdown
  ---
  name: security-auditor
  description: Reviews modified files for security vulnerabilities
  tools: [FileRead, GrepTool]
  model: sonnet
  ---
  You are an expert security engineer reviewing code changes...
  ```
- **Foreground vs. Background**: Sub-agents can run synchronously (blocking parent until done) or asynchronously in the background.

### 4.3 Agent Teams (Multi-Session Parallelism)
Experimental framework for multi-agent collaboration across separate processes:
- **Peer Coordination**: Multiple agents collaborate on different modules or git worktrees simultaneously.
- **Display Modes**: Split-pane terminal views or consolidated notification feeds.
- **Quality Gates**: Pre-merge hooks and peer code review passes.

### 4.4 Lifecycle Hooks
Deterministic shell scripts or commands triggered on agent events:
- `PrePrompt`: Executed before the agent receives a user prompt.
- `PreToolUse`: Intercepts tool execution (allows blocking dangerous commands or enforcing linting).
- `PostToolUse`: Fires after a tool completes (e.g., auto-formatting code after `Replace` or `Write`).
- `SessionStart` / `SessionEnd`: Initialization and cleanup routines.

### 4.5 Model Context Protocol (MCP) Integration
Claude Code implements the open Model Context Protocol:
- Connects local and remote MCP servers (databases, browser automation like Puppeteer, Jira, Slack).
- Standardizes tool schemas, resources, and prompt templates across the ecosystem.

---

## Part 5: Empirical Network & Reverse Engineering Analysis (Kir Shatrov)

Kir Shatrov captured and analyzed Claude Code's real-time API requests and prompt payloads over the wire using `mitmproxy`. This reveals the exact operational mechanics behind the high-level architecture.

### 5.1 Interception Methodology
```bash
# Install and run transparent reverse proxy
brew install mitmproxy
mitmweb --mode reverse:https://api.anthropic.com --listen-port 8000

# Route Claude Code traffic through local proxy
ANTHROPIC_BASE_URL=http://localhost:8000/ claude
```

### 5.2 Turn 0: Topic & Intent Classification Hook
Before processing user input in the primary loop, Claude Code makes a lightweight utility call to assess conversational continuity:

```
Analyze if this message indicates a new conversation topic.
If it does, extract a 2-3 word title that captures the new topic.
Format your response as a JSON object with two fields: 'isNewTopic' (boolean) and 'title' (string, or null if isNewTopic is false). Only include these fields, no other text.
```

- **Output Structure**: `{"isNewTopic": false, "title": null}` or `{"isNewTopic": true, "title": "Top HackerNews Script"}`.
- **Model Used**: Routed to faster, cheaper model (`claude-3-5-haiku`) to minimize latency and token spend.
- **Purpose**: Powers terminal session titling and topic boundary tracking for context compaction.

### 5.3 Initial Context & Prompt Overhead
On the very first turn, Claude Code sends a massive initial prompt payload:
- **Prompt Size**: Between **15,000 and 20,000+ tokens** on turn 1.
- **Components Injected**:
  - Full tool schemas (`Bash`, `GlobTool`, `GrepTool`, `FileRead`, `Replace`, `Write`, etc.).
  - Complete base system instructions, security guidelines, and formatting constraints.
  - Formatted repository directory tree (excluding gitignored patterns).
  - Content of `CLAUDE.md` and related instruction files.
- **Economic Consequence**: Even a simple question like *"describe what's in this project"* incurs non-trivial token consumption and a noticeable initial latency delay (~3-5 seconds).

### 5.4 The Bash Security Gatekeeper & Injection Detection
When the agent decides to execute a `Bash` command, the command is **not** immediately executed. Instead, it is routed through an explicit safety evaluation prompt:

```xml
<policy_spec>
# Claude Code Bash command prefix detection

This document defines risk levels for actions that the Claude Code agent may take. 
This classification system is part of a broader safety framework and is used to determine 
when additional user confirmation or oversight may be needed.

## Definitions
**Command Injection:** Any technique used that would result in a command being run other than the detected prefix.

## Command prefix extraction examples
Examples:
- cat foo.txt => cat
- cd src => cd
- find ./src -type f -name "*.ts" => find
- git commit -m "foo" => git commit
- git diff HEAD~1 => git diff
- git diff $(pwd) => command_injection_detected
- git status => git status
- git status# test(`id`) => command_injection_detected
- git status`ls` => command_injection_detected
- git push => none
- git push origin master => git push
- git log -n 5 => git log
- grep -A 40 "from foo.bar.baz import" alpha/beta/gamma.py => grep
- npm run lint => none
- npm run lint -- "foo" => npm run lint
- npm test => none
- npm test -- -f "foo" => npm test
- pwd
curl example.com => command_injection_detected
- pytest foo/bar.py => pytest
- sleep 3 => sleep
</policy_spec>

The user has allowed certain command prefixes to be run, and will otherwise be asked to approve or deny the command.
Your task is to determine the command prefix for the following command.

IMPORTANT: Bash commands may run multiple commands that are chained together.
For safety, if the command seems to contain command injection, you must return "command_injection_detected".
(This will help protect the user: if they think that they're allowlisting command A,
but the AI coding agent sends a malicious command that technically has the same prefix as command A,
then the safety system will see that you said “command_injection_detected” and ask the user for manual confirmation.)

Note that not every command has a prefix. If a command has no prefix, return "none".

ONLY return the prefix. Do not return any other text, markdown markers, or other content or formatting.

Command: chmod +x hn_top.sh
```

### 5.5 Filepath Extraction Prompt
Immediately following or alongside prefix detection, another prompt extracts affected file paths to present clear visual diffs and allowlist prompts to the user:

```
Extract any file paths that this command reads or modifies. For commands like "git diff" and "cat", include the paths of files being shown. Use paths verbatim -- don't add any slashes or try to resolve them. Do not try to infer paths that were not explicitly listed in the command output.
Format your response as:
<filepaths>
path/to/file1
path/to/file2
</filepaths>

If no files are read or modified, return empty filepaths tags: <filepaths></filepaths>
Do not include any other text in your response.
```

### 5.6 Codebase Onboarding: The `/init` Workflow
When the user executes `/init`, Claude Code runs a specialized prompt designed to bootstrap `CLAUDE.md`:

```
Please analyze this codebase and create a CLAUDE.md file containing:
1. Build/lint/test commands - especially for running a single test
2. Code style guidelines including imports, formatting, types, naming conventions, error handling, etc.

Usage notes:
- The file you create will be given to agentic coding agents (such as yourself) that operate in this repository. Make it about 20 lines long.
- If there's already a CLAUDE.md, improve it.
- If there are Cursor rules (in .cursor/rules/ or .cursorrules) or Copilot rules (in .github/copilot-instructions.md), make sure to include them.
- Be sure to prefix the file with the following text:

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
```

To execute this, Claude Code issues a parallelized `BatchTool` call combining multiple `GlobTool` queries:
```json
{
  "type": "tool_use",
  "id": "toolu_01GmZ4d81pGHKaqryDEuZQNm",
  "name": "BatchTool",
  "input": {
    "description": "Gather repository information",
    "invocations": [
      { "tool_name": "GlobTool", "input": { "pattern": "package*.json" } },
      { "tool_name": "GlobTool", "input": { "pattern": "*.md" } },
      { "tool_name": "GlobTool", "input": { "pattern": ".cursor/rules/**" } },
      { "tool_name": "GlobTool", "input": { "pattern": ".cursorrules/**" } },
      { "tool_name": "GlobTool", "input": { "pattern": ".github/copilot-instructions.md" } }
    ]
  }
}
```

### 5.7 Multi-Tier Model Routing Architecture
Kir Shatrov's traffic inspection verified Anthropic's multi-tier model strategy:
- **Core Reasoning Engine (`claude-3-7-sonnet`)**: Drives the main TAOR loop, complex code edits, multi-file refactoring, planning, and debugging.
- **Utility & Gatekeeper Engine (`claude-3-5-haiku`)**: Handles high-frequency, low-latency utility tasks:
  - Topic classification (`isNewTopic`)
  - Command prefix extraction
  - Command injection vulnerability checks
  - Filepath extraction for UI approval modals

---

## Part 6: Comparative Analysis: Claude Code vs. Other AI Coding Paradigms

| Dimension | Claude Code | Cursor / Copilot (IDE Plugins) | Aider (Terminal Agent) | OpenHands / Devin |
| :--- | :--- | :--- | :--- | :--- |
| **Primary Interface** | Terminal / CLI native | VS Code / JetBrains IDE GUI | Terminal / CLI native | Web UI / Remote Sandboxed VM |
| **Execution Environment**| Local host OS with user permissions | Local IDE / Language Server | Local host OS with Git repo | Docker container / Remote VM |
| **Control Flow** | Fully autonomous TAOR loop | Human-driven in-editor completions / inline edits | Semi-autonomous prompt-and-patch loop | Fully autonomous remote agent loop |
| **Tool Calling Style** | Primitive OS tools (`Bash`, `Grep`, `Replace`) | IDE API calls (LSP, TextEditor AST) | Git commits, unified diff patches, Aider repo-map | Docker shell, browser, remote editor |
| **Security Architecture**| Granular prefix allowlists, injection checks | Implicit user consent inside IDE | Git commits serve as safety checkpoints | Isolated sandbox / container boundary |
| **Cost & Token Overhead**| High (~15k-20k base context + utility calls) | Low to Medium (RAG chunking, targeted diffs) | Medium (Repo-map AST, git diffs) | Very High (Full workspace streaming) |
| **Execution Autonomy** | High (can run build, test, fix, commit loop) | Low to Medium (requires user trigger per step)| Medium (runs tests if configured) | High (end-to-end task resolution) |

---

## Part 7: Synthesis & Architectural Lessons for Agent Builders

1. **The Model Must Be the CEO (Workflows Lose, Loops Win)**:
   Never hardcode multi-step code generation pipelines as static Python/TypeScript DAGs. Software engineering is inherently non-linear; the agent must be able to branch, observe compiler feedback, backtrack, and iterate.
2. **Primitives Are Universally Scalable**:
   Avoid creating dozens of domain-specific tools. An agent equipped with robust `Bash`, `Glob`, `Grep`, `Read`, and `Replace` tools can master any language, framework, build system, or CLI tool.
3. **Context Must Be Actively Defended**:
   Context windows are scarce cognitive spaces. Auto-compacting around 50% capacity and routing reconnaissance to isolated sub-agents prevents hallucinations and cognitive decay.
4. **Security Must Be Multi-Layered**:
   Never pipe raw LLM outputs directly into `exec()`. Run syntax and prefix analyzers, command-injection classifiers, and path extractors before executing shell operations.
5. **Layered Memory Bridges Ephemeral Context to Long-Term Continuity**:
   Support global user instructions, repo-level markdown guides (`CLAUDE.md`), and auto-learned memory to eliminate cold-start friction.

---

## Part 8: References & Prompt Inventory

Claude Code's full prompt catalog contains **810+ extracted prompts** (Claude Code v2.1.286+), organized in this documentation suite:
- `docs/agent-prompts/`: Subagents, creation assistants, slash commands, code review phases (81 prompts).
- `docs/system-prompts/`: Base system prompts, environment specifications, safety rules, system reminders (277 prompts).
- `docs/tool-prompts/`: Built-in tool descriptions and parameter schemas (`Bash`, `Write`, `Replace`, `TodoWrite`, etc.) (252 prompts).
- `docs/skill-prompts/`: Extensible skill prompt templates (71 prompts).
- `docs/data-prompts/`: Reference prompts, formats, and data schemas (129 prompts).
