## Constructing a Hook (with verification)

Given an event, matcher, target file, and desired behavior, follow this flow. Each step catches a different failure class — a hook that silently does nothing is worse than no hook.

1. **Dedup check.** Read the target file. If a hook already exists on the same event+matcher, show the existing command and ask: keep it, replace it, or add alongside.

2. **Construct the command for THIS project — don't assume.** The hook receives JSON on stdin. Build a command that:
   - Extracts any needed payload safely — use `jq -r` into a quoted variable or `{ read -r f; ... "$f"; }`, NOT unquoted `| xargs` (splits on spaces)
   - Invokes the underlying tool the way this project runs it (npx/bunx/yarn/pnpm? Makefile target? globally-installed?)
   - Skips inputs the tool doesn't handle (formatters often have `--ignore-unknown`; if not, guard by extension)
   - Stays RAW for now — no `|| true`, no stderr suppression. You'll wrap it after the pipe-test passes.

3. **Pipe-test the raw command.** Synthesize the stdin payload the hook will receive and pipe it directly:
   - `Pre|PostToolUse` on `WriteFile|Edit`: `echo '{"tool_name":"Edit","tool_input":{"file_path":"<a real file from this repo>"}}' | <cmd>`
   - `Pre|PostToolUse` on `Bash`: `echo '{"tool_name":"Bash","tool_input":{"command":"ls"}}' | <cmd>`
   - `Stop`/`UserPromptSubmit`/`SessionStart`: most commands don't read stdin, so `echo '{}' | <cmd>` suffices

   Check exit code AND side effect (file actually formatted, test actually ran). If it fails you get a real error — fix (wrong package manager? tool not installed? jq path wrong?) and retest. Once it works, wrap with `2>/dev/null || true` (unless the user wants a blocking check).

4. **Write the JSON.** Merge into the target file (schema shape in the "Hook Structure" section above). If this creates `.tddagents/settings.local.json` for the first time, add it to .gitignore — the WriteFile tool doesn't auto-gitignore it.

5. **Validate syntax + schema in one shot:**

   `jq -e '.hooks.<event>[] | select(.matcher == "<matcher>") | .hooks[] | select(.type == "command") | .command' <target-file>`

   Exit 0 + prints your command = correct. Exit 4 = matcher doesn't match. Exit 5 = malformed JSON or wrong nesting. A broken settings.json silently disables ALL settings from that file — fix any pre-existing malformation too.

6. **Handoff.** Tell the user the hook is live from the next session: TDDAgents reads hooks when a session starts, so it needs a restart (resume the session with `--thread-id`).
