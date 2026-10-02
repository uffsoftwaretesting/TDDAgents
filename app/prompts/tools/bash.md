---
name: Bash
description: Executes a given bash command in the workspace environment and returns stdout/stderr.
---
Executes a given bash command and returns its output.

**IMPORTANT:** Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, or `ls` commands unless explicitly instructed or after you have verified that a dedicated tool (ReadFile, Grep, Glob) cannot accomplish your task. Dedicated tools provide a much better experience.

Rules:
- The working directory persists between commands, but shell state (variables, aliases) does not. The shell environment is initialized from the user's profile.
- Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. Never prepend `cd <current-directory>` to a `git` command — `git` already operates on the current working tree, and the compound triggers a permission prompt.
- Always quote file paths that contain spaces with double quotes (e.g., `cd "path with spaces/file.txt"`).
- If your command will create new directories or files, first run `ls` to verify the parent directory exists and is the correct location.

## Git Safety Protocol
- NEVER update the git config.
- NEVER run destructive git commands (`push --force`, `reset --hard`, `checkout .`, `restore .`, `clean -f`, `branch -D`) unless the user explicitly requests these actions.
- NEVER skip hooks (`--no-verify`) or bypass signing (`--no-gpg-sign`) unless the user explicitly requests it. If a hook fails, investigate and fix the underlying issue.
- CRITICAL: Always create NEW commits rather than amending, unless the user explicitly requests `git amend`. After hook failure, fix the issue, re-stage, and create a NEW commit.
- When staging files, prefer adding specific files by name rather than using `git add -A` or `git add .`, which can accidentally include sensitive files (`.env`, credentials) or large binaries.
- Before running destructive operations (`git reset --hard`, `git push --force`, `git checkout --`), consider whether there is a safer alternative that achieves the same goal.
- NEVER use git commands with the `-i` flag (like `git rebase -i` or `git add -i`) since they require interactive input which is not supported.
