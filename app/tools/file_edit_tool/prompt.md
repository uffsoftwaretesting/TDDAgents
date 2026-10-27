---
name: Edit
description: Performs exact string replacements in files.
---
Performs exact string replacements in files.

Usage:
- You must use `ReadFile` at least once before editing an existing file. This tool will error if you attempt an edit without reading the file, or if the file changed since you read it.
- When editing text from `ReadFile` output, preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The prefix format is: line number + tab. Everything after that is the actual file content to match. Never include any part of the line number prefix in `old_string` or `new_string`.
- Provide `file_path`, `old_string`, and `new_string`. `new_string` must differ from `old_string`.
- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.
- Use `replace_all` for replacing and renaming strings across the file.
- An empty `old_string` creates a new file, or fills an existing empty one.
- ALWAYS prefer editing existing files. NEVER write new files unless explicitly required.
