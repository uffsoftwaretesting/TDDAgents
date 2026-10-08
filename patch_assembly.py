with open('app/loop/context/assembly.py', 'r') as f:
    text = f.read()

replacement = """def _compute_registry():
        vars = {"agent_name": "TDDAgent", "working_directory": str(getattr(ws, "root", "."))}
        sys_prompts = global_prompt_registry.get_system_context(vars)
        skills = "\\n".join(global_prompt_registry.get_skills_catalog(vars))
        return f"{sys_prompts}\\n\\n### Available Extended Skills:\\n{skills}"
"""
text = text.replace('def _compute_registry():\n        vars = {"agent_name": "TDDAgent", "working_directory": str(getattr(ws, "root", "."))}\n        return global_prompt_registry.get_system_context(vars)', replacement)

with open('app/loop/context/assembly.py', 'w') as f:
    f.write(text)
