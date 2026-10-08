"""
`app/loop/permissions/bash_read_only.py` and `bash_read_only_commands.py`: ports of
claude-code's `readOnlyValidation.ts` and `utils/shell/readOnlyCommandValidation.ts`.
Attack examples are the ones upstream documents next to each defense.
"""

import os

import pytest

from app.loop.permissions import bash_read_only as ro
from app.loop.permissions import bash_read_only_commands as roc
from app.loop.permissions.bash_read_only import (
    check_read_only_constraints,
    command_writes_to_git_internal_paths,
    contains_unquoted_expansion,
    extract_write_paths_from_subcommand,
    is_bash_read_only,
    is_command_read_only,
    is_command_safe_via_flag_parsing,
    is_current_directory_bare_git_repo,
    is_git_internal_path,
    make_regex_for_safe_command,
)
from app.loop.permissions.bash_read_only_commands import (
    CommandConfig,
    contains_vulnerable_unc_path,
    get_command_allowlist,
    gh_is_dangerous_callback,
    validate_flag_argument,
    validate_flags,
)


# ── tables ───────────────────────────────────────────────────────────────────

def test_table_shape():
    allow = roc.COMMAND_ALLOWLIST
    assert len(allow) == 51
    assert len(roc.ANT_ONLY_COMMAND_ALLOWLIST) == 23
    assert sum(len(c.safe_flags) for c in allow.values()) == 1329
    assert list(allow)[:3] == ["xargs", "git diff", "git log"]
    assert allow["fd"].safe_flags == roc.FD_SAFE_FLAGS and allow["fdfind"].safe_flags == roc.FD_SAFE_FLAGS
    assert allow["pyright"].respects_double_dash is False and allow["base64"].respects_double_dash is False
    assert allow["grep"].respects_double_dash is True
    assert allow["hostname"].regex is roc.REGEXES["hostname"]
    assert {k for k, c in allow.items() if c.callback} == {
        "git reflog", "git remote show", "git remote", "git tag", "git branch", "pyright", "sed", "ps", "date",
        "lsof", "tput"}
    assert all(c.callback is gh_is_dangerous_callback for k, c in roc.ANT_ONLY_COMMAND_ALLOWLIST.items()
               if k.startswith("gh ") and not k.startswith("gh search"))
    assert "-x" not in roc.FD_SAFE_FLAGS and "--exec" not in roc.FD_SAFE_FLAGS
    assert roc.EXTERNAL_READONLY_COMMANDS == ("docker ps", "docker images")
    assert roc.SAFE_TARGET_COMMANDS_FOR_XARGS == ("echo", "printf", "wc", "grep", "head", "tail")


def test_get_command_allowlist(monkeypatch):
    assert "gh pr view" not in get_command_allowlist({})
    assert "gh pr view" in get_command_allowlist({"USER_TYPE": "ant"})
    assert "xargs" in get_command_allowlist({})
    monkeypatch.setattr(roc, "get_platform", lambda: "windows")
    assert "xargs" not in get_command_allowlist({})
    monkeypatch.setenv("USER_TYPE", "ant")
    assert "aki" in get_command_allowlist()


def test_get_platform(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "win32")
    assert roc.get_platform() == "windows"
    monkeypatch.setattr(sys, "platform", "linux")
    assert roc.get_platform() == "linux"


# ── validate_flag_argument / validate_flags ──────────────────────────────────

@pytest.mark.parametrize("value, kind, ok", [
    ("10", "number", True), ("1a", "number", False), ("", "number", False),
    ("anything", "string", True), ("", "string", True),
    ("x", "char", True), ("xy", "char", False),
    ("{}", "{}", True), ("x", "{}", False), ("EOF", "EOF", True), ("eof", "EOF", False),
    ("x", "none", False), ("x", "bogus", False),
])
def test_validate_flag_argument(value, kind, ok):
    assert validate_flag_argument(value, kind) is ok


CFG = CommandConfig(safe_flags={"-a": "none", "-b": "none", "-n": "number", "-s": "string", "-I": "{}"})


@pytest.mark.parametrize("tokens, ok", [
    (["cmd", "-a", "file"], True),
    (["cmd", "-ab"], True),                  # bundled no-arg flags
    (["cmd", "-aI"], False),                 # bundled arg-taking flag
    (["cmd", "-z"], False),                  # unknown
    (["cmd", "--zz"], False),
    (["cmd", "-n", "5"], True),
    (["cmd", "-n", "x"], False),
    (["cmd", "-n"], False),                  # missing argument
    (["cmd", "-n", "-a"], False),            # argument looks like a flag
    (["cmd", "-n=5"], True),
    (["cmd", "-n="], False),                 # empty inline value
    (["cmd", "-a=1"], False),                # none-type with a value
    (["cmd", "-s", "-x"], False),            # string value starting with '-'
    (["cmd", "--", "-z"], True),             # end of options
    (["cmd", "", "-a"], True),
    (["cmd", "=x"], True),                   # not a flag token
    (["cmd", "-"], True),                    # bare dash is positional
])
def test_validate_flags(tokens, ok):
    assert validate_flags(tokens, 1, CFG) is ok


def test_validate_flags_double_dash_not_respected():
    cfg = CommandConfig(safe_flags={"-a": "none"}, respects_double_dash=False)
    assert validate_flags(["pyright", "--", "--createstub"], 1, cfg) is False
    assert validate_flags(["pyright", "--", "-a"], 1, cfg) is True


def test_validate_flags_git_and_grep_specials():
    git = CommandConfig(safe_flags={"--sort": "string"})
    assert validate_flags(["git", "-5"], 1, git, command_name="git") is True
    assert validate_flags(["git", "-5"], 1, git, command_name="other") is False
    # a space-separated value that looks like a flag is "missing argument", as upstream
    assert validate_flags(["git", "--sort", "-refname"], 1, git, command_name="git") is False
    assert validate_flags(["git", "--sort=-refname"], 1, git, command_name="git") is True
    assert validate_flags(["git", "--sort=-1"], 1, git, command_name="git") is False
    assert validate_flags(["x", "--sort=-refname"], 1, git, command_name="x") is False
    grep = CommandConfig(safe_flags={"-A": "number", "-C": "string", "-c": "none", "-o": "none"})
    assert validate_flags(["grep", "-A20"], 1, grep, command_name="grep") is True
    assert validate_flags(["grep", "-C3"], 1, grep, command_name="rg") is True
    assert validate_flags(["grep", "-A20"], 1, grep, command_name="cat") is False
    assert validate_flags(["grep", "-Ax"], 1, grep, command_name="grep") is False
    assert validate_flags(["grep", "-co"], 1, grep, command_name="grep") is True


def test_validate_flags_xargs_target():
    cfg = roc.COMMAND_ALLOWLIST["xargs"]
    targets = roc.SAFE_TARGET_COMMANDS_FOR_XARGS
    assert validate_flags(["xargs", "grep", "x"], 1, cfg, command_name="xargs", xargs_target_commands=targets)
    assert validate_flags(["xargs", "--", "head"], 1, cfg, command_name="xargs", xargs_target_commands=targets)
    assert not validate_flags(["xargs", "sh", "-c", "id"], 1, cfg, command_name="xargs",
                              xargs_target_commands=targets)
    assert not validate_flags(["xargs", "--"], 1, cfg, command_name="xargs", xargs_target_commands=targets)


# ── callbacks ────────────────────────────────────────────────────────────────

CB = roc.CALLBACKS


@pytest.mark.parametrize("name, args, dangerous", [
    ("git reflog", [], False), ("git reflog", ["show"], False), ("git reflog", ["-n", "expire"], True),
    ("git reflog", ["delete"], True), ("git reflog", ["exists"], True), ("git reflog", ["", "show"], False),
    ("git remote show", ["origin"], False), ("git remote show", ["-n", "origin"], False),
    ("git remote show", [], True), ("git remote show", ["a", "b"], True), ("git remote show", ["x;y"], True),
    ("git remote", [], False), ("git remote", ["-v"], False), ("git remote", ["--verbose"], False),
    ("git remote", ["add"], True),
    ("git tag", [], False), ("git tag", ["v1"], True), ("git tag", ["-l", "v*"], False),
    ("git tag", ["--list", "v*"], False), ("git tag", ["-nl", "x"], False), ("git tag", ["--sort", "x", "y"], True),
    ("git tag", ["--sort=x", "y"], True), ("git tag", ["--", "-l"], True), ("git tag", ["", "-l", "x"], False),
    ("git tag", ["-n", "5"], False), ("git tag", ["-a", "v"], True),
    ("git branch", [], False), ("git branch", ["new"], True), ("git branch", ["-l", "x"], False),
    ("git branch", ["--merged", "main"], False), ("git branch", ["--contains", "c", "x"], True),
    ("git branch", ["--sort=x", "y"], True), ("git branch", ["--", "x"], True), ("git branch", ["-vl", "x"], False),
    ("git branch", ["", "-a"], False), ("git branch", ["--list=x", "y"], True),
    ("pyright", ["--watch"], True), ("pyright", ["-w"], True), ("pyright", ["src"], False),
    ("ps", ["aux"], False), ("ps", ["axe"], True), ("ps", ["-e"], False), ("ps", ["e"], True), ("ps", ["a1e"], False),
    ("date", ["+%s"], False), ("date", ["-d", "now"], False), ("date", ["--date=now"], False),
    ("date", ["010203042026"], True), ("date", ["-u"], False), ("date", ["-r", "f", "+%s"], False),
    ("date", ["--iso-8601", "x"], False),
    ("lsof", ["+m"], True), ("lsof", ["+mfoo"], True), ("lsof", ["-p", "1"], False),
    ("tput", ["cols"], False), ("tput", ["-S"], True), ("tput", ["-xS"], True), ("tput", ["clear"], True),
    ("tput", ["-T", "xterm", "cols"], False), ("tput", ["--", "-S"], False), ("tput", ["--", "reset"], True),
    ("tput", ["--long"], False),
])
def test_callbacks(name, args, dangerous):
    assert CB[name]("cmd", args) is dangerous


def test_sed_callback_uses_allowlist():
    assert CB["sed"]("sed -n '1p' f", []) is False
    assert CB["sed"]("sed 's/a/b/w x'", []) is True


@pytest.mark.parametrize("args, dangerous", [
    ([], False), (["123"], False), (["owner/repo"], False), (["host/owner/repo"], True),
    (["https://x"], True), (["a@b"], True), (["--repo", "o/r"], False), (["--repo=h/o/r"], True),
    (["--repo="], False), (["-R"], False), ([""], False), (["plain"], False),
])
def test_gh_is_dangerous_callback(args, dangerous):
    assert gh_is_dangerous_callback("gh", args) is dangerous


def test_hostname_regex():
    rx = roc.REGEXES["hostname"]
    assert rx.search("hostname") and rx.search("hostname -f") and rx.search("hostname --fqdn ")
    assert not rx.search("hostname newname") and not rx.search("hostname -f;")
    assert rx.search("hostname\n")  # JS `\s*` consumes the newline too


# ── UNC ──────────────────────────────────────────────────────────────────────

def test_unc_paths_only_matter_on_windows(monkeypatch):
    assert contains_vulnerable_unc_path("\\\\server\\share") is False
    monkeypatch.setattr(roc, "get_platform", lambda: "windows")
    for bad in ("\\\\server\\share", "//server/share", "/\\\\server", "\\\\/server", "x@SSL@443", "x@443@SSL",
                "\\\\h\\DavWWWRoot\\x", "\\\\10.0.0.1\\x", "//[::1]/x", "\\\\[::1]\\x", "//1.2.3.4/x",
                "\\\\server@ssl\\x"):
        assert contains_vulnerable_unc_path(bad) is True, bad
    for ok in ("https://example.com/a", "C:\\Users", "/usr/bin", "a/b"):
        assert contains_vulnerable_unc_path(ok) is False, ok


# ── is_command_safe_via_flag_parsing ─────────────────────────────────────────

@pytest.mark.parametrize("command, safe", [
    ("git status", True),
    ("git log --oneline -n 5", True),
    ("git log -5", True),
    ("git diff --output=/tmp/pwned", False),            # unknown flag
    ('git diff "$Z--output=/tmp/pwned"', False),        # $ in a token
    ("git diff {@'{'0},--output=/tmp/pwned}", False),   # brace expansion
    ("git diff stash@{0}", True),
    ("git ls-remote origin", True),
    ("git ls-remote https://evil", False),
    ("git ls-remote git@github.com:x/y", False),
    ("git ls-remote $X", False),
    ("rg . \"$Z--pre=bash\" FILE", False),
    ("rg --pre=bash x", False),
    ("rg -A3 foo", True),
    ("grep -r foo .", True),
    ("grep 'a\nb' f", False),                           # newline in grep pattern
    ("ps ax\"$Z\"e", False),
    ("ps aux", True),
    ("xargs -rI echo sh -c id", False),
    ("xargs -E= EOF echo foo", False),
    ("xargs grep x", True),
    ("hostname -f", True),
    ("hostname newname", False),                        # regex rejects
    ("sort -o out f", False),
    ("fd -x rm", False),
    ("pyright -- --createstub os", False),
    ("cat f", False),                                   # not in the flag allowlist
    ("ls | wc", False),                                 # operators
    ("ls *.py", False),                                 # glob pattern token: ls not in allowlist
    ("tree -L 2", True),
    ("tree -o out", False),
    ("file *.txt", True),                               # glob tokens are patterns
    ("echo ${}", False),                                # unparseable
    ("", False),
    ("sort `id`", False),                               # backtick without a regex
    ("date +%s", True),
])
def test_is_command_safe_via_flag_parsing(command, safe):
    assert is_command_safe_via_flag_parsing(command) is safe


def test_flag_parsing_ignores_ls_remote_flags_and_empty_tokens():
    assert is_command_safe_via_flag_parsing("git ls-remote --heads") is True
    assert is_command_safe_via_flag_parsing("git status ''") is True


# ── regex allowlist ──────────────────────────────────────────────────────────

def test_make_regex_for_safe_command():
    rx = make_regex_for_safe_command("cat")
    assert rx.search("cat") and rx.search("cat f") and rx.search("cat\tf")
    for bad in ("catx", "cat f > x", "cat $(id)", "cat f; ls", "cat f\n", "cat `id`", "cat {a}", "cat f & ls"):
        assert not rx.search(bad), bad


@pytest.mark.parametrize("command, ok", [
    ("cat file.txt", True),
    ("head -n 5 f", True),
    ("wc -l f 2>&1", True),
    ("echo hello", True),
    ("echo 'a\nb'", True),
    ('echo "x $HOME"', False),
    ("echo $HOME", False),
    ("echo a > f", False),
    ("pwd", True), ("pwd x", False), ("whoami", True),
    ("node -v", True), ("node -v --run x", False), ("python3 --version", True), ("python --version", True),
    ("node --version", True),
    ("history", True), ("history 5", True), ("history -w f", False),
    ("alias", True), ("arch", True), ("arch -h", True), ("arch x", False),
    ("ip addr", True), ("ip link", False),
    ("ifconfig", True), ("ifconfig eth0", True), ("ifconfig eth0 up", False),
    ("uniq -c", True), ("uniq -f 2", True), ("uniq in out", False), ("uniq --skip-chars=0$_", False),
    ("jq . f.json", True), ("jq -r '.a' f", True), ("jq -f prog f", False), ("jq --rawfile a f .", False),
    ("jq 'env'", False), ("jq '$ENV'", False), ("jq '.a | `x`'", False),
    ("cd src", True), ("cd 'my dir'", True), ("cd $(x)", False),
    ("ls -la", True), ("ls dir", True), ("ls; rm x", False),
    ("find . -name x", True), ("find . -delete", False), ("find . -exec rm {} \\;", False),
    ("find . \\( -name a \\)", True), ("find . -fprint out", False),
    ("claude -h", True), ("claude --help", True),
    ("docker ps", True), ("docker images", True),
    ("git -c core.fsmonitor=x status", False),
    ("ls git -c x", False),
    ("ls git --exec-path=x", False),
    ("ls git --config-env=x", False),
    ("rm x", False),
    ("ls *", False),                      # unquoted glob
    ("ls '*'", True),
    ("ls \"*\"", True),
    ("ls \\*", True),
    ("ls '\\' *", False),                 # quote-tracker desync attack
    ("ls \"$X\"", False),
    ("ls '$X'", False),                  # the ls regex excludes `$` even when quoted
    ("sleep 1", True),
    ("true", True),
])
def test_is_command_read_only(command, ok):
    assert is_command_read_only(command) is ok


@pytest.mark.parametrize("command, expansion", [
    ("ls", False), ("ls *", True), ("ls ?", True), ("ls [a]", True), ("ls ']'", False),
    ("echo $X", True), ("echo \"$X\"", True), ("echo '$X'", False), ("echo $", False), ("echo $(", False),
    ("echo \\$X", False), ("echo \"*\"", False), ("echo $_", True), ("echo $1", True), ("echo $-", True),
])
def test_contains_unquoted_expansion(command, expansion):
    assert contains_unquoted_expansion(command) is expansion


def test_is_command_read_only_unc(monkeypatch):
    monkeypatch.setattr(ro, "contains_vulnerable_unc_path", lambda s: True)
    assert is_command_read_only("cat f") is False


# ── git hardening ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, internal", [
    ("HEAD", True), ("./HEAD", True), ("/HEAD", True), ("objects", True), ("objects/x", True), ("refs/heads", True),
    ("hooks/pre-commit", True), ("HEADS", False), ("src/hooks", False), ("objectsx", False),
])
def test_is_git_internal_path(path, internal):
    assert is_git_internal_path(path) is internal


@pytest.mark.parametrize("sub, paths", [
    ("mkdir -p hooks objects", ["hooks", "objects"]),
    ("touch HEAD", ["HEAD"]),
    ("cp a hooks/x", ["a", "hooks/x"]),
    ("rm HEAD", []),            # deletion, not creation
    ("sed -i x f", []),
    ("cat HEAD", []),           # read
    ("echo ${}", []),
    ("", []),
    ("unknowncmd x", []),
])
def test_extract_write_paths_from_subcommand(sub, paths):
    assert extract_write_paths_from_subcommand(sub) == paths


def test_command_writes_to_git_internal_paths():
    assert command_writes_to_git_internal_paths("mkdir -p hooks && git status") is True
    # upstream quirk: splitCommand_DEPRECATED already stripped the static redirect target
    assert command_writes_to_git_internal_paths("echo x > hooks/pre-commit && git status") is False
    assert command_writes_to_git_internal_paths("echo x > $D/hooks && git status") is False
    assert command_writes_to_git_internal_paths("mkdir src && git status") is False


def test_bare_git_repo_detection(tmp_path):
    cwd = str(tmp_path)
    assert is_current_directory_bare_git_repo(cwd) is False
    (tmp_path / "HEAD").write_text("x")
    assert is_current_directory_bare_git_repo(cwd) is True
    (tmp_path / ".git").mkdir()
    assert is_current_directory_bare_git_repo(cwd) is True          # .git without HEAD
    (tmp_path / ".git" / "HEAD").write_text("ref")
    assert is_current_directory_bare_git_repo(cwd) is False
    other = tmp_path / "o"
    other.mkdir()
    (other / ".git").write_text("gitdir: x")                       # worktree file
    (other / "objects").mkdir()
    assert is_current_directory_bare_git_repo(str(other)) is False
    for marker in ("objects", "refs"):
        d = tmp_path / marker
        bare = tmp_path / ("b_" + marker)
        bare.mkdir()
        (bare / marker).mkdir()
        assert is_current_directory_bare_git_repo(str(bare)) is True, d
    weird = tmp_path / "w"
    (weird / ".git" / "HEAD").mkdir(parents=True)                  # HEAD as a directory
    (weird / "refs").mkdir()
    assert is_current_directory_bare_git_repo(str(weird)) is True


# ── check_read_only_constraints ──────────────────────────────────────────────

def test_check_read_only_constraints(tmp_path, monkeypatch):
    cwd = str(tmp_path)
    allow = check_read_only_constraints("ls && cat f", False, cwd)
    assert (allow.behavior, allow.updated_input) == ("allow", {"command": "ls && cat f"})

    def msg(command, has_cd=False):
        r = check_read_only_constraints(command, has_cd, cwd)
        return str(r.behavior), r.message

    assert msg("echo ${}") == ("passthrough", "Command cannot be parsed, requires further permission checks")
    assert msg("echo $(id)") == ("passthrough", ro.NOT_READ_ONLY)
    assert msg("cd x && git status", True) == (
        "passthrough", "Compound commands with cd and git require permission checks for enhanced security")
    assert msg("mkdir -p hooks && git status") == (
        "passthrough",
        "Compound commands that create git internal files and run git require permission checks for enhanced "
        "security")
    assert msg("rm x") == ("passthrough", ro.NOT_READ_ONLY)
    (tmp_path / "HEAD").write_text("x")
    assert msg("git status") == (
        "passthrough",
        "Git commands in directories with bare repository structure require permission checks for enhanced "
        "security")
    assert msg("ls") == ("allow", "")
    monkeypatch.setattr(ro, "contains_vulnerable_unc_path", lambda s: True)
    assert msg("ls") == ("ask", "Command contains Windows UNC path that could be vulnerable to WebDAV attacks")


def test_check_read_only_defaults_cwd_to_process_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "HEAD").write_text("x")
    assert check_read_only_constraints("git status", False).behavior == "passthrough"
    assert check_read_only_constraints("git status", False, str(tmp_path / "..")).behavior in ("allow", "passthrough")


def test_is_bash_read_only(tmp_path):
    cwd = str(tmp_path)
    assert is_bash_read_only({"command": "ls -la"}, cwd) is True
    assert is_bash_read_only({"command": "pip install x"}, cwd) is False
    assert is_bash_read_only({}, cwd) is True  # `every` over no subcommands, as upstream
    assert is_bash_read_only({"command": "cd x && git log"}, cwd) is False


def test_readonly_vocabularies():
    assert "cat" in ro.READONLY_COMMANDS and "docker ps" in ro.READONLY_COMMANDS
    assert len(ro.READONLY_COMMANDS) == 50
    assert ro.NON_CREATING_WRITE_COMMANDS == frozenset({"rm", "rmdir", "sed"})
    assert os.path.basename(ro.__file__) == "bash_read_only.py"


# ── direct coverage for import-time and callback branches ────────────────────

def test_configs_builds_command_configs():
    rx = roc.REGEXES["hostname"]

    def cb(raw, args):
        return False

    built = roc._configs({
        "a": {"safe_flags": {"-x": "none"}},
        "b": {"safe_flags": {}, "regex": rx, "callback": cb, "respects_double_dash": False},
    })
    assert built == {
        "a": CommandConfig(safe_flags={"-x": "none"}, regex=None, callback=None, respects_double_dash=True),
        "b": CommandConfig(safe_flags={}, regex=rx, callback=cb, respects_double_dash=False),
    }
    source = {"-y": "none"}
    copy = roc._configs({"c": {"safe_flags": source}})["c"]
    source["-z"] = "none"
    assert dict(copy.safe_flags) == {"-y": "none"}          # tables are copied, not aliased


@pytest.mark.parametrize("args, dangerous", [
    (["-a"], False), (["-r"], False), (["--all"], False), (["-v", "-a"], False),
    (["--merged"], False), (["--merged", "main"], False), (["--no-merged", "x"], False),
    (["--points-at", "HEAD"], False), (["--points-at", "HEAD", "x"], True),
    (["--sort", "-committerdate"], False), (["--sort", "x", "y"], True),
    (["--contains"], False), (["--format=%(refname)"], False), (["--format=%(x)", "y"], True),
    (["-l"], False), (["-l", "a", "b"], False), (["--list", "a"], False),
    (["-al", "x"], False), (["--l", "x"], True), (["-l=x", "y"], True), (["-x", "y"], True),
    (["-d", "x"], True), (["-D", "x"], True), (["-m", "a", "b"], True), (["--", "x"], True),
    (["--", "--", "x"], True), (["--merged", "--", "x"], True),
])
def test_git_branch_callback_dense(args, dangerous):
    assert CB["git branch"]("git branch", args) is dangerous


@pytest.mark.parametrize("args, dangerous", [
    (["-l"], False), (["-l", "v1*"], False), (["--list"], False), (["-n5", "-l", "v"], False),
    (["--contains", "c"], False), (["--contains", "c", "v"], True), (["--merged", "m", "v"], True),
    (["--points-at", "h"], False), (["--format", "%(x)"], False), (["--format=%(x)", "v"], True),
    (["-n", "3", "-l", "v"], False), (["-al", "v"], False), (["--l", "v"], True), (["-l=x", "v"], True),
    (["-d", "v1"], True), (["-a", "-m", "msg", "v1"], True), (["v1", "-l"], True),
    (["--", "-l"], True), (["--", "--", "x"], True),
])
def test_git_tag_callback_dense(args, dangerous):
    assert CB["git tag"]("git tag", args) is dangerous


@pytest.mark.parametrize("command, safe", [
    ("git branch", True), ("git branch -a", True), ("git branch new", False), ("git branch -l 'feat*'", True),
    ("git tag", True), ("git tag v1", False), ("git tag -l", True),
    ("git reflog", True), ("git reflog expire", False),
    ("git remote", True), ("git remote -v", True), ("git remote add x y", False),
    ("git remote show origin", True), ("git remote show a b", False),
    ("lsof -p 1", True), ("lsof +m", False),
    ("tput cols", True), ("tput reset", False),
    ("date +%Y", True), ("date 0101", False),
    ("pyright src", True), ("pyright --watch", False),
    ("sed -n 1p f", True), ("sed -i 's/a/b/' f", False),
    ("docker logs c", True), ("docker inspect c", True), ("docker run x", False),
    ("rg -n foo", True), ("rg --pre x foo", False),
])
def test_allowlist_callbacks_end_to_end(command, safe):
    assert is_command_safe_via_flag_parsing(command) is safe


def test_gh_commands_only_for_ant(monkeypatch):
    assert is_command_safe_via_flag_parsing("gh pr list") is False
    monkeypatch.setenv("USER_TYPE", "ant")
    assert is_command_safe_via_flag_parsing("gh pr list") is True
    assert is_command_safe_via_flag_parsing("gh pr view https://evil/x") is False
    assert is_command_safe_via_flag_parsing("gh search repos foo") is True
