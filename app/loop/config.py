"""
The once-per-run snapshot of everything that must not change while the run is in flight.

First of the three kinds in §3.1 of `docs/transition_elaboration_plan.md`, and a port of
`reference/claude-code/src/query/config.ts` -> `QueryConfig` / `buildQueryConfig`. The
in-source rationale for separating it from the per-iteration record is worth keeping in
view, because it is a design constraint and not a tidiness argument: it *"makes future
step() extraction tractable — a pure reducer can take (state, event, config) where config
is plain data."*

**The only ceiling here is upstream's own.** No `max_turns`, no bound on how many times a
recovery path may fire. `max_retries` is the one exception and it is a port, not an
invention: `withRetry.ts` bounds API attempts at `DEFAULT_MAX_RETRIES`, because a provider
that never recovers would otherwise hang a run with no exit. The loop itself still runs
until it terminates for a reason in its own vocabulary. Upstream's `maxTurns` is
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
from app.loop.retry import DEFAULT_MAX_RETRIES


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

    #: The model this run calls. Resolved once, so a transcript names what answered it.
    model: str = Config.MODEL

    #: Where `withRetry`'s 529 fallback switches to, or `None` for no fallback. Upstream's
    #: `options.fallbackModel`: when it is unset the fallback branch simply never fires.
    fallback_model: str | None = None

    #: `withRetry.ts` -> `getMaxRetries`. Attempts are `max_retries + 1`.
    max_retries: int = DEFAULT_MAX_RETRIES


def build_run_config(run_id: str, *, postgres_checkpointing: bool) -> RunConfig:
    """
    The one place a `RunConfig` is resolved from the environment.

    The two gates are resolved differently on purpose. Web tool availability is a fact
    about configuration, so it is read here from the single constant that holds it and no
    caller can contradict it. Whether Postgres checkpointing is live is a fact about an
    attempted connection, which only the caller that made the attempt can know, so it is
    passed in — inventing it here would mean guessing.

    The model is resolved here for the same reason: `Config.MODEL` is the single place it
    is named, and reading it once means every turn of a run is answered by the same model
    even if the constant were reloaded underneath.
    """
    return RunConfig(
        run_id=run_id,
        gates=Gates(
            web_tools_available=bool(Config.TAVILY_API_KEY),
            postgres_checkpointing=postgres_checkpointing,
        ),
        model=Config.MODEL,
        fallback_model=getattr(Config, "FALLBACK_MODEL", None),
    )
