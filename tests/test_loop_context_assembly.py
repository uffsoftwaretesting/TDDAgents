import pytest
from pathlib import Path
from unittest.mock import patch
from app.loop.context.assembly import (
    assemble_6_layer_prompt,
    compute_layer_1_identity,
    compute_layer_2_global,
    compute_layer_3_project,
    compute_layer_4_auto_memory,
    compute_layer_5_scratchpad,
)
from app.loop.ledger import PhaseLedger, TddPhase

def test_compute_layer_1_identity(tmp_path):
    # Create the files it expects
    system_dir = tmp_path / "system"
    system_dir.mkdir()
    files_to_mock = [
        "identity.md",
        "system.md",
        "tools.md",
        "actions-with-care.md",
        "doing-tasks.md",
        "tone.md"
    ]
    for f in files_to_mock:
        (system_dir / f).write_text(f"content of {f}", encoding="utf-8")
    
    with patch("app.loop.context.assembly.get_base_prompts_dir", return_value=tmp_path):
        result = compute_layer_1_identity()
        for f in files_to_mock:
            assert f"content of {f}" in result
            
        # Test when one file is missing
        (system_dir / "identity.md").unlink()
        result_missing = compute_layer_1_identity()
        assert "content of identity.md" not in result_missing
        assert "content of system.md" in result_missing

def test_compute_layer_2_global(tmp_path, monkeypatch):
    config_path = tmp_path / "config.md"
    config_path.write_text("global config", encoding="utf-8")
    
    # Mock expanduser
    with patch.object(Path, "expanduser", return_value=config_path):
        assert compute_layer_2_global() == "global config"
        
    config_path.unlink()
    with patch.object(Path, "expanduser", return_value=config_path):
        assert compute_layer_2_global() == ""

def test_compute_layer_3_project(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("Claude rules", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "rules").mkdir()
    (tmp_path / ".claude" / "rules" / "rule1.md").write_text("rule 1 content", encoding="utf-8")
    
    result = compute_layer_3_project(tmp_path)
    assert "Claude rules" in result
    assert "rule 1 content" in result
    
    # Empty
    empty_path = tmp_path / "empty"
    empty_path.mkdir()
    assert compute_layer_3_project(empty_path) == ""

def test_compute_layer_4_auto_memory(tmp_path):
    assert compute_layer_4_auto_memory(tmp_path) == ""
    
    (tmp_path / "MEMORY.md").write_text("memory rules", encoding="utf-8")
    assert "memory rules" in compute_layer_4_auto_memory(tmp_path)

def test_compute_layer_5_scratchpad(tmp_path):
    ledger = PhaseLedger(phase=TddPhase.RED, red_confirmed=True, green_passed=False)
    
    # No TODO
    result = compute_layer_5_scratchpad(tmp_path, ledger)
    assert "TODO Scratchpad" not in result
    assert "Current Phase: RED" in result
    assert "Red Confirmed: True" in result
    
    # With TODO
    (tmp_path / "TODO.md").write_text("todo item", encoding="utf-8")
    result = compute_layer_5_scratchpad(tmp_path, ledger)
    assert "TODO Scratchpad" in result
    assert "todo item" in result

def test_assemble_6_layer_prompt(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("Claude rules", encoding="utf-8")
    (tmp_path / "TODO.md").write_text("- Task 1", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("Memory content", encoding="utf-8")
    
    phase_ledger = PhaseLedger(phase=TddPhase.RED)
    
    with patch("app.loop.context.assembly.get_base_prompts_dir", return_value=tmp_path):
        sections = assemble_6_layer_prompt(tmp_path, phase_ledger)
        assert len(sections) == 6 # 5 layers + boundary
        
        from app.loop.prompts.sections import split_system_prompt_sections, resolve_system_prompt_sections
        static, dynamic = split_system_prompt_sections(sections)
        assert len(static) == 4
        assert len(dynamic) == 1
        
        static_resolved = resolve_system_prompt_sections(static)
        dynamic_resolved = resolve_system_prompt_sections(dynamic)
        
        # Layer 3 should have Claude rules
        assert any("Claude rules" in s for s in static_resolved)
        # Layer 4 should have Memory content
        assert any("Memory content" in s for s in static_resolved)
        # Layer 5 should have Task 1 and RED phase
        assert any("Task 1" in s and "RED" in s for s in dynamic_resolved)

