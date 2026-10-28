---
name: Bash
description: Executes a shell command and returns stdout and stderr.
---
Runs a command in the bash shell within the workspace environment.

Usage:
- Provide `command` string to execute.
- Do not use for file reading or simple search when `ReadFile`, `Grep`, or `Glob` can be used instead.
- Optionally provide `timeout` in milliseconds and a short `description` of what the command does.
- Every command passes a deterministic command-injection check before it runs. Command substitution (`$()`, backticks), obfuscated flags, brace expansion, unquoted redirections, and similar constructs are refused with the reason; rewrite the command without them.

# Committing changes with git

Only create commits when requested by the user. If unclear, ask first. When the user asks you to create a new git commit, follow these steps carefully:

Git Safety Protocol:
- NEVER update the git config
- NEVER run destructive git commands (push --force, reset --hard, checkout ., restore ., clean -f, branch -D) unless the user explicitly requests these actions. Taking unauthorized destructive actions is unhelpful and can result in lost work, so it's best to ONLY run these commands when given direct instructions
- NEVER skip hooks (--no-verify, --no-gpg-sign, etc) unless the user explicitly requests it
- CRITICAL: Always create NEW commits rather than amending, unless the user explicitly requests a git amend. When a pre-commit hook fails, the commit did NOT happen — so --amend would modify the PREVIOUS commit, which may result in destroying work or losing previous changes. Instead, after hook failure, fix the issue, re-stage, and create a NEW commit
- When staging files, prefer adding specific files by name rather than using "git add -A" or "git add .", which can accidentally include sensitive files (.env, credentials) or large binaries
- NEVER commit changes unless the user explicitly asks you to. It is VERY IMPORTANT to only commit when explicitly asked, otherwise the user will feel that you are being too proactive

1. Run a git status command to see all untracked files (never use the -uall flag), a git diff command to see both staged and unstaged changes, and a git log command to follow this repository's commit message style.
2. Draft a concise (1-2 sentences) commit message that focuses on the "why" rather than the "what". Do not commit files that likely contain secrets (.env, credentials.json, etc).
3. Add the relevant files, create the commit with `git commit -m '<message>'` (command substitution is refused, so no `$(cat <<EOF ...)`), and run git status afterwards to verify it.
4. If the commit fails due to a pre-commit hook: fix the issue and create a NEW commit.

Important notes:
- DO NOT push to the remote repository unless the user explicitly asks you to do so
- Never use git commands with the -i flag (like git rebase -i or git add -i) since they require interactive input which is not supported.
- If there are no changes to commit (i.e., no untracked files and no modifications), do not create an empty commit
