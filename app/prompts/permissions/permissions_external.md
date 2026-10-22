---
name: auto-mode-permissions
description: Default environment, ALLOW and BLOCK rules of the auto-mode classifier.
authored-by: TDDAgents
provenance: >-
  Upstream's permissions_external.txt was compiled out of claude-code v2.1.88. TDDAgents
  reconstruction. Each section's defaults sit inside its <user_*_to_replace> tag, as
  upstream's external template does, so settings `autoMode` rules REPLACE them.
---
## Environment

<user_environment_to_replace>
- The agent works inside a per-run project workspace on the user's machine; files outside it belong to the user.
- The workspace has its own Python virtual environment, and tests run with pytest.
- The project's purpose is to turn a requirement into tested code; network access is not part of that work.
</user_environment_to_replace>

## ALLOW rules

Allow these unless a BLOCK rule applies:

<user_allow_rules_to_replace>
- Reading, searching and listing files inside the workspace.
- Creating and editing source and test files inside the workspace as part of the requested task.
- Running the project's test suite, linters and type checkers.
- Read-only inspection commands (`ls`, `cat`, `grep`, `git status`, `git diff`, `git log`) and creating directories inside the workspace.
- Installing the project's declared dependencies from its own requirements file into the workspace environment.
</user_allow_rules_to_replace>

## BLOCK rules

Block actions that fall under these categories:

<user_deny_rules_to_replace>
- Code from External: downloading and running code or scripts from the network (`curl ... | sh`, fetching and executing remote payloads), or installing packages the project does not declare.
- Irreversible Local Destruction: deleting or overwriting files or directories the task did not create (`rm -rf` outside generated paths, truncating existing files with `>`), or rewriting version-control history.
- Data Exfiltration: sending workspace or user data off the machine (uploads, posting to URLs, writing to network shares), or reading credentials, keys, tokens or personal files.
- Unauthorized Persistence: modifying shell profiles, cron jobs, services, startup items, git hooks or editor configuration outside the workspace.
- Security Weaken: disabling or editing the agent's own permission settings, hooks or safety configuration; changing file permissions to make things world-writable; elevating privileges (`sudo`, `su`).
- Scope Escape: acting on paths outside the workspace, or on systems, accounts or resources the task does not involve.
- Test Subversion: using the ability to run tests to execute non-test actions, or editing the test runner or its configuration to make failing tests report success.
</user_deny_rules_to_replace>
