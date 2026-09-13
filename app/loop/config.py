"""
The once-per-run snapshot of everything that must not change while the run is in flight.

First of the three kinds in §3.1 of `docs/transition_elaboration_plan.md`, and a port of
`reference/claude-code/src/query/config.ts` -> `QueryConfig` / `buildQueryConfig`. The
in-source rationale for separating it from the per-iteration record is worth keeping in
view, because it is a design constraint and not a tidiness argument: it *"makes future
step() extraction tractable — a pure reducer can take (state, event, config) where config
is plain data."*

**There are no ceilings here, and that is a decision rather than an omission.** No
`max_turns`, no retry cap, no bound on how many times a recovery path may fire. The loop
runs until it terminates for a reason in its own vocabulary. Upstream's `maxTurns` is
`number | undefined` and the loop tests it as `if (maxTurns && ...)`, so falsy already
means unbounded; when Part A6 gives this loop the same knob it arrives as an entry
argument the way it does upstream, not as a value living in this record. The consequence
worth naming: nothing in the architecture will stop a Stop hook that refuses the exit
forever, so the hooks of Part D5 carry that responsibility alone.

What does belong here is state the run reads repeatedly and that would change the meaning
of a turn if it flipped halfway through. Snapshotting it once is what makes a turn
explicable after the fact: whatever the environment did during the run, every iteration
saw these values.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.config import Config


@dataclass(frozen=True, slots=True)
class Gates:
    """
    Environment facts frozen at entry, because a mid-run flip would be invisible.

    `web_tools_available` decides whether the web tools exist in the roster at all. If the
    key were read per turn, a run could assemble a different tool pool at turn 9 than at
    turn 1 — which changes the prompt prefix, and so both the model's options and the
    cache key, for reasons no transcript would record.

    `postgres_checkpointing` records whether the run is actually persisting to Postgres or
    fell back to an in-memory checkpointer. That fallback exists today in the orchestrator
    and is currently only a log line; as a gate it is a fact the run can be judged against
    afterwards — a resumable run and an unresumable one are not the same experiment.
    """

    web_tools_available: bool
    postgres_checkpointing: bool


@dataclass(frozen=True, slots=True)
class RunConfig:
    """
    Identity plus gates. Deliberately small.

    `run_id` is supplied by the caller rather than generated here: the session shell of
    Part K1 derives it from the specification so a run can be resumed from its checkpoint,
    and a config object that minted its own id would quietly break that.
    """

    run_id: str
    gates: Gates


def build_run_config(run_id: str, *, postgres_checkpointing: bool) -> RunConfig:
    """
    The one place a `RunConfig` is resolved from the environment.

    The two gates are resolved differently on purpose. Web tool availability is a fact
    about configuration, so it is read here from the single constant that holds it and no
    caller can contradict it. Whether Postgres checkpointing is live is a fact about an
    attempted connection, which only the caller that made the attempt can know, so it is
    passed in — inventing it here would mean guessing.
    """
    return RunConfig(
        run_id=run_id,
        gates=Gates(
            web_tools_available=bool(Config.TAVILY_API_KEY),
            postgres_checkpointing=postgres_checkpointing,
        ),
    )
