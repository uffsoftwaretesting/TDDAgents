#!/usr/bin/env python3
"""Verify every `- Code:` claim in a reference doc resolves under reference/claude-code/.

Usage: verify_paths.py <markdown file>...
Exits non-zero if any cited path or symbol is missing.
"""
import re, subprocess, sys, pathlib

ROOT = pathlib.Path("reference")

def check_loose(md: pathlib.Path):
    """Citations outside `- Code:` lines -- tables and prose."""
    bad, checked = [], 0
    for i, l in enumerate(md.read_text().split("\n"), 1):
        if l.startswith("- Code:"):
            continue
        for m in re.finditer(r'`(claude-code/[^`]+)`(?:\s*\u2192\s*`([^`]+)`)?', l):
            path, sym = m.group(1), m.group(2)
            checked += 1
            if "<" in path:
                continue
            if not (ROOT / path).exists():
                bad.append((i, path, None, "nopath")); continue
            if sym:
                checked += 1
                if subprocess.run(["grep", "-rqF", "--", sym.strip(), str(ROOT / path)]).returncode != 0:
                    bad.append((i, path, sym, "nosym"))
    return checked, bad


def check(md: pathlib.Path):
    bad_paths, bad_syms, checked = [], [], 0
    for i, l in enumerate(md.read_text().split("\n"), 1):
        if not l.startswith("- Code:"):
            continue
        for claim in l[len("- Code:"):].split(";"):
            paths = re.findall(r'`(claude-code/[^`]+)`', claim)
            for p in paths:
                checked += 1
                if "<" in p:                       # documented placeholder
                    continue
                if not (ROOT / p).exists():
                    bad_paths.append((i, p))
            m = re.search(r'→\s*(.*)$', claim)
            if not (m and paths):
                continue
            base = ROOT / paths[0]
            if not base.exists():
                continue
            for s in re.findall(r'`([^`]+)`', m.group(1)):
                s = s.strip().strip("'\"")
                if not s or s.startswith(("see ", "the ")):
                    continue
                checked += 1
                # a filename/dir reference relative to a cited directory
                if base.is_dir() and (s.endswith((".ts", ".tsx", ".js", "/")) or "/" in s):
                    if not (base / s.rstrip("/")).exists():
                        bad_syms.append((i, paths[0], s, "nofile"))
                    continue
                if subprocess.run(["grep", "-rqF", "--", s, str(base)]).returncode != 0:
                    bad_syms.append((i, paths[0], s, "nosym"))
    return checked, bad_paths, bad_syms

total, failures = 0, 0
for arg in sys.argv[1:]:
    md = pathlib.Path(arg)
    checked, bp, bs = check(md)
    loose_checked, loose_bad = check_loose(md)
    total += checked + loose_checked
    print(f"{md.name}: {checked} structured + {loose_checked} inline = {checked + loose_checked} claims checked")
    for i, p in bp:
        print(f"  MISSING PATH   L{i}: {p}"); failures += 1
    for i, p, s, k in bs:
        print(f"  MISSING {k.upper():6} L{i}: {p} -> {s}"); failures += 1
    for i, p, s, k in loose_bad:
        print(f"  MISSING {k.upper():6} L{i}: {p}" + (f" -> {s}" if s else "")); failures += 1
print(f"\ntotal {total} claims, {failures} failures")
sys.exit(1 if failures else 0)
