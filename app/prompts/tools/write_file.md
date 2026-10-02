---
name: WriteFile
description: Creates a new file or completely overwrites an existing file.
---
Writes a file to the local filesystem, overwriting if one already exists at the provided path.

Usage:
- When to use: creating a new file, or fully replacing one you've already read with ReadFile.
- Overwriting an existing file you haven't read will risk losing content. Read the file first.
- Prefer the Edit tool for making surgical modifications to existing files — it only sends the diff. Only use WriteFile when creating new files or performing a complete rewrite.
- NEVER create documentation files (*.md) or README files unless explicitly requested by the user.
- Parent directories are created automatically if they do not already exist.
- Only use emojis if the user explicitly requests it.
