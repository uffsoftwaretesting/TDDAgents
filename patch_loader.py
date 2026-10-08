import re

with open('app/loop/agents/loader.py', 'r') as f:
    content = f.read()

replacement = """def get_agent_definitions_with_overrides(
    project_dir: Path | str | None = None,
    user_home: Path | str | None = None,
    built_in_dir: Path | str | None = None,
    vars: Mapping[str, Any] | None = None,
) -> dict[str, AgentDefinition]:
    from app.loop.prompts.registry import global_prompt_registry
    
    definitions: dict[str, AgentDefinition] = {}

    # 1. Built-in (from PromptRegistry)
    for name, data in global_prompt_registry.prompts["agent-prompts"].items():
        try:
            # We construct a mock content and parse it via load_agent_definition
            raw = f"---\nname: {name}\ndescription: {data['frontmatter'].get('description', '')}\n---\n{data['body']}"
            ag = load_agent_definition(raw, vars)
            definitions[name] = ag
        except Exception as e:
            logger.warning(f"Failed to load agent {name} from registry: {e}")

    # 2. User
    if user_home is not None:
"""

content = re.sub(
    r"def get_agent_definitions_with_overrides\(.*?\n    # 2\. User\n    if user_home is not None:",
    replacement,
    content,
    flags=re.DOTALL
)

with open('app/loop/agents/loader.py', 'w') as f:
    f.write(content)

