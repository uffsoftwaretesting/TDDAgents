"""
`canUseTool` for an interactive session: a permission 'ask' becomes a question to the user
(Phase 11).

Ported from `reference/claude-code/src/hooks/useCanUseTool.tsx`,
`hooks/toolPermission/handlers/interactiveHandler.ts` (`handleInteractivePermission`) and
`hooks/toolPermission/PermissionContext.ts` (`handleUserAllow`, `cancelAndAbort`,
`persistPermissions`). The gate (`has_permissions_to_use_tool`) has already decided by the
time this runs, so the TDD phase tier — which denies, never asks — is out of its reach: no
answer here can open a phase-forbidden write.

1. PermissionRequest hooks have the first word, as upstream's race against the dialog does.
2. Otherwise the user is asked: the tool's dialog (`components/permissions/permission_request.py`,
   upstream's `PermissionRequest`) and its options.
3. "Yes" allows; a "don't ask again" / "during this session" choice also persists its
   permission updates and applies them to the run (re-stripped of dangerous allows).
4. "No" rejects with upstream's `REJECT_MESSAGE`; without feedback the turn is aborted, so
   control returns to the user; with feedback the model hears it and goes on.

`AskUserQuestion` has its own question, as upstream's `PermissionRequest` routes it to
`AskUserQuestionPermissionRequest`: the user answers each question (`ask_questions`) and the
call is allowed with the answers in its input, and the preview of each option picked as its
question's `annotations` (`selected_preview_annotations`, upstream's submit handler; the REPL has no
notes field); declining aborts the turn (`onReject`).

`ExitPlanMode` has its own too, as upstream's `ExitPlanModePermissionRequest`: the plan is shown
with upstream's choices; a "Yes" sets the mode the work goes on in (`onAllow` with a `setMode`
update, through `set_permission_mode` so the plan-mode flags move as upstream's do); "No, keep
planning" with feedback (any text typed instead of an option) sends it to the model, which revises the
plan (`onReject(trimmedFeedback)`, `REJECT_MESSAGE_WITH_REASON_PREFIX`); a "No" without feedback is a
no-op, as upstream's `handleResponse` returns on it: the question is asked again. Esc (an answer
`cancelled`) rejects and stops the turn (`handleCancel`).

Divergences: the hooks run before the question, not in a race with it; an accept-with-feedback
input, the classifier pre-check and the bridge/channel relays are not ported; the plan cannot be edited
in an external editor (upstream's ctrl-g, `editFileInEditor`; user decision 2026-11-17).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.components.permissions.permission_dialog import PermissionDialog
from app.components.permissions.permission_options import (
    EXIT_PLAN_MODE_TOOL_NAME,
    PermissionOption,
    permission_options,
)
from app.components.permissions.permission_request import permission_dialog
from app.tdd.tdd_plan_tool.tdd_plan_tool import UPDATE_PLAN_TOOL_NAME
from app.tools.ask_user_question_tool.ask_user_question_tool import (
    ASK_USER_QUESTION_TOOL_NAME,
)
from app.types.permissions import PermissionBehavior, PermissionResult
from app.utils.messages import REJECT_MESSAGE, REJECT_MESSAGE_WITH_REASON_PREFIX
from app.utils.permissions.filesystem import permission_cwd
from app.utils.permissions.permission_setup import set_permission_mode
from app.utils.permissions.permissions import (
    extract_permission_context,
    persist_permissions,
    run_permission_request_hooks_for_headless_agent,
)


@dataclass(frozen=True, slots=True)
class PermissionPrompt:
    """What the user is asked: which tool, doing what, why, and the choices."""

    tool_name: str
    description: str
    message: str
    input: dict[str, Any]
    options: tuple[PermissionOption, ...] = field(default_factory=tuple)
    #: The agent asking, when it is not the main thread (a `bubble` agent's prompt).
    agent: str | None = None
    #: The tool's dialog (`components/permissions/`), as upstream's `PermissionRequest` draws it.
    #: `None` for the flows with their own text (the plan approval, a plan change).
    dialog: PermissionDialog | None = None


@dataclass(frozen=True, slots=True)
class PermissionAnswer:
    """The user's choice (an option's `value`) and what they said with it."""

    value: str
    feedback: str | None = None
    #: Esc: the question was dismissed rather than answered (upstream's `handleCancel`).
    cancelled: bool = False


AskUser = Callable[[PermissionPrompt], Awaitable[PermissionAnswer]]

#: The line the plan approval adds after a "No" without feedback, which upstream ignores.
PLAN_FEEDBACK_NEEDED = "Tell the Analyst what to change (type it after 3, or just type it), or Esc to cancel."


def selected_preview_annotations(
    questions: Sequence[Mapping[str, Any]], answers: Mapping[str, str]
) -> dict[str, dict[str, str]]:
    """
    `AskUserQuestionPermissionRequest`'s submit handler: for each answered question, the `preview`
    of the option whose label is the answer, as that question's annotation.
    """
    annotations: dict[str, dict[str, str]] = {}
    for question in questions:
        text = str(question.get("question", ""))
        answer = answers.get(text)
        selected = next((o for o in question.get("options") or () if answer and o.get("label") == answer), None)
        if selected is not None and selected.get("preview"):
            annotations[text] = {"preview": str(selected["preview"])}
    return annotations


#: `AskUserQuestion`'s questions in, the answers out (question text -> answer); `None` declines.
AskQuestions = Callable[[tuple[dict[str, Any], ...]], Awaitable[dict[str, str] | None]]
#: `CanUseToolFn`: the gate's 'ask', resolved — (tool, input, ask, context, tool_use_id) -> decision.
CanUseTool = Callable[[Any, dict[str, Any], PermissionResult, Any, str], Coroutine[Any, Any, PermissionResult]]


def rejection(feedback: str | None) -> PermissionResult:
    """`cancelAndAbort`'s message: upstream answers with an 'ask' carrying the rejection."""
    message = f"{REJECT_MESSAGE_WITH_REASON_PREFIX}{feedback}" if feedback else REJECT_MESSAGE
    return PermissionResult(behavior=PermissionBehavior.ASK, message=message)


def _chosen(options: Sequence[PermissionOption], value: str) -> PermissionOption:
    """The option the answer names; an answer naming none is a "No"."""
    return next((o for o in options if o.value == value), PermissionOption(value, value, reject=True))


def plan_approval_message(plan: str, notice: str = "") -> str:
    """The plan as the approval shows it (upstream's "Ready to code?" dialog)."""
    lines = ["Here is the plan:", "", plan.rstrip(), "", "Would you like to proceed?"]
    if notice:
        lines.append(notice)
    return "\n".join(lines)


async def approve_plan(tool: Any, input_args: dict[str, Any], context: Any, ask_user: AskUser) -> PermissionResult:
    """`ExitPlanModePermissionRequest`: the plan and upstream's choices."""
    perm = extract_permission_context(context)
    options = permission_options(tool.name, input_args, None, perm, permission_cwd(context))
    plans = getattr(context, "session_plans", None)
    plan = plans.read(getattr(context, "agent_id", None)) if plans is not None else None
    notice = ""
    while True:
        answer = await ask_user(PermissionPrompt(
            tool_name=tool.name,
            description=tool.description(input_args),
            message=plan_approval_message(plan or "", notice),
            input=input_args,
            options=tuple(options),
            agent=getattr(context, "agent_type", None),
        ))
        if answer.cancelled:
            context.cancel.cancel()
            return rejection(None)
        choice = _chosen(options, answer.value)
        feedback = (answer.feedback or "").strip() or None
        if choice.reject:
            if feedback is None:
                # `handleResponse`: `if (!trimmedFeedback && !hasImages) return` — the dialog stays.
                notice = PLAN_FEEDBACK_NEEDED
                continue
            return rejection(feedback)
        mode = next((u.mode for u in choice.updates if u.type == "setMode" and u.mode is not None), None)
        if mode is not None:
            context.set_app_state(lambda state, to=mode: set_permission_mode(state, to))
        return PermissionResult(behavior=PermissionBehavior.ALLOW, updated_input=input_args,
                                decision_reason={"type": "user", "option": choice.value})


def build_interactive_can_use_tool(
    ask_user: AskUser, ask_questions: AskQuestions | None = None
) -> CanUseTool:
    """The REPL's `canUseTool`, asking through `ask_user` (`ask_questions` for `AskUserQuestion`)."""

    async def can_use_tool(
        tool: Any, input_args: dict[str, Any], result: PermissionResult, context: Any, tool_use_id: str
    ) -> PermissionResult:
        if result.behavior != PermissionBehavior.ASK:
            return result
        perm = extract_permission_context(context)
        hooked = await run_permission_request_hooks_for_headless_agent(
            tool, input_args, tool_use_id, context, perm.mode
        )
        if hooked is not None:
            return hooked
        if tool.name == EXIT_PLAN_MODE_TOOL_NAME:
            return await approve_plan(tool, input_args, context, ask_user)
        if tool.name == ASK_USER_QUESTION_TOOL_NAME and ask_questions is not None:
            answers = await ask_questions(tuple(input_args.get("questions") or ()))
            if answers is None:
                context.cancel.cancel()
                return rejection(None)
            annotations = selected_preview_annotations(input_args.get("questions") or (), answers)
            return PermissionResult(behavior=PermissionBehavior.ALLOW,
                                    updated_input={**input_args, "answers": answers,
                                                   **({"annotations": annotations} if annotations else {})},
                                    decision_reason={"type": "user", "option": "answered"})
        options = permission_options(tool.name, input_args, result.suggestions, perm, permission_cwd(context))
        # A plan change shows its before/after diff (its message), as ExitPlanMode shows its plan.
        dialog = None if tool.name == UPDATE_PLAN_TOOL_NAME else permission_dialog(tool, input_args, result, context)
        answer = await ask_user(PermissionPrompt(
            tool_name=tool.name,
            description=tool.description(input_args),
            message=result.message,
            input=input_args,
            options=tuple(options),
            agent=getattr(context, "agent_type", None),
            dialog=dialog,
        ))
        choice = _chosen(options, answer.value)
        if choice.reject:
            feedback = (answer.feedback or "").strip() or None
            if feedback is None:
                # `cancelAndAbort`: a plain "No" stops the turn and hands control back.
                context.cancel.cancel()
            return rejection(feedback)
        persist_permissions(choice.updates, context)
        return PermissionResult(behavior=PermissionBehavior.ALLOW, updated_input=input_args,
                                decision_reason={"type": "user", "option": choice.value})

    return can_use_tool
