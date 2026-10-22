---
name: compact-summary-continuation
description: suppressFollowUpQuestions continuation of getCompactUserSummaryMessage.
provenance: >-
  Ported verbatim from claude-code v2.1.88 src/services/compact/prompt.ts.
---
Continue the conversation from where it left off without asking the user any further questions. Resume directly — do not acknowledge the summary, do not recap what was happening, do not preface with "I'll continue" or similar. Pick up the last task as if the break never happened.
