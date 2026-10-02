---
name: ReadFile
description: Reads the contents of a file from the local filesystem.
---
Reads a file from the local filesystem. You can access any file directly by using this tool.

Assume this tool is able to read all files on the machine. If the user provides a path to a file, assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The file_path parameter must be an absolute path, not a relative path.
- By default, it reads up to 2000 lines starting from the beginning of the file.
- For long files, use `offset` and `limit` parameters to read targeted sections rather than loading the entire file.
- This tool can only read files, not directories. To list files in a directory, use the Bash tool.
- If you read a file that exists but has empty contents, you will receive a system reminder warning in place of file contents.
- Use this tool before modifying existing files with Edit to ensure accurate line references.
