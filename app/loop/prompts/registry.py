from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from app.loop.prompts.loader import parse_markdown_frontmatter, render_prompt

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent
DOCS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "docs"


class PromptRegistry:
    """
    Dynamically loads and indexes all 800+ markdown prompt files.

    Every prompt file is indexed across categories:
    - system-prompts (277 files: core personas, safety rules, environment directives, reminders)
    - tool-prompts (252 files: tool descriptions, usage notes, security constraints)
    - data-prompts (129 files)
    - agent-prompts (81 files)
    - skill-prompts (71 files)
    - decode-claude-code-analysis-en (18 files)

    Every prompt supports dynamic variable injection via {{var}}, ${var}, or {var}.
    """

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = base_dir or PROMPTS_DIR
        self.prompts: Dict[str, Dict[str, dict]] = {
            "system-prompts": {},
            "tool-prompts": {},
            "data-prompts": {},
            "agent-prompts": {},
            "skill-prompts": {},
            "decode-claude-code-analysis-en": {},
        }
        self._normalized_index: Dict[str, Dict[str, str]] = {
            cat: {} for cat in self.prompts
        }
        self.load_all()

    @staticmethod
    def _normalize_key(key: str) -> str:
        """Normalize key for resilient, case-insensitive, prefix-agnostic lookup."""
        k = key.lower().strip()
        if k.endswith(".md"):
            k = k[:-3]
        for prefix in ("tool-description-", "system-prompt-", "system-reminder-", "agent-prompt-", "skill-prompt-"):
            if k.startswith(prefix):
                k = k[len(prefix):]
        k = re.sub(r"[-_ ]+", "", k)
        return k

    def load_all(self) -> None:
        """Load all prompts from PROMPTS_DIR and DOCS_DIR."""
        search_dirs = [self.base_dir]
        if DOCS_DIR.exists() and DOCS_DIR != self.base_dir:
            search_dirs.append(DOCS_DIR)

        for category in self.prompts:
            for s_dir in search_dirs:
                category_dir = s_dir / category
                if not category_dir.exists():
                    continue
                for md_file in category_dir.glob("*.md"):
                    if md_file.name == "README.md":
                        continue
                    try:
                        content = md_file.read_text(encoding="utf-8")
                        fm, body = parse_markdown_frontmatter(content)
                        name = fm.get("name", md_file.stem)
                        entry = {
                            "name": name,
                            "filename": md_file.name,
                            "frontmatter": fm,
                            "body": body,
                            "filepath": str(md_file),
                        }
                        # Primary index by name and filename
                        self.prompts[category][name] = entry
                        self.prompts[category][md_file.name] = entry
                        self.prompts[category][md_file.stem] = entry

                        # Normalized index
                        norm_name = self._normalize_key(name)
                        norm_file = self._normalize_key(md_file.stem)
                        self._normalized_index[category][norm_name] = name
                        self._normalized_index[category][norm_file] = name
                    except Exception as e:
                        logger.warning("Failed to load prompt %s: %s", md_file, e)

    def find_prompt(
        self, category: str, key: str
    ) -> Optional[dict]:
        """Lookup prompt entry by exact name, filename, normalized key, or substring."""
        cat_prompts = self.prompts.get(category, {})
        if not cat_prompts:
            return None

        # 1. Exact match
        if key in cat_prompts:
            return cat_prompts[key]

        # Common aliases
        aliases = {
            "write_file": "write",
            "writefile": "write",
            "file_write": "write",
            "filewrite": "write",
            "read_file": "readfile",
            "readfile": "readfile",
            "file_read": "readfile",
            "fileread": "readfile",
            "file_edit": "edit",
            "edit_file": "edit",
        }
        alias_target = aliases.get(key.lower().strip())
        if alias_target and alias_target != key.lower().strip():
            target_entry = self.find_prompt(category, alias_target)
            if target_entry:
                return target_entry

        # 2. Normalized match
        norm = self._normalize_key(key)
        norm_map = self._normalized_index.get(category, {})
        if norm in norm_map:
            canonical_name = norm_map[norm]
            return cat_prompts.get(canonical_name)

        # 3. Substring match
        lower_key = key.lower()
        for name, entry in cat_prompts.items():
            if lower_key in name.lower() or lower_key in entry["filename"].lower():
                return entry

        return None

    def get_system_prompt(
        self, name: str, vars: Optional[Mapping[str, Any]] = None
    ) -> str:
        """Fetch and render a specific system prompt with injected variables."""
        entry = self.find_prompt("system-prompts", name)
        if entry:
            return render_prompt(entry["body"], vars)
        return ""

    def get_system_reminder(
        self, name: str, vars: Optional[Mapping[str, Any]] = None
    ) -> str:
        """Fetch and render a dynamic system reminder prompt with injected variables."""
        search_key = name if "reminder" in name else f"system-reminder-{name}"
        entry = self.find_prompt("system-prompts", search_key)
        if entry:
            return render_prompt(entry["body"], vars)
        return ""

    def get_tool_prompt(
        self, tool_name: str, vars: Optional[Mapping[str, Any]] = None
    ) -> str:
        """Fetch and render tool prompt/description with injected variables."""
        entry = self.find_prompt("tool-prompts", tool_name)
        if entry:
            return render_prompt(entry["body"], vars)
        return ""

    def get_tool_description(
        self, tool_name: str, vars: Optional[Mapping[str, Any]] = None
    ) -> str:
        """Backward-compatible alias for get_tool_prompt."""
        return self.get_tool_prompt(tool_name, vars)

    def get_agent_prompt(
        self, agent_name: str, vars: Optional[Mapping[str, Any]] = None
    ) -> str:
        """Fetch and render an agent definition prompt with injected variables."""
        entry = self.find_prompt("agent-prompts", agent_name)
        if entry:
            return render_prompt(entry["body"], vars)
        return ""

    def get_skill_prompt(
        self, skill_name: str, vars: Optional[Mapping[str, Any]] = None
    ) -> str:
        """Fetch and render a skill prompt with injected variables."""
        entry = self.find_prompt("skill-prompts", skill_name)
        if entry:
            return render_prompt(entry["body"], vars)
        return ""

    def get_system_context(
        self, vars: Optional[Mapping[str, Any]] = None
    ) -> str:
        """Construct unified system prompt context by rendering core system prompts."""
        compiled: list[str] = []
        seen_files: set[str] = set()
        for name, data in self.prompts["system-prompts"].items():
            fpath = data.get("filepath", "")
            if fpath in seen_files:
                continue
            seen_files.add(fpath)
            rendered = render_prompt(data["body"], vars).strip()
            if rendered:
                compiled.append(rendered)
        return "\n\n---\n\n".join(compiled)

    def get_skills_catalog(
        self, vars: Optional[Mapping[str, Any]] = None
    ) -> List[str]:
        """Provides skill documentation with dynamic variables injected."""
        skills: list[str] = []
        seen_files: set[str] = set()
        for name, data in self.prompts["skill-prompts"].items():
            fpath = data.get("filepath", "")
            if fpath in seen_files:
                continue
            seen_files.add(fpath)
            skills.append(f"Skill: {name}\n{render_prompt(data['body'], vars)}")
        return skills

    def list_prompts(self, category: str) -> List[str]:
        """List all unique prompt names in a category."""
        cat_prompts = self.prompts.get(category, {})
        unique_names: set[str] = set()
        for entry in cat_prompts.values():
            unique_names.add(entry["name"])
        return sorted(unique_names)

    def count_prompts(self, category: Optional[str] = None) -> int:
        """Return count of unique prompt files in category or overall."""
        if category:
            cat_prompts = self.prompts.get(category, {})
            seen = {e["filepath"] for e in cat_prompts.values()}
            return len(seen)
        total = 0
        for cat in self.prompts:
            total += self.count_prompts(cat)
        return total


global_prompt_registry = PromptRegistry()
