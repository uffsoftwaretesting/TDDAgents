"""
The baseline snapshot, the ignore rules, and the conflict classifier.

The classifier is the load-bearing piece: without a baseline you can only see that two
sides differ, not which one changed. Every case below is a statement about that
distinction.
"""

from __future__ import annotations

from app.sync.baseline import (
    Action,
    Baseline,
    IgnoreRules,
    classify,
    content_hash,
    snapshot,
)


def h(text: str) -> str:
    return content_hash(text)


# ── Baseline persistence ─────────────────────────────────────────────────────

def test_baseline_round_trips_through_disk(baseline_path):
    Baseline(entries={"a.py": h("x")}, taken_at=123.0).save(baseline_path)
    loaded = Baseline.load(baseline_path)
    assert loaded.entries == {"a.py": h("x")}
    assert loaded.taken_at == 123.0


def test_missing_baseline_loads_empty(baseline_path):
    loaded = Baseline.load(baseline_path)
    assert loaded.entries == {}
    assert loaded.taken_at == 0.0


def test_corrupt_baseline_degrades_to_empty(baseline_path):
    baseline_path.write_text("{not json", encoding="utf-8")
    assert Baseline.load(baseline_path).entries == {}


def test_save_creates_parent_directories(tmp_path):
    target = tmp_path / "nested" / "deeper" / "baseline.json"
    Baseline(entries={}).save(target)
    assert target.exists()


# ── Ignore rules ─────────────────────────────────────────────────────────────

def test_git_is_always_excluded_even_without_a_pattern():
    rules = IgnoreRules([])
    assert rules.matches(".git/config") is True
    assert rules.matches("src/.git/HEAD") is True


def test_bare_pattern_matches_at_any_depth():
    rules = IgnoreRules(["__pycache__/", "*.pyc"])
    assert rules.matches("__pycache__/mod.pyc") is True
    assert rules.matches("src/deep/__pycache__/mod.pyc") is True
    assert rules.matches("src/mod.pyc") is True
    assert rules.matches("src/mod.py") is False


def test_anchored_pattern_matches_from_the_root_only():
    rules = IgnoreRules(["/build/"])
    assert rules.matches("build/out.js") is True
    assert rules.matches("src/build/out.js") is False


def test_interior_slash_anchors_the_pattern():
    """git treats any pattern with a non-trailing slash as root-anchored."""
    rules = IgnoreRules(["docs/generated"])
    assert rules.matches("docs/generated/api.md") is True
    assert rules.matches("docs/generated") is True
    assert rules.matches("src/docs/generated/api.md") is False


def test_anchored_dir_pattern_does_not_match_a_file_of_the_same_name():
    rules = IgnoreRules(["/build/"])
    assert rules.matches("build/out.js") is True
    assert rules.matches("build") is False


def test_anchored_file_pattern_matches_that_file_only():
    rules = IgnoreRules(["/.coverage"])
    assert rules.matches(".coverage") is True
    assert rules.matches("src/.coverage") is False


def test_add_parses_the_same_way_as_the_constructor():
    """engine.py appends the sync marker through add(); it must not bypass parsing."""
    rules = IgnoreRules([])
    rules.add("/build/")
    assert rules.matches("build/out.js") is True
    assert rules.matches("src/build/out.js") is False


def test_comments_blanks_and_negations_are_dropped():
    rules = IgnoreRules(["# a comment", "", "   ", "!keepme.py"])
    assert rules.patterns == []
    assert rules.matches("keepme.py") is False


def test_rules_prefer_a_gitignore_from_the_workspace(fake_sandbox):
    fake_sandbox.files[".gitignore"] = "# generated\nbuild/\n*.log\n"
    rules = IgnoreRules.from_workspace(fake_sandbox, ["fallback_only/"])
    assert rules.source == ".gitignore"
    assert rules.matches("build/x.js") is True
    assert rules.matches("app.log") is True
    assert rules.matches("fallback_only/x.py") is False


def test_rules_fall_back_when_no_gitignore(fake_sandbox):
    rules = IgnoreRules.from_workspace(fake_sandbox, ["__pycache__/"])
    assert rules.source == "fallback"
    assert rules.matches("__pycache__/x.pyc") is True


# ── Snapshot ─────────────────────────────────────────────────────────────────

def test_snapshot_hashes_every_non_ignored_file(fake_sandbox):
    fake_sandbox.files = {
        "src/main.py": "print(1)",
        "__pycache__/main.pyc": "binary-ish",
        "tests/test_main.py": "assert True",
    }
    digests = snapshot(fake_sandbox, IgnoreRules(["__pycache__/"]))
    assert set(digests) == {"src/main.py", "tests/test_main.py"}
    assert digests["src/main.py"] == h("print(1)")


# ── Classification ───────────────────────────────────────────────────────────

def only(decisions, path):
    return next(d for d in decisions if d.path == path)


def test_identical_sides_are_a_noop():
    base = Baseline(entries={"a.py": h("v1")})
    decisions = classify(base, {"a.py": h("v1")}, {"a.py": h("v1")})
    assert only(decisions, "a.py").action is Action.NOOP


def test_one_sided_change_propagates():
    base = Baseline(entries={"a.py": h("v1")})
    decisions = classify(base, {"a.py": h("v2")}, {"a.py": h("v1")})
    assert only(decisions, "a.py").action is Action.PROPAGATE


def test_both_sides_changed_is_a_conflict():
    base = Baseline(entries={"a.py": h("v1")})
    decisions = classify(base, {"a.py": h("sandbox")}, {"a.py": h("local")})
    decision = only(decisions, "a.py")
    assert decision.action is Action.CONFLICT
    assert "sandbox wins" in decision.reason


def test_conflict_winner_flips_outside_a_run():
    base = Baseline(entries={"a.py": h("v1")})
    decisions = classify(
        base, {"a.py": h("sandbox")}, {"a.py": h("local")}, sandbox_wins=False
    )
    assert "local wins" in only(decisions, "a.py").reason


def test_new_file_on_one_side_propagates():
    decisions = classify(Baseline(entries={}), {"new.py": h("x")}, {})
    assert only(decisions, "new.py").action is Action.PROPAGATE


def test_deletion_propagates_when_the_other_side_is_untouched():
    """The baseline proves the surviving copy never changed, so the delete is safe."""
    base = Baseline(entries={"a.py": h("v1")})
    decisions = classify(base, {}, {"a.py": h("v1")})
    assert only(decisions, "a.py").action is Action.DELETE


def test_deletion_is_skipped_when_the_other_side_changed():
    """
    Deleted in the sandbox, edited locally. Honoring the delete would silently discard
    the edit, so the deletion does not propagate.
    """
    base = Baseline(entries={"a.py": h("v1")})
    decisions = classify(base, {}, {"a.py": h("edited-locally")})
    assert only(decisions, "a.py").action is Action.SKIP_DELETE


def test_deletion_on_both_sides_is_a_noop():
    base = Baseline(entries={"a.py": h("v1")})
    decisions = classify(base, {}, {})
    assert only(decisions, "a.py").action is Action.NOOP


def test_every_known_path_gets_exactly_one_decision():
    base = Baseline(entries={"only_in_base.py": h("x"), "shared.py": h("v1")})
    decisions = classify(
        base,
        {"shared.py": h("v2"), "only_in_sandbox.py": h("s")},
        {"shared.py": h("v1"), "only_in_local.py": h("l")},
    )
    paths = [d.path for d in decisions]
    assert sorted(paths) == [
        "only_in_base.py",
        "only_in_local.py",
        "only_in_sandbox.py",
        "shared.py",
    ]
    assert len(paths) == len(set(paths))
