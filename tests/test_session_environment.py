"""
Phase 13: the session environment script (`app/utils/session_environment.py`), ported from
`utils/sessionEnvironment.ts` and `utils/shell/bashProvider.ts`.
"""

from __future__ import annotations

from app.utils.session_environment import (
    ENV_FILE_VAR,
    build_command,
    get_session_environment_script,
)
from app.workspace.local import LocalWorkspace


def test_the_script_is_the_trimmed_file_tddagents_env_file_names(tmp_path):
    script = tmp_path / "env.sh"
    script.write_text("\n  export A=1  \n\n")
    assert ENV_FILE_VAR == "TDDAGENTS_ENV_FILE"
    assert get_session_environment_script({ENV_FILE_VAR: str(script)}) == "export A=1"


def test_no_script_without_the_variable_a_file_or_content(tmp_path):
    (tmp_path / "empty.sh").write_text("  \n")
    assert get_session_environment_script({}) is None
    assert get_session_environment_script({ENV_FILE_VAR: ""}) is None
    assert get_session_environment_script({ENV_FILE_VAR: str(tmp_path / "absent.sh")}) is None
    assert get_session_environment_script({ENV_FILE_VAR: str(tmp_path / "empty.sh")}) is None
    assert get_session_environment_script({ENV_FILE_VAR: str(tmp_path)}) is None  # a directory


def test_the_command_runs_after_the_script_through_eval(tmp_path):
    script = tmp_path / "env.sh"
    script.write_text("export A=1")
    expected = "export A=1 && eval 'echo '\"'\"'a b'\"'\"'; ls'"
    assert build_command("echo 'a b'; ls", {ENV_FILE_VAR: str(script)}) == expected
    assert build_command("ls", {}) == "eval ls"


def test_a_workspace_command_sees_the_script_and_a_failing_script_stops_it(tmp_path):
    script = tmp_path / "env.sh"
    script.write_text('export GREETING="from the session env"')
    ws = LocalWorkspace(tmp_path / "p", env={ENV_FILE_VAR: str(script)})
    assert ws.execute('echo "$GREETING"; echo second').stdout == "from the session env\nsecond\n"
    script.write_text("false")
    res = ws.execute("echo never")
    assert res.exit_code == 1 and res.stdout == ""
