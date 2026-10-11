---
name: TodoWrite
description: Update the todo list for the current session.
---
Update the todo list for the current session. To be used proactively and often to track progress and pending tasks. Make sure that at least one task is in_progress at all times. Always provide both content (imperative) and activeForm (present continuous) for each task.

## When to Use This Tool

Use this tool in these situations:

1. Complex multi-step work — a sub-requirement that needs three or more distinct steps.
2. Non-trivial tasks — work that needs planning before the first edit.
3. The user supplies multiple requirements at once — capture each as its own item.
4. After receiving new instructions — record them immediately so nothing is dropped.
5. Before starting a step — mark it in_progress, one item at a time.
6. Immediately after finishing a step — mark it completed, and do not batch completions.
7. When the plan changes — rewrite the list rather than working from a stale one.

## When NOT to Use This Tool

Skip it when:

1. The work is a single straightforward step.
2. The task is trivial and tracking it adds no information.
3. The whole job finishes in fewer than three trivial steps.
4. The exchange is purely conversational.

## Task States and Management

1. **States**
   - `pending` — not started.
   - `in_progress` — being worked on now. Exactly one item should be in_progress.
   - `completed` — finished and verified.

2. **Management**
   - Update states in real time, as the work happens.
   - Complete a task the moment it is actually done.
   - Keep exactly one task in_progress at any time.
   - Finish the current task before starting the next.

3. **Completion requirements**
   - Only mark a task completed when it is fully accomplished.
   - If tests fail, the implementation is partial, or you hit an error you could not
     resolve, keep the task in_progress and add a new task describing what is blocking it.
   - Never mark a task completed because you intend to finish it shortly.

4. **Breakdown**
   - Write specific, actionable items.
   - Split complex work into smaller concrete steps.
   - Use clear, descriptive names.
   - Give every task both forms: `content` as the imperative ("Write the failing test"),
     `activeForm` as the present continuous ("Writing the failing test").

When in doubt, use this tool. Being proactive with task management shows attentiveness and
makes sure every requirement is completed.
