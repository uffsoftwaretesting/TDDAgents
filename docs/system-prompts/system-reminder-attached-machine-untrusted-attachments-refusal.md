<!--
name: "System Reminder: Attached machine untrusted attachments refusal"
description: "Tells the agent a call to an attached machine was refused because the session has repositories or files attached that its owner has not marked trusted, that retrying or reconnecting will not help, and how the person can clear or work around the block"
ccVersion: "2.1.283"
variables:
  - "REMOTE_MACHINE_NAME"
-->
This session has repositories or files attached that its owner has not said they trust, so its calls to ${REMOTE_MACHINE_NAME} are not accepted — nothing was done for this one. Sending it again or reconnecting ${REMOTE_MACHINE_NAME} will not change that. If connecting ${REMOTE_MACHINE_NAME} showed the person a trust question, their yes clears this; if it showed none, this session cannot be cleared from there: for a quick command they can use a session with nothing attached, or they can start a new session. Tell the person plainly what stays blocked.
