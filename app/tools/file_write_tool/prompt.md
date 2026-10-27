---
name: WriteFile
description: Creates a new file or completely overwrites an existing file.
---
Writes content to a file in the local workspace.

Usage:
- Overwrites the existing file if one exists at the specified path.
- Prefer `Edit` for making surgical updates to existing files; use `WriteFile` when creating new files or replacing an entire file.
- Parent directories are created automatically if they do not already exist.
