"""
The closed vocabulary: why an iteration continued, and why the loop stopped.

Mirrors `reference/claude-code/src/query/transitions.ts`, which declares upstream's
`Continue` and `Terminal` unions. That file is **types-only, so the TypeScript compiler
erases it and it is absent from the source snapshot** — the reasons below were read off
their construction sites in `src/query.ts`, which is where the map routes for exactly this
reason. Every spelling here is the string upstream builds.

**This replaces routing on `state["status"]` literals.** The old vocabulary spanned three
families of bare strings compared in router functions, with nothing connecting the node
that set one to the router that read it; a typo produced a run that silently failed to
route. A `StrEnum` closes the set: `Continue("next_trun")` is a `ValueError` at the call
site, and the member still prints and serialises as its string, so logs and checkpoints are
unchanged.

**The set is narrower than upstream's, and deliberately so.** Upstream has seven continue
reasons and ten terminal ones. Four of those are produced by subsystems this architecture
does not have — the context-collapse drain, the token-budget continuation, and the 8k->64k
output-token escalation, which needs an output cap to escalate away from and this loop sets
no caps — and two more (`max_turns`, `image_error`) need a turn ceiling and image inputs
respectively, neither of which exists here. A member no site can produce is a member no test
can cover, which is the same objection that retired the recovery counters; they are left out
until a part of this plan produces them, at which point the spelling is waiting here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Continue(StrEnum):
    """
    Why the loop is about to run another iteration.

    Each member is set at exactly one continue site, which rebuilds `LoopState` wholesale
    and records the reason in `transition`. A test asserts the recovery path fired by
    reading that field rather than by inferring it from message contents.
    """

    #: Tools ran and their results are going back to the model. The only reason that
    #: advances `turn_count`, which is what makes every recovery iteration free.
    NEXT_TURN = "next_turn"

    #: A Stop hook refused to let the turn end and returned blocking errors, which are
    #: appended so the model is told why it may not stop. Part D5.
    STOP_HOOK_BLOCKING = "stop_hook_blocking"

    #: The context was too long, compaction recovered it, and the request is retried
    #: against the compacted history. Single-shot per turn. Part E7.
    REACTIVE_COMPACT_RETRY = "reactive_compact_retry"

    #: The model hit its output limit mid-answer and is asked to resume. Part F6.
    MAX_OUTPUT_TOKENS_RECOVERY = "max_output_tokens_recovery"


class Terminal(StrEnum):
    """
    Why the loop stopped. Exactly one of these ends every run.

    `COMPLETED` is the ordinary exit — the model answered with no tool calls — and it is
    also what an unrecoverable API error message resolves to upstream, because the turn did
    produce a final assistant message. The rest are failures or interruptions, and each is
    reached from one place in the loop.
    """

    #: The model answered without asking for a tool. The only non-exceptional exit.
    COMPLETED = "completed"

    #: The context is over the hard limit before a request is even attempted. Part E1/E2.
    BLOCKING_LIMIT = "blocking_limit"

    #: The model call failed in a way retry and fallback could not recover. Part F6.
    MODEL_ERROR = "model_error"

    #: The request was rejected as too long and compaction could not recover it. Part E7.
    PROMPT_TOO_LONG = "prompt_too_long"

    #: Cancelled while the model was streaming. Part F3.
    ABORTED_STREAMING = "aborted_streaming"

    #: Cancelled while tools were running. Part F3.
    ABORTED_TOOLS = "aborted_tools"

    #: A Stop hook demanded the turn end outright. Upstream's precedence is exact and is
    #: ported with it: `prevent_continuation` wins over blocking errors and blanks them, so
    #: a hook can never both stop the turn and ask for a retry. Part D5.
    STOP_HOOK_PREVENTED = "stop_hook_prevented"

    #: A PostToolUse hook signalled that the run must not continue. Distinct from the
    #: above: it comes from the tool path, after a tool has already run. Part H3.
    HOOK_STOPPED = "hook_stopped"


@dataclass(frozen=True, slots=True)
class Transition:
    """
    Why the previous iteration continued.

    Carried on `LoopState` purely so a test can assert that a recovery path fired without
    reading message contents to infer it — upstream added the field for that reason and
    says so in-source. Part L2 then gets its flow classification for free: F1 and F2 are
    shapes of transition history, not something a wrapper function records.

    A record rather than a bare enum member, because upstream's `Continue` is a union of
    objects and some of its members carry a payload. Nothing is declared here before a site
    sets it, which is why the record has exactly one member today.
    """

    reason: Continue


@dataclass(frozen=True, slots=True)
class Terminated:
    """
    The loop's final word, yielded as the last event of a run.

    Upstream returns this from the generator (`AsyncGenerator<..., Terminal>`) and callers
    read it with `yield*`. **Python forbids a value in an async generator's `return`
    (PEP 525)**, so the terminal reason is delivered as a final, distinctly typed event
    instead. `drain` in `app/loop/engine.py` is the stand-in for `yield*`'s return value.
    """

    reason: Terminal
