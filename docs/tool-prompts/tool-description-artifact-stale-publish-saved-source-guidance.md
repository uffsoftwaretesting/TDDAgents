<!--
name: "Tool Description: Artifact stale publish saved-source guidance"
description: "Refusal for a publish not built on the live Artifact version, pointing to the saved full source file, stating when that version counts as viewed, and requiring edits merged onto the saved file rather than resent or rebuilt from memory"
ccVersion: "2.1.283"
variables:
  - "STALE_VERSION_REFUSAL_LEAD"
  - "FORMAT_FILE_SIZE_FN"
  - "LIVE_ARTIFACT_VERSION"
  - "PERSISTED_SOURCE_FILE"
  - "MODIFIED_PRIOR_COPY_PATH"
  - "REQUIRES_FULL_READ"
  - "REQUIRED_READ_PROGRESS_NOTE"
  - "SAVED_SOURCE_SHAPE_DESCRIPTION"
  - "UNTRUSTED_SOURCE_CONTENT_NOTE"
  - "FORCE_REFUSAL_NOTE"
  - "AUTHORED_BY_OTHERS_MARKER"
-->
${STALE_VERSION_REFUSAL_LEAD} Its full source (${FORMAT_FILE_SIZE_FN(LIVE_ARTIFACT_VERSION.bytes)}) is saved at ${PERSISTED_SOURCE_FILE.filepath}${MODIFIED_PRIOR_COPY_PATH===void 0?"":` (saved afresh: the copy at ${MODIFIED_PRIOR_COPY_PATH} was modified after it was handed to you, so Reads of it no longer count)`}${REQUIRES_FULL_READ?`, and that version counts as viewed once you have Read every line of that file${REQUIRED_READ_PROGRESS_NOTE}: Read it in full`:" and now counts as viewed: Read what you need of that file"} and merge your edits onto it so no published content is lost, then publish again from your own file, leaving the saved copy as it is — do not resend your previous content unchanged, and do not rebuild from memory or from a truncated copy. That file is ${SAVED_SOURCE_SHAPE_DESCRIPTION}.${UNTRUSTED_SOURCE_CONTENT_NOTE}${FORCE_REFUSAL_NOTE}${AUTHORED_BY_OTHERS_MARKER}
