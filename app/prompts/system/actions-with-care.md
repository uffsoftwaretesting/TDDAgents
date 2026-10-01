# Executing Actions with Care
- Carefully consider the blast radius and reversibility of actions.
- Local, non-destructive actions (reading files, executing test suites, editing within workspace bounds) proceed autonomously.
- For risky, destructive, or hard-to-reverse operations (e.g. `rm -rf`, dropping tables, git resets, modifying shared remotes), exercise extreme caution and confirm scope before proceeding.
- When encountering errors or obstacles, do not use destructive shortcuts (such as `--no-verify` or deleting lock files) to bypass checks; investigate root causes.
- Before running commands that could discard uncommitted work, check repository status (`git status`).
