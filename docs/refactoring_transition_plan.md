# TDDAgents: Refactoring Transition Plan

**Status:** Completed & Fully Tested (All Phases Verified)
**Objective:** Outline the sequential, phased refactoring strategy to fully align the remaining TDDAgents subsystems with the Claude Code `while-loop` architecture. The core `engine.py` loop has been established, and all four surrounding architectural plans have been completed with comprehensive unit test coverage.

---

## Plan A: Subagent Orchestration & Delegation — [COMPLETED]
**Goal:** Transition from static LangGraph nodes (`Analyst`, `Planner`, `Developer`) to dynamic, nested Claude Code-style subagents spawned directly from the core while-loop.

### Completed Implementations
1. **Dynamic Instantiation:** Implemented `create_subagent` in `app/loop/agents/subagent.py` dynamically loading `agent-prompts` from the `PromptRegistry` with multi-format variable rendering (`{{var}}`, `${var}`, `{var}`).
2. **Context Isolation:** Enforced that subagents receive an isolated `ToolContext` with `discard_app_state_update` as the `set_app_state` setter, preventing any child writes from mutating the parent TDD Ledger.
3. **Delegation Tooling:** Enhanced `AgentTool` (`build_agent_tool`) in `app/loop/agents/tool.py` to delegate to `create_subagent`, supporting sync in-process execution and async background dispatch while guarding against recursive fork loops.
4. **Unit Tests:** `tests/test_plan_a_subagent_orchestration.py` (5/5 passing).

---

## Plan B: Extensibility Mechanisms (MCP & Skills) — [COMPLETED]
**Goal:** Finalize the integration of the four Claude Code extensibility boundaries (MCP, Plugins, Skills, Hooks) into the new pool architecture.

### Completed Implementations
1. **Hooks Dispatcher:** Implemented `tdd_pre_tool_use_hook` and `tdd_post_tool_use_hook` in `app/loop/tdd/hooks.py` mapped directly to the `PhaseLedger`. Enforced phase boundaries in `app/loop/tools/execution.py` (blocking production edits in RED phase, blocking test alterations in GREEN phase, and observing test feedback).
2. **MCP Router Integration:** Created `MCPRouter` and `build_mcp_tool` in `app/loop/tools/mcp.py` conforming to canonical `mcp__<server>__<tool>` naming and `is_mcp=True`. Updated `assemble_tool_pool` in `app/loop/tools/pool.py` to accept `MCPRouter` and partition-sort MCP tools after built-in tools with prefix deny-rule matching.
3. **Skills Execution:** Connected the 71 skill prompts from `app/loop/prompts/skill-prompts/` to `discover_skills` in `app/loop/skills/loader.py` with multi-tier alias resolution (`artifact-diagramming`, `Skill: Artifact diagramming`). Verified execution through `SkillTool` (`app/loop/skills/tool.py`).
4. **Unit Tests:** `tests/test_plan_b_extensibility.py` (6/6 passing).

---

## Plan C: Persistence & The `Memdir` System — [COMPLETED]
**Goal:** Transition from basic artifact dumping to the 5-layer compaction and append-only `memdir` storage system used by Claude Code.

### Completed Implementations
1. **Session Checkpointing:** Enhanced `TranscriptLogger` in `app/loop/transcript.py` with `checkpoint_state`, `read_events`, and `get_last_checkpoint`, persisting full `LoopState` snapshots and message arrays to append-only JSONL files.
2. **Context Compaction:** Fully wired `microcompact_tool_results` and `apply_tool_result_budget` in `app/loop/model.py` (`build_request_messages`) to collapse older turn results and cap oversized payloads before passing to the model.
3. **Cross-Session Memory:** Implemented `extract_session_memory` and `update_auto_memory` in `app/loop/context/memory.py` updating `~/.tddagents/projects/<project>/memory/MEMORY.md` within strict line (`MAX_ENTRYPOINT_LINES = 200`) and byte (`MAX_ENTRYPOINT_BYTES = 25_000`) bounds.
4. **Unit Tests:** `tests/test_plan_c_persistence_memdir.py` (4/4 passing).

---

## Plan D: Observability & Resilience Metrics — [COMPLETED]
**Goal:** Stabilize the metrics generation for the TDD runs under the new loop paradigm.

### Completed Implementations
1. **Token Tracking Adapter:** Exposed `get_global_token_tracker` and `get_token_usage_summary` in `app/loop/model.py` wrapping `GlobalTokenTracker` directly into model streaming.
2. **Resilience Logs:** Authored `ResilienceTracker`, `ResilienceReport`, and `SubRequirementMetric` in `app/loop/resilience.py`. Emits structured pass/fail rates for every sub-requirement, tracks recovery transitions (`STOP_HOOK_BLOCKING`, `REACTIVE_COMPACT_RETRY`), and renders human-readable scorecards and JSON logs.
3. **Unit Tests:** `tests/test_plan_d_observability_resilience.py` (3/3 passing).

---

## Test Verification Summary
All 18 new integration tests across Plans A, B, C, and D and all 3,986 tests in the full test suite pass with 100% success rate:
- **Plan A Tests:** 5 passed
- **Plan B Tests:** 6 passed
- **Plan C Tests:** 4 passed
- **Plan D Tests:** 3 passed
- **Full Test Suite:** 3,986 passed, 4 skipped, 0 failed.
