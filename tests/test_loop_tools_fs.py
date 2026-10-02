import pytest
from app.loop.tools.fs import build_read_file_tool, build_write_file_tool, build_edit_tool, build_glob_tool, build_grep_tool
from app.loop.context import ToolContext
from app.workspace.local import LocalWorkspace
from dataclasses import dataclass

@dataclass
class MockState:
    pass

@pytest.fixture
def context(tmp_path):
    ws = LocalWorkspace(str(tmp_path))
    return ToolContext(
        workspace=ws,
        cancel=lambda: None,
        get_app_state=lambda: MockState(),
        set_app_state=lambda x: None
    )

@pytest.mark.asyncio
async def test_write_and_read_file(context, tmp_path):
    write_tool = build_write_file_tool()
    read_tool = build_read_file_tool()
    
    file_path = str(tmp_path / "hello.txt")
    
    # Write
    res_w = await write_tool.call({"file_path": file_path, "content": "Hello World\nLine 2"}, context)
    assert not res_w.is_error
    
    # Read
    res_r = await read_tool.call({"file_path": file_path}, context)
    assert not res_r.is_error
    assert "Hello World\nLine 2" in res_r.content

@pytest.mark.asyncio
async def test_edit_file_exact_match(context, tmp_path):
    write_tool = build_write_file_tool()
    edit_tool = build_edit_tool()
    
    file_path = str(tmp_path / "edit.txt")
    await write_tool.call({"file_path": file_path, "content": "A\nB\nC\nD\n"}, context)
    
    # Edit B -> X
    res_e = await edit_tool.call({"path": file_path, "old_string": "B\n", "new_string": "X\n"}, context)
    assert not res_e.is_error
    
    # Verify
    read_tool = build_read_file_tool()
    res_r = await read_tool.call({"file_path": file_path}, context)
    assert "A\nX\nC\nD\n" in res_r.content

@pytest.mark.asyncio
async def test_edit_fails_if_no_exact_match(context, tmp_path):
    write_tool = build_write_file_tool()
    edit_tool = build_edit_tool()
    
    file_path = str(tmp_path / "edit_fail.txt")
    await write_tool.call({"file_path": file_path, "content": "A\nB\nC\n"}, context)
    
    # Edit something not there
    res_e = await edit_tool.call({"path": file_path, "old_string": "X", "new_string": "Y"}, context)
    assert res_e.is_error
    assert "No matches found" in res_e.content

@pytest.mark.asyncio
async def test_glob_and_grep(context, tmp_path):
    write_tool = build_write_file_tool()
    file_path = str(tmp_path / "src" / "test.py")
    await write_tool.call({"file_path": file_path, "content": "def foo():\n    print('hello glob')\n"}, context)
    
    glob_tool = build_glob_tool()
    res_g = await glob_tool.call({"pattern": "**/*.py"}, context)
    assert not res_g.is_error
    assert "src/test.py" in res_g.content
    
    grep_tool = build_grep_tool()
    res_gr = await grep_tool.call({"pattern": "hello glob"}, context)
    assert not res_gr.is_error
    assert "src/test.py" in res_gr.content
