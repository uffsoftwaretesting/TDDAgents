"""Phase 5 F2: the compaction prompt (`services/compact/prompt.ts`) plus the TDD sections."""

from __future__ import annotations

from app.loop.compact.prompt import (
    COMPACT_SUMMARIZER_SYSTEM_PROMPT,
    format_compact_summary,
    get_compact_prompt,
    get_compact_user_summary_message,
)


def test_the_prompt_is_preamble_base_tdd_then_trailer():
    prompt = get_compact_prompt()
    assert prompt.startswith("CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.\n\n")
    assert "- Do NOT use ReadFile, Bash, Grep, Glob, Edit, WriteFile, or ANY other tool." in prompt
    assert "Your task is to create a detailed summary of the conversation so far" in prompt
    assert "9. Optional Next Step:" in prompt
    # The TDD sections come after upstream's base prompt and before the trailer.
    base_end = prompt.index("When you are using compact - please focus on test output and code changes.")
    tdd = prompt.index("## TDD state")
    assert base_end < tdd
    for heading in ("Phase ledger", "Latest failing-test evidence", "Test files", "Sub-requirement"):
        assert heading in prompt[tdd:]
    assert prompt.endswith(
        "\n\nREMINDER: Do NOT call any tools. Respond with plain text only — an <analysis> block followed "
        "by a <summary> block. Tool calls will be rejected and you will fail the task."
    )
    assert "{{" not in prompt


def test_custom_instructions_go_before_the_trailer():
    prompt = get_compact_prompt("Keep the stack traces.")
    assert "\n\nAdditional Instructions:\nKeep the stack traces.\n\nREMINDER: Do NOT call any tools." in prompt
    assert "Additional Instructions" not in get_compact_prompt("   ")
    assert "Additional Instructions" not in get_compact_prompt(None)


def test_format_compact_summary_drops_analysis_and_unwraps_summary():
    raw = "<analysis>thinking\nmore</analysis>\n\n\n<summary>\n  1. Request\n</summary>\n\n\n\ntrailing"
    assert format_compact_summary(raw) == "Summary:\n1. Request\n\ntrailing"
    assert format_compact_summary("  plain text  ") == "plain text"
    assert format_compact_summary("<summary></summary>") == "Summary:"


def test_user_summary_message_variants():
    base = get_compact_user_summary_message("<summary>S</summary>")
    assert base == (
        "This session is being continued from a previous conversation that ran out of context. The summary "
        "below covers the earlier portion of the conversation.\n\nSummary:\nS"
    )
    with_path = get_compact_user_summary_message("x", transcript_path="/t.jsonl")
    assert with_path.endswith(
        "\n\nIf you need specific details from before compaction (like exact code snippets, error messages, or "
        "content you generated), read the full transcript at: /t.jsonl"
    )
    kept = get_compact_user_summary_message("x", recent_messages_preserved=True)
    assert kept.endswith("\n\nRecent messages are preserved verbatim.")
    cont = get_compact_user_summary_message("x", suppress_follow_up_questions=True)
    assert cont.endswith(
        "x\nContinue the conversation from where it left off without asking the user any further questions. "
        "Resume directly — do not acknowledge the summary, do not recap what was happening, do not preface "
        'with "I\'ll continue" or similar. Pick up the last task as if the break never happened.'
    )


def test_summarizer_system_prompt():
    assert COMPACT_SUMMARIZER_SYSTEM_PROMPT == "You are a helpful AI assistant tasked with summarizing conversations."
