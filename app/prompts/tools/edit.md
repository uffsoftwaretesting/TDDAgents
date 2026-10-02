---
name: Edit
description: Makes a targeted replacement in an existing file.
---
Replaces a specific section of text in an existing file with new content.

Usage:
- Provide `path`, `old_string`, and `new_string`.
- `old_string` must match exactly one unique occurrence in the file, including indentation and newlines.
- If `old_string` matches multiple occurrences or zero occurrences, the edit will fail. Read the file first to verify uniqueness.
