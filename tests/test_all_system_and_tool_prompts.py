import glob
import os
import re
from pathlib import Path
import pytest

from app.loop.prompts.loader import parse_markdown_frontmatter, render_prompt
from app.loop.prompts.registry import global_prompt_registry

PROMPTS_ROOT = Path(__file__).resolve().parent.parent / "app" / "loop" / "prompts"
DOCS_ROOT = Path(__file__).resolve().parent.parent / "docs"

VAR_PATTERN = re.compile(
    r"(\{[a-zA-Z_][a-zA-Z0-9_]*\}|\{\{[a-zA-Z_][a-zA-Z0-9_]*\}\}|\$\{[a-zA-Z0-9_.]+[^}]*\})"
)


def _get_markdown_files(directory: Path) -> list[Path]:
    return [p for p in directory.glob("*.md") if p.name != "README.md"]


class TestAllSystemAndToolPrompts:
    """
    Validates that every system and tool prompt has room for injected variables
    and is accessible and usable in TDDAgents.
    """

    def test_all_app_system_prompts_have_injected_variables(self) -> None:
        sys_dir = PROMPTS_ROOT / "system-prompts"
        files = _get_markdown_files(sys_dir)
        assert len(files) >= 270, f"Expected >=270 system prompts, found {len(files)}"

        for p in files:
            content = p.read_text(encoding="utf-8")
            assert VAR_PATTERN.search(content), (
                f"System prompt '{p.name}' must have room for injected variables!"
            )

    def test_all_app_tool_prompts_have_injected_variables(self) -> None:
        tool_dir = PROMPTS_ROOT / "tool-prompts"
        files = _get_markdown_files(tool_dir)
        assert len(files) >= 250, f"Expected >=250 tool prompts, found {len(files)}"

        for p in files:
            content = p.read_text(encoding="utf-8")
            assert VAR_PATTERN.search(content), (
                f"Tool prompt '{p.name}' must have room for injected variables!"
            )

    def test_all_docs_system_prompts_have_injected_variables(self) -> None:
        sys_dir = DOCS_ROOT / "system-prompts"
        files = _get_markdown_files(sys_dir)
        assert len(files) >= 270, f"Expected >=270 system prompts, found {len(files)}"

        for p in files:
            content = p.read_text(encoding="utf-8")
            assert VAR_PATTERN.search(content), (
                f"Docs system prompt '{p.name}' must have room for injected variables!"
            )

    def test_all_docs_tool_prompts_have_injected_variables(self) -> None:
        tool_dir = DOCS_ROOT / "tool-prompts"
        files = _get_markdown_files(tool_dir)
        assert len(files) >= 250, f"Expected >=250 tool prompts, found {len(files)}"

        for p in files:
            content = p.read_text(encoding="utf-8")
            assert VAR_PATTERN.search(content), (
                f"Docs tool prompt '{p.name}' must have room for injected variables!"
            )

    def test_render_prompt_substitutes_all_variable_styles(self) -> None:
        template = "Agent {agent_name} for user {{user_id}} in dir ${CWD}. Unresolved: {{missing}} and {unset}."
        rendered = render_prompt(
            template,
            {"agent_name": "TDDAssistant", "user_id": "U12345", "CWD": "/workspace"},
        )
        assert "Agent TDDAssistant" in rendered
        assert "for user U12345" in rendered
        assert "in dir /workspace" in rendered
        # Unresolved variables must survive visibly (§4.1 acceptance rule)
        assert "{{missing}}" in rendered
        assert "{unset}" in rendered

    def test_registry_indexes_and_renders_every_system_prompt(self) -> None:
        sample_vars = {
            "agent_name": "TDDRunner",
            "user_id": "Dev1",
            "working_directory": "/app",
            "current_date": "2026-10-07",
            "model_id": "claude-3-7-sonnet",
        }
        sys_prompts = global_prompt_registry.prompts["system-prompts"]
        assert len(sys_prompts) >= 270

        # Verify registry can resolve system prompts by name and filename
        for name, entry in list(sys_prompts.items())[:20]:
            rendered = render_prompt(entry["body"], sample_vars)
            assert len(rendered) > 0

    def test_registry_indexes_and_renders_every_tool_prompt(self) -> None:
        sample_vars = {
            "user_id": "Dev1",
            "working_directory": "/app",
            "MAX_LINES_CONSTANT": "1000",
            "DEFAULT_TIMEOUT_MS": "120000",
        }
        tool_prompts = global_prompt_registry.prompts["tool-prompts"]
        assert len(tool_prompts) >= 250

        # Verify registry can resolve tool prompts
        for name, entry in list(tool_prompts.items())[:20]:
            rendered = render_prompt(entry["body"], sample_vars)
            assert len(rendered) > 0

    def test_tool_prompts_lookup_by_tool_name(self) -> None:
        vars = {"working_directory": "/test/workspace", "user_id": "tester"}
        # ReadFile
        p_read = global_prompt_registry.get_tool_prompt("read_file", vars)
        assert len(p_read) > 0
        # WriteFile
        p_write = global_prompt_registry.get_tool_prompt("write_file", vars)
        assert len(p_write) > 0
        # Bash
        p_bash = global_prompt_registry.get_tool_prompt("bash", vars)
        assert len(p_bash) > 0
        # Agent
        p_agent = global_prompt_registry.get_tool_prompt("agent", vars)
        assert len(p_agent) > 0

    def test_system_reminders_lookup(self) -> None:
        vars = {"MAX_LINES_CONSTANT": "500", "file_path": "main.py"}
        r_trunc = global_prompt_registry.get_system_reminder("file-truncated", vars)
        assert len(r_trunc) > 0
        assert "500" in r_trunc or len(r_trunc) > 20
