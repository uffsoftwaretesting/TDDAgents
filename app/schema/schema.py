from pydantic import BaseModel, Field

class FileToWrite(BaseModel):
    filepath: str = Field(
        description="The relative path to the file (e.g. 'src/core.py', 'tests/test_core.py', 'pytest.ini'). Directories are created automatically."
    )
    content: str = Field(
        description="The COMPLETE and up-to-date code. NEVER use ellipses (...). CRITICAL: Do not wrap the content in markdown blocks (```). Return only the file's plain text."
    )

class AgentAction(BaseModel):
    thoughts: str = Field(
        description="Your step-by-step reasoning about the module structure, separation of concerns, and how to make the tests pass."
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="Any external packages needed for execution and testing (e.g. ['numpy', 'pytest-asyncio', 'httpx'])."
    )
    bash_commands: list[str] = Field(
        default_factory=list,
        description="Terminal commands to run AFTER writing the files, but BEFORE the tests (e.g. ['export DATABASE_URL=...']). Do NOT put the test runner command here."
    )
    files_to_write: list[FileToWrite] = Field(
        description="The files that need to be created or modified to implement the components required by the current sub-requirement."
    )
