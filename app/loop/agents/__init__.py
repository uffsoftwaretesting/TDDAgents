"""
Subpackage for Part I: Agents and delegation.

Provides:
- I1: Agent definitions and multi-tier override loader.
- I2: Per-agent tool resolution and filtering.
- I3: The Agent tool (`agents/tool.py`) and the worker run (`agents/run_agent.py`), imported
  directly: they pull in the loop, which this package's light modules must not.
- I5: Run-scoped agent memory with automatic run-end purge.
"""
