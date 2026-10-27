"""
Edit-string matching: curly-quote normalization and the edit itself.

Ported from `reference/claude-code/src/tools/FileEditTool/utils.ts` -> `normalizeQuotes`,
`findActualString`, `preserveQuoteStyle`, `applyEditToFile`.
"""

from __future__ import annotations

LEFT_SINGLE_CURLY_QUOTE = "‘"

RIGHT_SINGLE_CURLY_QUOTE = "’"

LEFT_DOUBLE_CURLY_QUOTE = "“"

RIGHT_DOUBLE_CURLY_QUOTE = "”"


def normalize_quotes(text: str) -> str:
    return (
        text.replace(LEFT_SINGLE_CURLY_QUOTE, "'")
        .replace(RIGHT_SINGLE_CURLY_QUOTE, "'")
        .replace(LEFT_DOUBLE_CURLY_QUOTE, '"')
        .replace(RIGHT_DOUBLE_CURLY_QUOTE, '"')
    )


def find_actual_string(file_content: str, search: str) -> str | None:
    """Exact match first, then a match under curly-quote normalization."""
    if search in file_content:
        return search
    index = normalize_quotes(file_content).find(normalize_quotes(search))
    if index != -1:
        return file_content[index:index + len(search)]
    return None


def _is_opening_context(chars: list[str], index: int) -> bool:
    if index == 0:
        return True
    return chars[index - 1] in (" ", "\t", "\n", "\r", "(", "[", "{", "—", "–")


def _apply_curly_double_quotes(text: str) -> str:
    chars = list(text)
    return "".join(
        (LEFT_DOUBLE_CURLY_QUOTE if _is_opening_context(chars, i) else RIGHT_DOUBLE_CURLY_QUOTE)
        if c == '"' else c
        for i, c in enumerate(chars)
    )


def _apply_curly_single_quotes(text: str) -> str:
    chars = list(text)
    out: list[str] = []
    for i, c in enumerate(chars):
        if c != "'":
            out.append(c)
            continue
        prev = chars[i - 1] if i > 0 else ""
        nxt = chars[i + 1] if i < len(chars) - 1 else ""
        if prev.isalpha() and nxt.isalpha():
            out.append(RIGHT_SINGLE_CURLY_QUOTE)  # contraction, e.g. don't
        else:
            out.append(LEFT_SINGLE_CURLY_QUOTE if _is_opening_context(chars, i) else RIGHT_SINGLE_CURLY_QUOTE)
    return "".join(out)


def preserve_quote_style(old_string: str, actual_old_string: str, new_string: str) -> str:
    """Re-apply the file's curly quotes to `new_string` when the match needed normalizing."""
    if old_string == actual_old_string:
        return new_string
    result = new_string
    if LEFT_DOUBLE_CURLY_QUOTE in actual_old_string or RIGHT_DOUBLE_CURLY_QUOTE in actual_old_string:
        result = _apply_curly_double_quotes(result)
    if LEFT_SINGLE_CURLY_QUOTE in actual_old_string or RIGHT_SINGLE_CURLY_QUOTE in actual_old_string:
        result = _apply_curly_single_quotes(result)
    return result


def apply_edit_to_file(original: str, old_string: str, new_string: str, replace_all: bool) -> str:
    """Deleting a whole line (empty `new_string`) also removes its trailing newline."""
    count = -1 if replace_all else 1
    if new_string == "" and not old_string.endswith("\n") and old_string + "\n" in original:
        return original.replace(old_string + "\n", new_string, count)
    return original.replace(old_string, new_string, count)
