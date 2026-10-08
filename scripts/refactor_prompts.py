import os
import glob
import re

source_dirs = [
    "docs/system-prompts",
    "docs/tool-prompts",
    "docs/data-prompts",
    "docs/agent-prompts",
    "docs/skill-prompts",
    "docs/decode-claude-code-analysis-en"
]

target_base = "app/loop/prompts"

ARCH_TENETS = """
> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
"""

def enhance_text(text, filename, category):
    frontmatter_dict = {}
    match = re.search(r'(<!--\s*name:.*?\n.*?-->)', text, flags=re.DOTALL)
    if match:
        original_frontmatter = match.group(1)
        content = text[match.end():].strip()
        name_match = re.search(r'name:\s*"(.*?)"', original_frontmatter)
        if name_match:
            frontmatter_dict["name"] = name_match.group(1)
        desc_match = re.search(r'description:\s*"(.*?)"', original_frontmatter)
        if desc_match:
            frontmatter_dict["description"] = desc_match.group(1)
    else:
        content = text.strip()
        frontmatter_dict["name"] = filename.replace(".md", "")
        frontmatter_dict["description"] = "Refactored prompt based on Claude Code source analysis."

    if not content:
        return ""

    md_frontmatter = f"---\nname: \"{frontmatter_dict.get('name', 'Prompt')}\"\ndescription: \"{frontmatter_dict.get('description', '')}\"\ntype: \"{category}\"\n---\n\n"
    
    enhanced = md_frontmatter
    
    # Replace instances using standard Python f-string bracket syntax instead of Jinja
    content = content.replace("The user", "The user (ID: {user_id})")
    content = content.replace("the user", "the user (ID: {user_id})")
    content = content.replace("autonomously", "autonomously in {environment_mode} mode")
    
    content = content.replace("claude code", "{agent_name}")
    content = content.replace("Claude Code", "{agent_name}")
    content = content.replace("Claude", "{agent_name}")
    
    if "artifact" in filename.lower() or "artifact" in content.lower():
        content += "\n\n### Artifact Management\n"
        content += "All artifact actions must be persisted to `{artifacts_dir}`.\n"
        content += "Track versions carefully to support Contextual Adaptability. Send feedback to `{artifact_feedback_channel}`.\n"
        
    if "tool" in category.lower():
        content += "\n\n### Tool Execution Policies\n"
        content += "- Execute commands strictly within `{working_directory}` to enforce the *Safety & Security* boundary.\n"
        content += "- Run tools in the core while-loop. Validate results for *Reliable Execution*.\n"
        content += "- Before running destructive actions, defer to the user to maintain *Human Decision Authority*.\n"
        
    if "mcp" in content.lower():
        content += "\n\n### MCP Resources Integration\n"
        content += "You are connected to MCP servers: {mcp_servers_list}.\n"
        content += "These are part of the four extensibility mechanisms designed for *Capability Amplification*.\n"
        
    if "sleep" in content.lower() or "tick" in content.lower():
        content += "\n\n### Autonomous Loop Pacing\n"
        content += "Use the {sleep_tool_name} when waiting to avoid useless spin cycles.\n"
        
    enhanced += content
    enhanced += "\n\n" + ARCH_TENETS
    return enhanced

def main():
    count = 0
    for sdir in source_dirs:
        if not os.path.exists(sdir):
            continue
        category = os.path.basename(sdir)
        target_dir = os.path.join(target_base, category)
        os.makedirs(target_dir, exist_ok=True)
        
        md_files = glob.glob(f"{sdir}/*.md")
        for filepath in md_files:
            filename = os.path.basename(filepath)
            if filename.lower() == "readme.md":
                continue
                
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read()
                
            enhanced = enhance_text(text, filename, category)
            if not enhanced:
                continue
                
            target_path = os.path.join(target_dir, filename)
            
            with open(target_path, 'w', encoding='utf-8') as f:
                f.write(enhanced)
                
            count += 1
            
    print(f"Successfully processed {count} prompts in Pure MARKDOWN.")

if __name__ == '__main__':
    main()
