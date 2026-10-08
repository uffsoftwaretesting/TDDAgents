"""
Skill loader and multi-tier discovery conforming to §4.6 and Part J1.

Ported from:
- `reference/claude-code/src/skills/loadSkillsDir.ts` ->
  `parseSkillFrontmatterFields`, `parseSkillPaths`, `getSkillsPath`
- `reference/claude-code/src/utils/markdownConfigLoader.ts` -> `extractDescriptionFromMarkdown`
- `docs/transition_elaboration_plan.md` -> §4.6 (SKILL.md loader, frontmatter discipline, on-demand references)
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from app.loop.context.instructions import strip_html_comments
from app.loop.skills.definition import SkillDefinition, SkillFrontmatterError

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n(.*)$", re.DOTALL)


def extract_description_from_markdown(markdown_content: str, fallback: str = "Skill") -> str:
    """
    Extract the first non-empty paragraph of text from markdown content,
    ignoring top-level markdown headers (# Header).

    Ported from `extractDescriptionFromMarkdown` in `markdownConfigLoader.ts`.
    """
    lines = markdown_content.splitlines()
    paragraph_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if paragraph_lines:
                break
            continue
        if stripped.startswith("#"):
            if paragraph_lines:
                break
            continue
        paragraph_lines.append(stripped)

    if paragraph_lines:
        return " ".join(paragraph_lines)
    return f"{fallback} instructions"


def parse_skill_paths(paths_raw: Any) -> tuple[str, ...] | None:
    """
    Parse paths frontmatter from a skill.

    Ported from `parseSkillPaths` in `loadSkillsDir.ts`:
    - Normalizes comma-separated strings or lists of patterns.
    - Strips '/**' suffix because wildcard matching treats 'path' as matching
      both the path itself and everything inside it.
    - If all patterns are '**' (match-all), treats as unconditional (None).
    """
    if not paths_raw:
        return None

    raw_patterns: list[str] = []
    if isinstance(paths_raw, str):
        raw_patterns = [p.strip() for p in paths_raw.split(",") if p.strip()]
    elif isinstance(paths_raw, (list, tuple)):
        raw_patterns = [str(p).strip() for p in paths_raw if str(p).strip()]

    normalized: list[str] = []
    for pattern in raw_patterns:
        pat = pattern.rstrip()
        if pat.endswith("/**"):
            pat = pat[:-3]
        if pat:
            normalized.append(pat)

    if not normalized or all(p == "**" for p in normalized):
        return None

    return tuple(normalized)


def parse_skill_frontmatter(content: str, fallback_name: str = "") -> tuple[dict[str, Any], str]:
    """
    Extract and parse YAML frontmatter from SKILL.md content, stripping HTML comments
    from the markdown body.
    """
    content = strip_html_comments(content)
    match = _FRONTMATTER_RE.match(content.lstrip())
    if not match:
        return {}, content.strip()

    frontmatter_raw, body = match.group(1), match.group(2)
    try:
        parsed = yaml.safe_load(frontmatter_raw)
        if not isinstance(parsed, dict):
            return {}, body.strip()
        return parsed, body.strip()
    except Exception as exc:
        logger.warning("Failed to parse YAML frontmatter for skill '%s': %s", fallback_name, exc)
        return {}, body.strip()


def load_skill_references(references_dir: Path) -> dict[str, str]:
    """
    Load on-demand reference markdown files from a skill's references/ subdirectory.
    """
    references: dict[str, str] = {}
    if not references_dir.is_dir():
        return references

    for ref_file in sorted(references_dir.glob("*.md")):
        try:
            references[ref_file.name] = strip_html_comments(ref_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read skill reference file '%s': %s", ref_file, exc)

    return references


def load_skill_from_path(
    path: Path | str,
    *,
    source: str = "built-in",
    expected_name: str | None = None,
) -> SkillDefinition:
    """
    Load a SkillDefinition from a SKILL.md file or directory containing SKILL.md.
    """
    p = Path(path).resolve()
    skill_file: Path
    base_dir: Path

    if p.is_dir():
        skill_file = p / "SKILL.md"
        base_dir = p
        default_name = expected_name or p.name
    else:
        skill_file = p
        base_dir = p.parent
        default_name = expected_name or (p.parent.name if p.name == "SKILL.md" else p.stem)

    if not skill_file.is_file():
        raise FileNotFoundError(f"SKILL.md not found at {skill_file}")

    try:
        content = skill_file.read_text(encoding="utf-8")
    except Exception as exc:
        raise SkillFrontmatterError(f"Cannot read skill file at {skill_file}: {exc}") from exc

    frontmatter, body = parse_skill_frontmatter(content, fallback_name=default_name)

    name = str(frontmatter.get("name") or default_name).strip()
    desc_val = frontmatter.get("description")
    if desc_val:
        description = str(desc_val).strip()
    else:
        description = extract_description_from_markdown(body, fallback=name)

    when_to_use = str(frontmatter.get("when_to_use") or frontmatter.get("whenToUse") or "").strip() or None
    argument_hint = str(frontmatter.get("argument_hint") or frontmatter.get("argumentHint") or "").strip() or None

    # Parse argument names
    arg_names_raw = frontmatter.get("arguments")
    if arg_names_raw is None:
        arg_names_raw = frontmatter.get("argument_names")
    if arg_names_raw is None:
        arg_names_raw = ()

    if isinstance(arg_names_raw, str):
        arg_names = tuple(a.strip() for a in arg_names_raw.split(",") if a.strip())
    elif isinstance(arg_names_raw, (list, tuple)):
        arg_names = tuple(str(a).strip() for a in arg_names_raw if str(a).strip())
    else:
        arg_names = ()

    # Parse allowed tools
    allowed_tools_raw = frontmatter.get("allowed_tools")
    if allowed_tools_raw is None:
        allowed_tools_raw = frontmatter.get("allowed-tools")
    if allowed_tools_raw is None:
        allowed_tools_raw = ()

    if isinstance(allowed_tools_raw, str):
        allowed_tools = tuple(t.strip() for t in allowed_tools_raw.split(",") if t.strip())
    elif isinstance(allowed_tools_raw, (list, tuple)):
        allowed_tools = tuple(str(t).strip() for t in allowed_tools_raw if str(t).strip())
    else:
        allowed_tools = ()

    # User invocable boolean
    user_invocable_raw = frontmatter.get("user_invocable")
    if user_invocable_raw is None:
        user_invocable_raw = frontmatter.get("user-invocable")

    if user_invocable_raw is None:
        user_invocable = True
    elif isinstance(user_invocable_raw, bool):
        user_invocable = user_invocable_raw
    else:
        user_invocable = str(user_invocable_raw).strip().lower() not in ("false", "0", "no")

    context_val = frontmatter.get("context")
    context = "fork" if context_val == "fork" else ("inline" if context_val == "inline" else None)

    agent = str(frontmatter.get("agent") or "").strip() or None
    model = str(frontmatter.get("model") or "").strip() or None
    effort = str(frontmatter.get("effort") or "").strip() or None
    hooks = frontmatter.get("hooks") if isinstance(frontmatter.get("hooks"), dict) else None

    # Paths frontmatter
    paths = parse_skill_paths(frontmatter.get("paths"))

    # References from references/ subdirectory
    references = load_skill_references(base_dir / "references")

    return SkillDefinition(
        name=name,
        description=description,
        body=body,
        when_to_use=when_to_use,
        argument_hint=argument_hint,
        argument_names=arg_names,
        allowed_tools=allowed_tools,
        model=model,
        user_invocable=user_invocable,
        context=context,
        agent=agent,
        paths=paths,
        hooks=hooks,
        effort=effort,
        source=source,
        base_dir=str(base_dir),
        skill_file_path=str(skill_file),
        references=references,
        raw_frontmatter=dict(frontmatter),
    )


class SkillRegistry:
    """
    Registry for holding and querying discovered skills.
    Supports canonical alias resolution (e.g. artifact-diagramming for Skill: Artifact diagramming).
    """

    __slots__ = ("_skills", "_aliases")

    def __init__(self, skills: Mapping[str, SkillDefinition] | None = None) -> None:
        self._skills: dict[str, SkillDefinition] = dict(skills) if skills else {}
        self._aliases: dict[str, str] = {}
        for skill in self._skills.values():
            self._index_aliases(skill)

    def _index_aliases(self, skill: SkillDefinition) -> None:
        raw_name = skill.name
        self._aliases[raw_name.lower()] = skill.name
        norm = raw_name.lower().strip()
        if norm.startswith("skill:"):
            norm = norm[6:].strip()
        slug = re.sub(r"[\s_]+", "-", norm)
        self._aliases[norm] = skill.name
        self._aliases[slug] = skill.name
        if skill.skill_file_path:
            p = Path(skill.skill_file_path)
            stem = p.stem.lower()
            if stem.startswith("skill-"):
                self._aliases[stem[6:]] = skill.name
            self._aliases[stem] = skill.name

    def register(self, skill: SkillDefinition, overwrite: bool = True) -> None:
        if not overwrite and skill.name in self._skills:
            return
        self._skills[skill.name] = skill
        self._index_aliases(skill)

    def get(self, name: str) -> SkillDefinition | None:
        if name in self._skills:
            return self._skills[name]
        norm = name.lower().strip()
        if norm in self._aliases:
            return self._skills.get(self._aliases[norm])
        slug = re.sub(r"[\s_]+", "-", norm)
        if slug in self._aliases:
            return self._skills.get(self._aliases[slug])
        return None

    def list_skills(self) -> tuple[SkillDefinition, ...]:
        return tuple(self._skills.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._skills.keys())

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None

    def __len__(self) -> int:
        return len(self._skills)


def _scan_skills_in_directory(dir_path: Path, source: str) -> list[SkillDefinition]:
    """
    Scan a directory for skills. Supports both directory format (`<name>/SKILL.md`)
    and standalone markdown files (`<name>.md`).
    """
    skills: list[SkillDefinition] = []
    if not dir_path.is_dir():
        return skills

    # First check subdirectories for <name>/SKILL.md
    for entry in sorted(dir_path.iterdir()):
        if entry.is_dir():
            skill_md = entry / "SKILL.md"
            if skill_md.is_file():
                try:
                    skills.append(load_skill_from_path(entry, source=source))
                except Exception as exc:
                    logger.warning("Error loading skill from '%s': %s", skill_md, exc)
        elif entry.is_file() and entry.suffix == ".md" and entry.name != "SKILL.md" and entry.name != "README.md":
            try:
                skills.append(load_skill_from_path(entry, source=source))
            except Exception as exc:
                logger.warning("Error loading skill file '%s': %s", entry, exc)

    return skills


def discover_skills(
    project_root: Path | str = ".",
    user_skills_dir: Path | str | None = None,
    built_in_dir: Path | str | None = None,
) -> SkillRegistry:
    """
    Discover skills following 3-tier precedence (§4.6):
    Project (.tddagents/skills, .claude/skills) > User (~/.tddagents/skills, ~/.claude/skills) > Built-in.
    """
    root = Path(project_root).resolve()
    registry = SkillRegistry()

    # 1. Built-in skills
    if built_in_dir is not None:
        built_in = Path(built_in_dir).resolve()
        if built_in.is_dir():
            for skill in _scan_skills_in_directory(built_in, source="built-in"):
                registry.register(skill, overwrite=True)
    else:
        # Default built-ins: prompts/skills and prompts/skill-prompts
        built_in_prompts = Path(__file__).resolve().parent.parent / "prompts" / "skill-prompts"
        if built_in_prompts.is_dir():
            for skill in _scan_skills_in_directory(built_in_prompts, source="built-in"):
                registry.register(skill, overwrite=True)

        legacy_built_in = Path(__file__).resolve().parent.parent.parent / "prompts" / "skills"
        if legacy_built_in.is_dir():
            for skill in _scan_skills_in_directory(legacy_built_in, source="built-in"):
                registry.register(skill, overwrite=False)

    # 2. User skills (~/.tddagents/skills and ~/.claude/skills)
    user_dirs = [
        Path(user_skills_dir).resolve() if user_skills_dir else Path.home() / ".tddagents" / "skills",
        Path.home() / ".claude" / "skills",
    ]
    for udir in user_dirs:
        if udir.is_dir():
            for skill in _scan_skills_in_directory(udir, source="user"):
                registry.register(skill, overwrite=True)

    # 3. Project skills (.tddagents/skills and .claude/skills) - highest precedence
    project_dirs = [
        root / ".tddagents" / "skills",
        root / ".claude" / "skills",
    ]
    for pdir in project_dirs:
        if pdir.is_dir():
            for skill in _scan_skills_in_directory(pdir, source="project"):
                registry.register(skill, overwrite=True)

    return registry
