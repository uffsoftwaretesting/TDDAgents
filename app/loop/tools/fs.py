import re
from pathlib import Path
from typing import Any
from app.loop.tools.base import BuiltTool, build_tool
from app.loop.context import ToolContext
from app.loop.tools.types import ToolResult
import glob as python_glob

def get_prompt(filename: str, fallback: str) -> str:
    path = Path(__file__).parent.parent.parent / "prompts" / "tools" / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return fallback

def build_read_file_tool() -> BuiltTool:
    async def call_read(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        file_path = input_args.get("file_path")
        ws = getattr(context, "workspace", None)
        if not ws:
            return ToolResult(content="No workspace available", is_error=True)
            
        try:
            content = ws.read_file(file_path)
            
            offset = input_args.get("offset", 0)
            limit = input_args.get("limit", 2000)
            lines = content.splitlines(keepends=True)
            subset = lines[offset:offset+limit]
            
            out_content = "".join(subset)
            if not out_content.strip():
                return ToolResult(content=f"System Reminder: {file_path} exists but is empty or requested range is empty.")
                
            return ToolResult(content=out_content)
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

    return build_tool(
        name="ReadFile",
        prompt=get_prompt("read_file.md", "Reads a file."),
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"}
            },
            "required": ["file_path"]
        },
        description=lambda args: f"Read file: {args.get('file_path')}",
        is_read_only=lambda args: True,
        call=call_read
    )

def build_write_file_tool() -> BuiltTool:
    async def call_write(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        file_path = input_args.get("file_path")
        content = input_args.get("content", "")
        ws = getattr(context, "workspace", None)
        if not ws:
            return ToolResult(content="No workspace available", is_error=True)
            
        try:
            ws.write_file(file_path, content)
            return ToolResult(content=f"File {file_path} written successfully.")
        except Exception as e:
            return ToolResult(content=f"Error writing file: {e}", is_error=True)

    return build_tool(
        name="WriteFile",
        prompt=get_prompt("write_file.md", "Writes a file."),
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["file_path", "content"]
        },
        description=lambda args: f"Write file: {args.get('file_path')}",
        is_destructive=lambda args: True,
        is_implementation_writer=True,
        is_test_writer=True,
        call=call_write
    )

def build_edit_tool() -> BuiltTool:
    async def call_edit(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        file_path = input_args.get("path")
        old_string = input_args.get("old_string", "")
        new_string = input_args.get("new_string", "")
        replace_all = input_args.get("replace_all", False)
        
        ws = getattr(context, "workspace", None)
        if not ws:
            return ToolResult(content="No workspace available", is_error=True)
            
        try:
            content = ws.read_file(file_path)
            occurrences = content.count(old_string)
            
            if occurrences == 0:
                return ToolResult(content=f"No matches found for old_string in {file_path}.", is_error=True)
            elif occurrences > 1 and not replace_all:
                return ToolResult(content=f"Multiple matches ({occurrences}) found. old_string must be unique, or set replace_all to True.", is_error=True)
                
            new_content = content.replace(old_string, new_string)
            ws.write_file(file_path, new_content)
            return ToolResult(content=f"File {file_path} edited successfully.")
            
        except Exception as e:
            return ToolResult(content=f"Error editing file: {e}", is_error=True)

    return build_tool(
        name="Edit",
        prompt=get_prompt("edit.md", "Edits a file."),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"}
            },
            "required": ["path", "old_string", "new_string"]
        },
        description=lambda args: f"Edit file: {args.get('path')}",
        is_destructive=lambda args: True,
        is_implementation_writer=True,
        is_test_writer=True,
        call=call_edit
    )

def build_glob_tool() -> BuiltTool:
    async def call_glob(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        pattern = input_args.get("pattern", "**/*")
        ws = getattr(context, "workspace", None)
        if not ws:
            return ToolResult(content="No workspace available", is_error=True)
            
        try:
            cwd = Path(ws.root)
            matches = []
            for p in cwd.glob(pattern):
                if p.is_file():
                    matches.append(str(p.relative_to(cwd)))
            if not matches:
                return ToolResult(content="No matches found.")
            return ToolResult(content="\n".join(matches))
        except Exception as e:
            return ToolResult(content=f"Glob error: {e}", is_error=True)

    return build_tool(
        name="Glob",
        prompt=get_prompt("glob.md", "Finds files."),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"}
            },
            "required": ["pattern"]
        },
        description=lambda args: f"Glob: {args.get('pattern')}",
        is_read_only=lambda args: True,
        call=call_glob
    )

def build_grep_tool() -> BuiltTool:
    async def call_grep(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        pattern = input_args.get("pattern", "")
        include_glob = input_args.get("include_glob", "**/*")
        
        ws = getattr(context, "workspace", None)
        if not ws:
            return ToolResult(content="No workspace available", is_error=True)
            
        try:
            cwd = Path(ws.root)
            regex = re.compile(pattern)
            results = []
            
            for p in cwd.glob(include_glob):
                if p.is_file():
                    try:
                        content = p.read_text(encoding="utf-8")
                        lines = content.splitlines()
                        for i, line in enumerate(lines):
                            if regex.search(line):
                                rel_path = p.relative_to(cwd)
                                results.append(f"{rel_path}:{i+1}: {line.strip()}")
                    except UnicodeDecodeError:
                        pass
                        
            if not results:
                return ToolResult(content="No matches found.")
            return ToolResult(content="\n".join(results))
        except Exception as e:
            return ToolResult(content=f"Grep error: {e}", is_error=True)

    return build_tool(
        name="Grep",
        prompt=get_prompt("grep.md", "Searches file contents."),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "include_glob": {"type": "string"}
            },
            "required": ["pattern"]
        },
        description=lambda args: f"Grep: {args.get('pattern')}",
        is_read_only=lambda args: True,
        call=call_grep
    )
