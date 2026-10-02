# TDDAgents: Transition Elaboration Enhancement Plan (Superagent Migration)

**Status:** Proposed Enhancement Plan
**Reference Base:** 
- `docs/claude-code-architecture-and-internals-analysis.md` (Leaked Claude Code Architecture Analysis)
- `docs/transition_elaboration_plan.md` (Current TDDAgents Implementation Plan)

---

## 1. Executive Summary

This document outlines a holistic enhancement plan to upgrade TDDAgents from its current loop architecture to a fully robust **"Superagent" harness**. It applies the architectural shifts discovered in Anthropic's Claude Code (Era 3 AI) directly to our existing `transition_elaboration_plan.md`.

While TDDAgents successfully transitioned from a rigid Directed Acyclic Graph (DAG) to a loop, we must now enhance the *harness around that loop*. This means treating the context window as an economic asset, enforcing strict security gates on capability primitives, implementing a 6-layer memory hierarchy, and aggressively isolating sub-agents to prevent context collapse and runaway failures.

## 2. Core Architectural Enhancements

### 2.1 Universal Capability Primitives (Replacing Complex Integrations)
*   **The Flaw:** Specialized, high-level tools (e.g., `LinterTool`, `GitCommitHelper`) are brittle and constantly break when underlying CLI contracts change.
*   **The Claude Code Fix:** Rely exclusively on low-level primitives: `Bash`, `GlobTool`, `GrepTool`, `FileRead` (with line slicing), `Replace` (strict contiguous string replacement), and `Write`.
*   **TDDAgents Implementation:** 
    *   Deprecate high-level integrations.
    *   Update `app/prompts/tools/` to match explicit schema definitions for these primitives.
    *   Enforce **Capability as Structure**: Agents like `Planner` or `Reviewer` will physically lack write tools (`disallowedTools: ['Replace', 'Write']`) in their tool pool, rather than relying on prompt instructions to avoid edits.

### 2.2 Bash Security Gatekeeper & Injection Detection
*   **The Flaw:** Permitting terminal commands to execute blindly invites "Permission Roulette" (accidental destructive commands) or prompts the user too often.
*   **The Claude Code Fix:** An explicit intermediate safety evaluation prompt (`<policy_spec>`) that detects command prefixes and flags command injections (e.g., `git diff $(pwd)`).
*   **TDDAgents Implementation:**
    *   Introduce a lightweight, pre-execution LLM pass (using a fast model) to evaluate all `Bash` tool calls.
    *   Implement a `Filepath Extraction` prompt to generate visual diffs and targeted approval requests for the user before execution.

### 2.3 The 6-Layer Memory Architecture
*   **The Flaw:** "Agent Amnesia" causes the model to forget project conventions, testing commands, or architectural decisions across restarts.
*   **The Claude Code Fix:** A tiered memory hierarchy loaded at session start.
*   **TDDAgents Implementation:** 
    Structure context assembly in the following order:
    1.  **Base System Identity:** Hardcoded persona, loop instructions, and safety rules.
    2.  **Global Instructions:** User-level preferences (e.g., `~/.tddagents/config.md`).
    3.  **Project Instructions:** Repository-level rules (e.g., `./TDDAgents.md` or `.cursorrules`).
    4.  **Auto-Memory:** Persisted architectural notes and patterns learned dynamically.
    5.  **Task Scratchpad:** Ephemeral `TODO.md` and phase ledgers for tracking multi-step TDD phases.
    6.  **Active Context:** The ongoing conversation history and tool observations.

### 2.4 Context Economy & Auto-Compaction
*   **The Flaw:** The loop accumulates verbose compiler logs and tool outputs until it hits token limits, causing "Context Collapse" and degraded reasoning.
*   **The Claude Code Fix:** Aggressive, proactive context management.
*   **TDDAgents Implementation:**
    *   Establish a **50% capacity watermark**. 
    *   When the context hits ~50% of the maximum window, trigger a `reactive_compact` loop transition.
    *   Summarize previous turns into a dense state representation and aggressively truncate large logs/diffs.

### 2.5 True Sub-Agent Isolation
*   **The Flaw:** Monolithic context windows attempting to handle broad exploration and deep implementation simultaneously.
*   **The Claude Code Fix:** Forking isolated TAOR (Thought-Action-Observation-Reflection) loops for specific tasks.
*   **TDDAgents Implementation:**
    *   Implement isolated `Explore` (search and read) and `Plan` (architectural design) sub-agents using YAML frontmatter. 
    *   These sub-agents execute in their own context window, utilize exclusively read-only tools, and return concise summaries to the main agent, keeping the primary context clean.

## 3. Immediate Prompt Updates (Phase 1 Execution)

To begin this transition immediately, the TDDAgents prompt library (`app/prompts/`) is being restructured:

1.  **System Prompts:** `identity.md` and `system.md` will explicitly define the TAOR loop expectations, reference the 6-layer memory, and declare the "CEO" operational role.
2.  **Agent Prompts:** Introducing `agent-prompt-explore.md` and `agent-prompt-plan.md` with explicit `=== CRITICAL: READ-ONLY MODE ===` headers and tool capability restrictions.
3.  **Tool Prompts:** Rewriting tool schemas to enforce primitive usage rules (e.g., defining exact whitespace matching requirements for the `Replace` tool).

## 4. Implementation Roadmap

*   **Phase 1: Prompt Restructuring (Current)** - Aligning markdown prompt files with Claude Code patterns.
*   **Phase 2: Capability Primitives Migration** - Refactoring TDDAgents backend tools to map exactly to the core primitive suite.
*   **Phase 3: Security & Lifecycle Gates** - Building the injection detector, pre-tool hooks, and command whitelists.
*   **Phase 4: Memory & Compaction Systems** - Wiring the auto-compaction trigger and the 6-layer memory assembler into the `LoopState`.
