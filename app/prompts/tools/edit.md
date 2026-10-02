---
name: Edit
description: Performs exact string replacements in an existing file.
---
Performs exact string replacements in files.

Usage:
- Before editing, you should read the file to understand its current content and structure.
- Provide `path`, `old_string`, and `new_string`.
- `old_string` must match exactly one unique occurrence in the file, including all indentation (tabs/spaces) and newlines. If it matches multiple or zero occurrences, the edit will fail.
- When editing text from ReadFile output, ensure you preserve the exact indentation as it appears AFTER any line number prefix. Never include any part of the line number prefix in the old_string or new_string.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it.
- Use `replace_all` for replacing and renaming strings across the file (e.g., renaming a variable globally).
