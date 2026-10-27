"""
The Agent tool's prompt (`reference/claude-code/src/tools/AgentTool/prompt.ts` -> `getPrompt`,
`formatAgentLine`, `getToolsDescription`).

Rendered as the external, headless build has it:

* **Fork off.** `isForkSubagentEnabled()` is false in a non-interactive session, so the
  "When to fork" section and the fork examples never appear.
* **Agent list inline.** `shouldInjectAgentListInMessages()` defaults to the GrowthBook gate
  `tengu_agent_list_attach`, off in this build.
* **Concurrency note on.** It is withheld only for a `pro` subscription, which an API-key run
  does not have.
* **Background notes** only when background tasks exist (`background_tasks_enabled`); they
  arrive in Phase 8, which is upstream's `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` path today.
* **Not ported:** the worktree-isolation line (no `isolation` parameter here) and the
  teammate lines (no teams).

`default_agent_type` is the one parameter upstream does not have. Upstream falls back to
`general-purpose` when `subagent_type` is omitted; when a caller requires `subagent_type`
(`None`), the sentence about the fallback is dropped rather than promising an agent that
does not exist.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.tools.agent_tool.load_agents_dir import AgentDefinition

AGENT_TOOL_NAME = "Agent"
FILE_READ_TOOL_NAME = "ReadFile"
FILE_WRITE_TOOL_NAME = "WriteFile"
GLOB_TOOL_NAME = "Glob"
SEND_MESSAGE_TOOL_NAME = "SendMessage"

#: `GENERAL_PURPOSE_AGENT.agentType`.
GENERAL_PURPOSE_AGENT_TYPE = "general-purpose"


def get_tools_description(agent: AgentDefinition) -> str:
    """`getToolsDescription`."""
    tools, disallowed = agent.tools, agent.disallowed_tools
    if tools and disallowed:
        deny = set(disallowed)
        effective = [t for t in tools if t not in deny]
        return ", ".join(effective) if effective else "None"
    if tools:
        return ", ".join(tools)
    if disallowed:
        return f"All tools except {', '.join(disallowed)}"
    return "All tools"


def format_agent_line(agent: AgentDefinition) -> str:
    """`formatAgentLine`: `- type: whenToUse (Tools: ...)`."""
    return f"- {agent.name}: {agent.description} (Tools: {get_tools_description(agent)})"


_WRITING_THE_PROMPT = """

## Writing the prompt

Brief the agent like a smart colleague who just walked into the room — it hasn't seen this conversation, \
doesn't know what you've tried, doesn't understand why this task matters.
- Explain what you're trying to accomplish and why.
- Describe what you've already learned or ruled out.
- Give enough context about the surrounding problem that the agent can make judgment calls rather than just \
following a narrow instruction.
- If you need a short response, say so ("report in under 200 words").
- Lookups: hand over the exact command. Investigations: hand over the question — prescribed steps become dead \
weight when the premise is wrong.

Terse command-style prompts produce shallow, generic work.

**Never delegate understanding.** Don't write "based on your findings, fix the bug" or "based on the research, \
implement it." Those phrases push synthesis onto the agent instead of doing it yourself. Write prompts that prove \
you understood: include file paths, line numbers, what specifically to change.
"""

_EXAMPLES = f"""Example usage:

<example_agent_descriptions>
"test-runner": use this agent after you are done writing code to run tests
"greeting-responder": use this agent to respond to user greetings with a friendly joke
</example_agent_descriptions>

<example>
user: "Please write a function that checks if a number is prime"
assistant: I'm going to use the {FILE_WRITE_TOOL_NAME} tool to write the following code:
<code>
function isPrime(n) {{
  if (n <= 1) return false
  for (let i = 2; i * i <= n; i++) {{
    if (n % i === 0) return false
  }}
  return true
}}
</code>
<commentary>
Since a significant piece of code was written and the task was completed, now use the test-runner agent to run \
the tests
</commentary>
assistant: Uses the {AGENT_TOOL_NAME} tool to launch the test-runner agent
</example>

<example>
user: "Hello"
<commentary>
Since the user is greeting, use the greeting-responder agent to respond with a friendly joke
</commentary>
assistant: "I'm going to use the {AGENT_TOOL_NAME} tool to launch the greeting-responder agent"
</example>
"""


def get_prompt(
    agent_definitions: Sequence[AgentDefinition],
    is_coordinator: bool = False,
    allowed_agent_types: Sequence[str] | None = None,
    *,
    background_tasks_enabled: bool = False,
    default_agent_type: str | None = GENERAL_PURPOSE_AGENT_TYPE,
) -> str:
    """`getPrompt`: the slim shared core for a coordinator, the full prompt otherwise."""
    effective = (
        [a for a in agent_definitions if a.name in allowed_agent_types]
        if allowed_agent_types is not None
        else list(agent_definitions)
    )
    agent_list = "Available agent types and the tools they have access to:\n" + "\n".join(
        format_agent_line(a) for a in effective
    )
    selection = (
        f"When using the {AGENT_TOOL_NAME} tool, specify a subagent_type parameter to select which agent type "
        "to use."
    )
    if default_agent_type is not None:
        selection += f" If omitted, the {default_agent_type} agent is used."
    shared = (
        "Launch a new agent to handle complex, multi-step tasks autonomously.\n\n"
        f"The {AGENT_TOOL_NAME} tool launches specialized agents (subprocesses) that autonomously handle complex "
        "tasks. Each agent type has specific capabilities and tools available to it.\n\n"
        f"{agent_list}\n\n{selection}"
    )
    if is_coordinator:
        return shared

    when_not_to_use = f"""
When NOT to use the {AGENT_TOOL_NAME} tool:
- If you want to read a specific file path, use the {FILE_READ_TOOL_NAME} tool or the {GLOB_TOOL_NAME} tool \
instead of the {AGENT_TOOL_NAME} tool, to find the match more quickly
- If you are searching for a specific class definition like "class Foo", use the {GLOB_TOOL_NAME} tool instead, \
to find the match more quickly
- If you are searching for code within a specific file or set of 2-3 files, use the {FILE_READ_TOOL_NAME} tool \
instead of the {AGENT_TOOL_NAME} tool, to find the match more quickly
- Other tasks that are not related to the agent descriptions above
"""
    concurrency = (
        "\n- Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a "
        "single message with multiple tool uses"
    )
    background = (
        "\n- You can optionally run agents in the background using the run_in_background parameter. When an "
        "agent runs in the background, you will be automatically notified when it completes — do NOT sleep, poll, "
        "or proactively check on its progress. Continue with other work or respond to the user instead.\n"
        "- **Foreground vs background**: Use foreground (default) when you need the agent's results before you "
        "can proceed — e.g., research agents whose findings inform your next steps. Use background when you have "
        "genuinely independent work to do in parallel."
        if background_tasks_enabled
        else ""
    )
    usage = (
        "Usage notes:\n"
        f"- Always include a short description (3-5 words) summarizing what the agent will do{concurrency}\n"
        "- When the agent is done, it will return a single message back to you. The result returned by the agent "
        "is not visible to the user. To show the user the result, you should send a text message back to the user "
        f"with a concise summary of the result.{background}\n"
        f"- To continue a previously spawned agent, use {SEND_MESSAGE_TOOL_NAME} with the agent's ID or name as "
        "the `to` field. The agent resumes with its full context preserved. Each Agent invocation starts fresh — "
        "provide a complete task description.\n"
        "- The agent's outputs should generally be trusted\n"
        "- Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, "
        "web fetches, etc.), since it is not aware of the user's intent\n"
        "- If the agent description mentions that it should be used proactively, then you should try your best to "
        "use it without the user having to ask for it first. Use your judgement.\n"
        "- If the user specifies that they want you to run agents \"in parallel\", you MUST send a single message "
        f"with multiple {AGENT_TOOL_NAME} tool use content blocks. For example, if you need to launch both a "
        "build-validator agent and a test-runner agent in parallel, send a single message with both tool calls."
    )
    return f"{shared}\n{when_not_to_use}\n\n{usage}{_WRITING_THE_PROMPT}\n\n{_EXAMPLES}"


__all__ = [
    "GENERAL_PURPOSE_AGENT_TYPE",
    "format_agent_line",
    "get_prompt",
    "get_tools_description",
]
