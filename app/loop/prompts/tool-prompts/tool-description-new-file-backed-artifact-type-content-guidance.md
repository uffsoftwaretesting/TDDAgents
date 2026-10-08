---
name: "Tool Description: New file-backed Artifact type content guidance"
description: "Explains how to create and later update a file-backed Artifact type instance while preserving its index metadata, reading changed files, and publishing only project content"
type: "tool-prompts"
---

This type keeps a new Artifact's content in the Artifact's own files under `project/`${ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.storeDescribed?`, never in its store${ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.storeCall===null?"":` (no ${ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.storeCall} for its content)`}`:""}. The files: `${ARTIFACT_TYPE_FILE_STORAGE_CONFIG.index}`, the index, a JSON object with ${ARTIFACT_TYPE_FILE_STORAGE_CONFIG.indexKeys}, plus ${ARTIFACT_CREATED_ON_FILES_MARKER}; and ${ARTIFACT_TYPE_FILE_STORAGE_CONFIG.files}.${ARTIFACT_TYPE_FILE_STORAGE_CONFIG.uploads}${FORMAT_ARTIFACT_TYPE_STORE_OVERRIDE_NOTE_FN(ARTIFACT_TYPE_FILE_STORAGE_CONFIG,ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.storeDescribed)} ${FORMAT_ARTIFACT_FIRST_PUBLISH_INSTRUCTIONS_FN(ARTIFACT_TYPE_FILE_STORAGE_CONFIG,ARTIFACT_URL)} Later changes: ${ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.pinned?`${FORMAT_PINNED_FILE_READ_GUIDANCE_FN(ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.read)} Publish only the changed files the same way (`file_path`: one changed file's absolute path, `files`: the other changed ones); send the index only when you ${ARTIFACT_TYPE_FILE_STORAGE_CONFIG.indexEdits}, keeping every other key and its `createdOnFiles` object as you last wrote or read it. ${PUBLISH_REFUSAL_FOLLOW_NOTE}`:`read each file you will change (${ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.read}) and publish only those the same way (`file_path`: one changed file's absolute path); send the index only when you ${ARTIFACT_TYPE_FILE_STORAGE_CONFIG.indexEdits}, keeping every other key and its `createdOnFiles` object as read.`} Never publish index.html, SKILL.md or anything under `artifact-type/`.

### Artifact Management
All artifact actions must be persisted to `{artifacts_dir}`.
Track versions carefully to support Contextual Adaptability. Send feedback to `{artifact_feedback_channel}`.


### Tool Execution Policies
- Execute commands strictly within `{working_directory}` to enforce the *Safety & Security* boundary.
- Run tools in the core while-loop. Validate results for *Reliable Execution*.
- Before running destructive actions, defer to the user to maintain *Human Decision Authority*.



> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
