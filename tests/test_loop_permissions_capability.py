"""
Tests for per-input capability determination (Part C4).
"""

from __future__ import annotations

import pytest

from app.loop.permissions.capability import (
    COMMAND_WRAPPERS,
    GIT_READ_ONLY_SUBCOMMANDS,
    READ_ONLY_BASE_COMMANDS,
    bash_is_read_only,
    has_output_redirection,
    is_bash_command_read_only,
    is_single_subcommand_read_only,
    split_shell_commands,
)


class TestShellCommandParsing:
    def test_split_shell_commands_simple(self):
        assert split_shell_commands("ls -la") == ["ls -la"]

    def test_split_shell_commands_chained_operators(self):
        assert split_shell_commands("ls; pwd") == ["ls", "pwd"]
        assert split_shell_commands("git status && git branch") == ["git status", "git branch"]
        assert split_shell_commands("test -f x || echo no") == ["test -f x", "echo no"]
        assert split_shell_commands("cat file | grep pattern") == ["cat file", "grep pattern"]

    def test_split_shell_commands_preserves_quoted_operators(self):
        assert split_shell_commands("echo 'a; b'") == ["echo 'a; b'"]
        assert split_shell_commands('echo "a && b"') == ['echo "a && b"']
        assert split_shell_commands("echo 'a | b'") == ["echo 'a | b'"]

    def test_has_output_redirection(self):
        assert has_output_redirection("ls") is False
        assert has_output_redirection("echo hello > out.txt") is True
        assert has_output_redirection("echo hello >> out.txt") is True
        assert has_output_redirection("echo 'hello > out.txt'") is False
        assert has_output_redirection('echo "hello > out.txt"') is False


class TestBashCommandReadOnly:
    def test_read_only_inspection_commands(self):
        assert is_bash_command_read_only("ls") is True
        assert is_bash_command_read_only("ls -la /tmp") is True
        assert is_bash_command_read_only("cat file.txt") is True
        assert is_bash_command_read_only("head -n 20 src/main.py") is True
        assert is_bash_command_read_only("tail -f log.txt") is True
        assert is_bash_command_read_only("wc -l file.txt") is True
        assert is_bash_command_read_only("pwd") is True
        assert is_bash_command_read_only("which pytest") is True
        assert is_bash_command_read_only("uname -a") is True
        assert is_bash_command_read_only("whoami") is True
        assert is_bash_command_read_only("grep 'pattern' file.txt") is True
        assert is_bash_command_read_only("rg 'def ' app/") is True
        assert is_bash_command_read_only("diff a.txt b.txt") is True

    def test_git_read_only_commands(self):
        assert is_bash_command_read_only("git status") is True
        assert is_bash_command_read_only("git diff") is True
        assert is_bash_command_read_only("git log -n 5") is True
        assert is_bash_command_read_only("git show HEAD") is True
        assert is_bash_command_read_only("git branch -a") is True
        assert is_bash_command_read_only("git rev-parse HEAD") is True
        assert is_bash_command_read_only("git ls-files") is True
        assert is_bash_command_read_only("git blame file.py") is True
        assert is_bash_command_read_only("git remote -v") is True

    def test_git_mutating_commands_are_not_read_only(self):
        assert is_bash_command_read_only("git commit -m 'feat'") is False
        assert is_bash_command_read_only("git push origin main") is False
        assert is_bash_command_read_only("git checkout main") is False
        assert is_bash_command_read_only("git reset --hard") is False
        assert is_bash_command_read_only("git rebase master") is False
        assert is_bash_command_read_only("git merge feature") is False
        assert is_bash_command_read_only("git add file.py") is False
        assert is_bash_command_read_only("git clean -fd") is False

    def test_package_managers_and_mutating_commands(self):
        # Package management installs are state mutations
        assert is_bash_command_read_only("pip install pytest") is False
        assert is_bash_command_read_only("pip3 install -r requirements.txt") is False
        assert is_bash_command_read_only("npm install") is False
        assert is_bash_command_read_only("yarn add react") is False
        assert is_bash_command_read_only("apt-get install curl") is False

        # File modifications
        assert is_bash_command_read_only("rm -rf /tmp/foo") is False
        assert is_bash_command_read_only("mkdir -p /tmp/bar") is False
        assert is_bash_command_read_only("touch file.txt") is False
        assert is_bash_command_read_only("mv a b") is False
        assert is_bash_command_read_only("cp a b") is False
        assert is_bash_command_read_only("chmod +x script.sh") is False

    def test_compound_commands_fail_if_any_is_mutating(self):
        assert is_bash_command_read_only("ls && pwd") is True
        assert is_bash_command_read_only("ls && pip install pytest") is False
        assert is_bash_command_read_only("pip install pytest && ls") is False
        assert is_bash_command_read_only("cat file | grep foo") is True
        assert is_bash_command_read_only("cat file | rm -rf") is False

    def test_redirections_are_never_read_only(self):
        assert is_bash_command_read_only("ls > output.txt") is False
        assert is_bash_command_read_only("cat file >> log.txt") is False

    def test_command_wrappers(self):
        assert is_bash_command_read_only("timeout 10s ls -la") is True
        assert is_bash_command_read_only("env FOO=bar timeout 5s git status") is True
        assert is_bash_command_read_only("timeout 10s rm -rf foo") is False

    def test_bash_is_read_only_predicate(self):
        assert bash_is_read_only({"command": "ls -la"}) is True
        assert bash_is_read_only({"command": "pip install foo"}) is False
        assert bash_is_read_only({"cmd": "git status"}) is True
        assert bash_is_read_only({}) is True

    @pytest.mark.parametrize("cmd", sorted(READ_ONLY_BASE_COMMANDS))
    def test_all_base_read_only_commands(self, cmd):
        assert is_bash_command_read_only(cmd) is True
        assert is_bash_command_read_only(f"{cmd} --help") is True

    @pytest.mark.parametrize("subcmd", sorted(GIT_READ_ONLY_SUBCOMMANDS))
    def test_all_git_read_only_subcommands(self, subcmd):
        assert is_bash_command_read_only(f"git {subcmd}") is True
        assert is_bash_command_read_only(f"git -C /tmp {subcmd}") is True
        assert is_bash_command_read_only(f"git -c foo=bar {subcmd}") is True

    @pytest.mark.parametrize("wrapper", sorted(COMMAND_WRAPPERS))
    def test_all_command_wrappers_parametrized(self, wrapper):
        if wrapper == "timeout":
            assert is_bash_command_read_only("timeout 5s ls") is True
            assert is_bash_command_read_only("timeout 10 ls") is True
            assert is_bash_command_read_only("timeout -s 9 5s ls") is True
            assert is_bash_command_read_only("timeout 5s rm foo") is False
        elif wrapper == "env":
            assert is_bash_command_read_only("env -i ls") is True
            assert is_bash_command_read_only("env -u FOO ls") is True
            assert is_bash_command_read_only("env FOO=bar ls") is True
            assert is_bash_command_read_only("env FOO=bar rm foo") is False
        else:
            assert is_bash_command_read_only(f"{wrapper} ls") is True
            assert is_bash_command_read_only(f"{wrapper} rm foo") is False

    def test_sed_read_only_and_in_place(self):
        assert is_bash_command_read_only("sed 's/foo/bar/g' file.txt") is True
        assert is_bash_command_read_only("sed -e 's/foo/bar/g' file.txt") is True
        assert is_bash_command_read_only("sed -n 'p' file.txt") is True
        assert is_bash_command_read_only("sed -i 's/foo/bar/g' file.txt") is False
        assert is_bash_command_read_only("sed -i.bak 's/foo/bar/g' file.txt") is False
        assert is_bash_command_read_only("sed --in-place 's/foo/bar/g' file.txt") is False
        assert is_bash_command_read_only("sed --in-place=.bak 's/foo/bar/g' file.txt") is False

    @pytest.mark.parametrize("interpreter", ["python", "python3", "node", "ruby", "perl"])
    def test_interpreter_version_flags(self, interpreter):
        assert is_bash_command_read_only(f"{interpreter} -v") is True
        assert is_bash_command_read_only(f"{interpreter} --version") is True
        assert is_bash_command_read_only(f"{interpreter} -V") is True
        assert is_bash_command_read_only(f"{interpreter} script.py") is False
        assert is_bash_command_read_only(interpreter) is False
        assert is_bash_command_read_only(f"{interpreter} -c 'print(1)'") is False

    def test_edge_cases_and_parsing_guards(self):
        # Empty and whitespace
        assert is_bash_command_read_only("") is True
        assert is_bash_command_read_only("   ") is True
        assert is_single_subcommand_read_only("") is True
        assert is_single_subcommand_read_only("   ") is True

        # Malformed quotes fail closed
        assert is_bash_command_read_only('ls "unclosed') is False
        assert is_single_subcommand_read_only('ls "unclosed') is False

        # Git alone and with flags
        assert is_bash_command_read_only("git") is True
        assert is_bash_command_read_only("git -C /tmp") is True
        assert is_bash_command_read_only("git -c foo=bar") is True
        assert is_bash_command_read_only("git -C /tmp -c foo=bar commit") is False
        assert is_bash_command_read_only("git --version") is True
        assert is_bash_command_read_only("git --help") is True
        assert is_bash_command_read_only("git checkout main") is False

        # Escaped quotes and operators in split_shell_commands
        parts = split_shell_commands(r"echo \"hello\" ; ls")
        assert len(parts) == 2
        assert parts[0] == r'echo \"hello\"'
        assert parts[1] == "ls"

        # Escaped backslash before delimiter
        parts_bs = split_shell_commands(r"echo foo\\; ls")
        assert len(parts_bs) == 2
        assert parts_bs[0] == r"echo foo\\"
        assert parts_bs[1] == "ls"

        # Escaped delimiter itself
        parts_esc = split_shell_commands(r"echo a\;b")
        assert len(parts_esc) == 1
        assert parts_esc[0] == r"echo a\;b"

        # Multiple delimiters in a chain
        chain = split_shell_commands("a && b && c")
        assert chain == ["a", "b", "c"]
        chain_or = split_shell_commands("a || b || c")
        assert chain_or == ["a", "b", "c"]
        chain_semi = split_shell_commands("a ; b ; c")
        assert chain_semi == ["a", "b", "c"]
        chain_pipe = split_shell_commands("a | b | c")
        assert chain_pipe == ["a", "b", "c"]
        chain_mixed = split_shell_commands("a && b || c ; d | e")
        assert chain_mixed == ["a", "b", "c", "d", "e"]

        # Delimiters inside quotes are preserved
        assert split_shell_commands("echo 'a && b || c ; d | e'") == ["echo 'a && b || c ; d | e'"]
        assert split_shell_commands('echo "a && b || c ; d | e"') == ['echo "a && b || c ; d | e"']
        assert split_shell_commands("echo '\"a;b\"'") == ["echo '\"a;b\"'"]
        assert split_shell_commands('echo "\'a;b\'"') == ['echo "\'a;b\'"']

        # Empty segments filtered
        assert split_shell_commands(" ; echo a ; ; echo b ; ") == ["echo a", "echo b"]

        # Timeout options and durations
        assert is_bash_command_read_only("timeout 10 ls") is True
        assert is_bash_command_read_only("timeout 10s ls") is True
        assert is_bash_command_read_only("timeout 1.5m ls") is True
        assert is_bash_command_read_only("timeout 2h ls") is True
        assert is_bash_command_read_only("timeout 1d ls") is True
        assert is_bash_command_read_only("timeout -s 9 10s ls") is True
        assert is_bash_command_read_only("timeout --signal 9 10s ls") is True
        assert is_bash_command_read_only("timeout -k 5s 10s ls") is True
        assert is_bash_command_read_only("timeout --kill-after 5s 10s ls") is True
        assert is_bash_command_read_only("timeout -v 10s ls") is True

        # Env options and chaining
        assert is_bash_command_read_only("env -i ls") is True
        assert is_bash_command_read_only("env -u FOO -u BAR ls") is True
        assert is_bash_command_read_only("env --unset FOO ls") is True
        assert is_bash_command_read_only("env A=1 B=2 timeout 5s env C=3 ls") is True
        assert is_bash_command_read_only("env A=1 B=2 timeout 5s env C=3 rm -rf x") is False

        # bash_is_read_only variations
        assert bash_is_read_only({"command": "ls"}) is True
        assert bash_is_read_only({"cmd": "ls"}) is True
        assert bash_is_read_only({"command": None, "cmd": "ls"}) is True
        assert bash_is_read_only({"command": "rm"}) is False
        assert bash_is_read_only({"cmd": "rm"}) is False
        assert bash_is_read_only({"command": None, "cmd": "rm"}) is False
        assert bash_is_read_only({"command": None, "cmd": None}) is True
        assert bash_is_read_only({}) is True
        assert bash_is_read_only({"other": "value"}) is True
        assert bash_is_read_only({"command": "ls && rm foo"}) is False
