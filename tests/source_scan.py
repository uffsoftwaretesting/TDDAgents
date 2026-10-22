"""
Scanning `app/` source from a test, the same way inside and outside mutmut.

mutmut rewrites every mutated module: the public function becomes a trampoline, the original
body moves to `x_<name>__mutmut_orig` (`xǁ<Class>ǁ<name>__mutmut_orig` for a method), and each
mutant is a numbered copy beside it. A test that reads source therefore sees every mutant
under mutmut — a copy that writes `None`, drops an argument — and either fails (stats
collection dies and nothing is measured) or passes for the wrong reason. Scan through
`original_nodes` instead: it keeps unmutated code and the `__mutmut_orig` copies, and drops
the numbered mutants.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _is_mutant_copy(name: str) -> bool:
    return "__mutmut_" in name and not name.endswith("__mutmut_orig")


def _original_statements(body: Iterable[ast.stmt]) -> list[ast.AST]:
    nodes: list[ast.AST] = []
    for stmt in body:
        if isinstance(stmt, _FUNCTIONS):
            if not _is_mutant_copy(stmt.name):
                nodes.extend(ast.walk(stmt))
        elif isinstance(stmt, ast.ClassDef):
            nodes.extend(_original_statements(stmt.body))
        else:
            nodes.extend(ast.walk(stmt))
    return nodes


def original_nodes(path: Path) -> list[ast.AST]:
    """Every AST node of `path` outside mutmut's numbered mutant copies."""
    return _original_statements(ast.parse(path.read_text(encoding="utf-8")).body)


def calls_to(nodes: Iterable[ast.AST], attribute: str) -> list[str]:
    """The source of each `<expr>.<attribute>(...)` call among `nodes`."""
    return [
        ast.unparse(n) for n in nodes
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attribute
    ]


__all__ = ["calls_to", "original_nodes"]
