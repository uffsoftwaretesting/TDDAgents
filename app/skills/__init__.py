"""
Skills (Phase 8): upstream's skill commands, ported from `skills/loadSkillsDir.ts`,
`skills/bundledSkills.ts`, `tools/SkillTool/` and `tools/AgentTool/runAgent.ts`'s preload.

The light modules are re-exported here. The Skill tool (`skills/tool.py`) and the agent preload
(`skills/preload.py`) are imported directly: the tool pulls in the worker loop, which this
package's light modules must not.
"""
