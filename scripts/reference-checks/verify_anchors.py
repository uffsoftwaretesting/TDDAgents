#!/usr/bin/env python3
"""Verify every analysis anchor link and intra-doc anchor in a reference doc resolves.

Usage: verify_anchors.py <markdown file>...
Uses GitHub's anchor algorithm: lowercase, strip non-word chars, space -> hyphen.
"""
import re, sys, pathlib

ROOT = pathlib.Path("reference")

def anchors(md: pathlib.Path):
    out = set()
    for l in md.read_text().split("\n"):
        m = re.match(r'^(#{1,6}) (.+)$', l)
        if not m:
            continue
        a = re.sub(r'[`*\\]', '', m.group(2).strip().lower())
        a = re.sub(r'[^\w\s-]', '', a)
        out.add(a.strip().replace(' ', '-'))
    return out

cache, ok, bad = {}, 0, []
for arg in sys.argv[1:]:
    md = pathlib.Path(arg)
    for i, l in enumerate(md.read_text().split("\n"), 1):
        for f, a in re.findall(r'`(analysis/[^`]+\.md)#([^`]+)`', l):
            target = ROOT / f
            if not target.exists():
                bad.append((md.name, i, f, a, "nofile")); continue
            if f not in cache:
                cache[f] = anchors(target)
            ok, _ = (ok + 1, None) if a in cache[f] else (ok, bad.append((md.name, i, f, a, "noanchor")))
        for a in re.findall(r'\]\(#([a-z0-9-]+)\)', l):
            key = str(md)
            if key not in cache:
                cache[key] = anchors(md)
            ok, _ = (ok + 1, None) if a in cache[key] else (ok, bad.append((md.name, i, md.name, a, "noanchor")))

print(f"anchor links OK: {ok}")
for name, i, f, a, k in bad:
    print(f"  BROKEN [{k}] {name} L{i}: {f}#{a}")
sys.exit(1 if bad else 0)
