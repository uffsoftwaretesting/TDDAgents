<!--
name: "System Reminder: Attached machine stopped answering"
description: "Reports that an attached machine stopped answering health checks while or after a command was sent, so the command's outcome is unknown, and forbids retrying non-idempotent commands or calling that machine again this turn"
ccVersion: "2.1.283"
variables:
  - "REMOTE_MACHINE_NAME"
  - "IS_WHILE_COMMAND_RUNNING"
  - "UNANSWERED_CHECK_COUNT"
  - "MATH_OBJECT"
  - "CHECK_INTERVAL_MS"
-->
${REMOTE_MACHINE_NAME} stopped answering ${IS_WHILE_COMMAND_RUNNING?"while this command was running":"after this command was sent to it"} (${UNANSWERED_CHECK_COUNT} checks about ${MATH_OBJECT.round(CHECK_INTERVAL_MS/1000)} s apart went unanswered). Its state is unknown — it may have completed, failed, ${IS_WHILE_COMMAND_RUNNING?"or still be running":"never started, or still be running"}. Do not retry non-idempotent commands on ${REMOTE_MACHINE_NAME} until ${REMOTE_MACHINE_NAME} reconnects, and do not call it again in this turn: do the rest of the task that this container can do, and tell the user what is blocked and what you could not verify. Check once more only when the user says it is back or asks you to try again.
