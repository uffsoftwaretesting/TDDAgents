"""The source-scan helper sees only original code, so a source-reading test means the same thing
inside mutmut's `mutants/` copy as in the repo (Phase 7 gate: two runs measured nothing)."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.source_scan import calls_to, original_nodes

#: The shape mutmut writes: the trampoline keeps the public name, the source becomes
#: `x_<name>__mutmut_orig`, and every mutant is a numbered copy beside it.
MUTATED = '''
def write(ctx):
    return _mutmut_trampoline(x_write__mutmut_orig, x_write__mutmut_mutants, (ctx,), {})

def x_write__mutmut_orig(ctx):
    ctx.set_app_state(lambda s: replace(s, phase_ledger=good))

def x_write__mutmut_1(ctx):
    ctx.set_app_state(lambda s: replace(s, phase_ledger=None))

class Box:
    def xǁBoxǁput__mutmut_orig(self, ctx):
        ctx.set_app_state(lambda s: replace(s, phase_ledger=good))

    def xǁBoxǁput__mutmut_2(self, ctx):
        ctx.set_app_state(None)
'''

PLAIN = '''
def write(ctx):
    ctx.set_app_state(lambda s: replace(s, phase_ledger=good))

async def nested(ctx):
    def inner():
        ctx.set_app_state(lambda s: replace(s, phase_ledger=good))
    inner()
'''


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "m.py"
    path.write_text(text, encoding="utf-8")
    return path


def test_mutant_copies_are_not_scanned(tmp_path):
    writes = calls_to(original_nodes(_write(tmp_path, MUTATED)), "set_app_state")
    assert writes == [
        "ctx.set_app_state(lambda s: replace(s, phase_ledger=good))",
        "ctx.set_app_state(lambda s: replace(s, phase_ledger=good))",
    ]


def test_unmutated_code_is_scanned_whole(tmp_path):
    writes = calls_to(original_nodes(_write(tmp_path, PLAIN)), "set_app_state")
    assert len(writes) == 2


def test_module_level_statements_are_scanned(tmp_path):
    nodes = original_nodes(_write(tmp_path, "ctx.set_app_state(1)\n"))
    assert calls_to(nodes, "set_app_state") == ["ctx.set_app_state(1)"]
    assert all(isinstance(n, ast.AST) for n in nodes)
