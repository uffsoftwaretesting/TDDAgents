"""
Streaming and recovery infrastructure (Part F).

Modules:
- abort: Three-controller abort tree (turn, sibling, per-tool) and signals.
- withhold: Withhold-then-decide gate snapshot and predicates.
- executor: StreamingToolExecutor with concurrency control and ordered emission.
"""
