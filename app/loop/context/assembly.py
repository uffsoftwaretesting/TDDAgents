from pathlib import Path
from typing import Sequence, Any
import os

from app.loop.prompts.sections import (
    SystemPromptSection,
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    systemPromptSection,
    DANGEROUS_uncachedSystemPromptSection,
)
from app.loop.context.instructions import (
    find_and_load_project_instructions,
    find_and_load_claude_rules,
)
from app.loop.context import AppState

def get_base_prompts_dir() -> Path:
    return Path(__file__).parent.parent.parent / "prompts"

def read_system_prompt_file(name: str) -> str:
    path = get_base_prompts_dir() / "system" / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""

def compute_layer_1_identity() -> str:
    files = [
        "identity.md",
        "system.md",
        "tools.md",
        "actions-with-care.md",
        "doing-tasks.md",
        "tone.md"
    ]
    parts = []
    for f in files:
        content = read_system_prompt_file(f)
        if content:
            parts.append(content)
    return "\n\n".join(parts)

def compute_layer_2_global() -> str:
    path = Path("~/.tddagents/config.md").expanduser()
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""

def compute_layer_3_project(workspace_root: Path) -> str:
    parts = []
    # Project files
    instructions = find_and_load_project_instructions(
        workspace_root, 
        candidates=("CLAUDE.md", "CONVENTIONS.md", "TDDAgents.md", ".cursorrules")
    )
    for name, f in instructions.items():
        parts.append(f"# {name}\n{f.content}")
        
    # Rules
    rules = find_and_load_claude_rules(workspace_root)
    for r in rules:
        parts.append(f"# Rule: {Path(r.path).name}\n{r.content}")
        
    return "\n\n".join(parts)

def compute_layer_4_auto_memory(workspace_root: Path) -> str:
    path = workspace_root / "MEMORY.md"
    if path.exists():
        return f"# Auto-Memory\n{path.read_text(encoding='utf-8')}"
    return ""

def compute_layer_5_scratchpad(workspace_root: Path, phase_ledger: PhaseLedger) -> str:
    parts = []
    todo_path = workspace_root / "TODO.md"
    if todo_path.exists():
        parts.append(f"# TODO Scratchpad\n{todo_path.read_text(encoding='utf-8')}")
        
    if phase_ledger:
        ledger = phase_ledger
        parts.append(
            f"# TDD Ledger\n"
            f"Current Phase: {ledger.phase.value}\n"
            f"Red Confirmed: {ledger.red_confirmed}\n"
            f"Green Passed: {ledger.green_passed}"
        )
    return "\n\n".join(parts)

def assemble_6_layer_prompt(workspace_root: str | Path, phase_ledger: PhaseLedger) -> list[SystemPromptSection]:
    """
    Assemble the 6-Layer Memory Architecture prompt sections.
    Layers 1-4 are static and go before the dynamic boundary.
    Layer 5 is dynamic (depends on changing ledger/TODO) and goes after.
    Layer 6 is the message history (handled externally by loop engine).
    """
    root = Path(workspace_root)
    
    sections = [
        systemPromptSection("Layer1_BaseIdentity", compute_layer_1_identity),
        systemPromptSection("Layer2_GlobalInstructions", compute_layer_2_global),
        systemPromptSection("Layer3_ProjectInstructions", lambda: compute_layer_3_project(root)),
        systemPromptSection("Layer4_AutoMemory", lambda: compute_layer_4_auto_memory(root)),
        
        # Boundary: Everything above can be globally cached for the session
        SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
        
        # Volatile section recomputed every turn
        DANGEROUS_uncachedSystemPromptSection(
            "Layer5_TaskScratchpad",
            lambda: compute_layer_5_scratchpad(root, phase_ledger),
            reason="TDD phase ledger and TODO.md change frequently and must be read fresh."
        )
    ]
    return sections
