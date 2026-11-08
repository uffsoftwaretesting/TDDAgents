"""
The bubblewrap command line (`app/utils/sandbox/runtime/linux_sandbox_utils.py`), built over an in-memory
filesystem: golden argv for the shipped configuration's shape and for each special case of
`generateFilesystemArgs`, the mandatory denies, the network wrapping and seccomp.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from app.utils.sandbox.runtime import linux_sandbox_utils as lsu
from app.utils.sandbox.runtime.generate_seccomp_filter import (
    get_apply_seccomp_binary_path,
    get_pre_generated_bpf_path,
)
from app.utils.sandbox.runtime.sandbox_config import RipgrepConfig, SeccompConfig
from app.utils.sandbox.runtime.sandbox_schemas import FsReadRestrictionConfig, FsWriteRestrictionConfig
from tests.sandbox_doubles import FakeFs, RecordingRipgrep

HOME = "/home/u"
CWD = "/home/u/ws/p"
TMP = "/tmp/tddagents-1000"
RG = RipgrepConfig(command="/opt/rg", args=["--no-config"])


@pytest.fixture(autouse=True)
def _no_mount_points() -> None:
    lsu._bwrap_mount_points.clear()


def project_fs(**extra: set[str]) -> FakeFs:
    return FakeFs(
        dirs={CWD, f"{CWD}/.git/hooks", TMP, "/usr/bin", *extra.get("dirs", set())},
        files={f"{CWD}/.tddagents/project.json", f"{CWD}/.git/config", "/dev/null", *extra.get("files", set())},
    )


def fs_args(fs: FakeFs, read: FsReadRestrictionConfig | None, write: FsWriteRestrictionConfig | None,
            rg: RecordingRipgrep | None = None, **kw: object) -> list[str]:
    return lsu.generate_filesystem_args(read, write, cwd=CWD, home=HOME, ripgrep_config=RG, fs=fs,
                                        run_ripgrep=rg or RecordingRipgrep(), **kw)  # type: ignore[arg-type]


MANDATORY_DEV_NULL = [f"{CWD}/{name}" for name in (
    ".gitconfig", ".gitmodules", ".bashrc", ".bash_profile", ".zshrc", ".zprofile", ".profile", ".ripgreprc",
    ".mcp.json", ".vscode", ".idea", ".tddagents/commands", ".tddagents/agents")]


def dev_null_binds(paths: list[str]) -> list[str]:
    return [arg for path in paths for arg in ("--ro-bind", "/dev/null", path)]


def test_the_shipped_shape_hides_home_and_keeps_the_project_writable_with_its_denies_last() -> None:
    fs = project_fs()
    write = FsWriteRestrictionConfig(allow_only=["/dev/null", TMP, f"{HOME}/.npm/_logs", "."],
                                     deny_within_allow=[f"{CWD}/.tddagents/project.json"])
    read = FsReadRestrictionConfig(deny_only=["~"], allow_within_deny=["."])
    assert fs_args(fs, read, write) == [
        "--ro-bind", "/", "/",
        "--bind", TMP, TMP,
        "--bind", CWD, CWD,
        "--tmpfs", HOME,
        # The tmpfs wiped the project's bind: it is bound again, writable; `allowRead: ["."]` adds nothing.
        "--bind", CWD, CWD,
        "--ro-bind", f"{CWD}/.tddagents/project.json", f"{CWD}/.tddagents/project.json",
        *dev_null_binds(MANDATORY_DEV_NULL),
        "--ro-bind", f"{CWD}/.git/hooks", f"{CWD}/.git/hooks",
        "--ro-bind", f"{CWD}/.git/config", f"{CWD}/.git/config",
    ]
    # Every /dev/null mount point is a file bwrap would create: each is tracked for the cleanup.
    assert lsu.bwrap_mount_points() == MANDATORY_DEV_NULL


def test_no_write_config_binds_the_root_writable_and_scans_nothing() -> None:
    rg = RecordingRipgrep()
    assert fs_args(project_fs(), None, None, rg) == ["--bind", "/", "/"]
    assert rg.calls == []


def test_a_write_path_that_does_not_exist_or_is_a_device_is_skipped() -> None:
    fs = project_fs()
    write = FsWriteRestrictionConfig(allow_only=["/dev/stdout", "/nowhere", TMP], deny_within_allow=[])
    args = fs_args(fs, None, write)
    assert args[:6] == ["--ro-bind", "/", "/", "--bind", TMP, TMP]
    assert "/nowhere" not in args and "/dev/stdout" not in args


def test_a_symlinked_write_path_resolving_to_an_ancestor_is_skipped() -> None:
    fs = project_fs()
    fs.links[f"{TMP}/up"] = "/tmp"
    write = FsWriteRestrictionConfig(allow_only=[f"{TMP}/up"], deny_within_allow=[])
    assert fs_args(fs, None, write)[:3] == ["--ro-bind", "/", "/"]
    assert f"{TMP}/up" not in fs_args(fs, None, write)


def test_a_symlinked_write_path_pointing_outside_its_own_tree_is_skipped() -> None:
    fs = project_fs(dirs={f"{TMP}/real/deeper"})
    fs.links[f"{TMP}/alias"] = f"{TMP}/real/deeper"
    write = FsWriteRestrictionConfig(allow_only=[f"{TMP}/alias", f"{TMP}/real/deeper"], deny_within_allow=[])
    # A sibling is outside the link's tree (isSymlinkOutsideBoundary); the real path itself is bound.
    assert fs_args(fs, None, write) == ["--ro-bind", "/", "/", "--bind", f"{TMP}/real/deeper",
                                        f"{TMP}/real/deeper"]


def test_a_missing_intermediate_component_is_blocked_with_an_empty_directory() -> None:
    fs = FakeFs(dirs={CWD})
    write = FsWriteRestrictionConfig(allow_only=["."], deny_within_allow=[f"{CWD}/a/b/c"])
    args = fs_args(fs, None, write)
    # `.tddagents` does not exist either, so its commands/agents denies block `.tddagents` itself.
    assert args[6:9] == ["--ro-bind", "/tmp/tddagents-empty-0", f"{CWD}/a"]
    assert ["--ro-bind", "/tmp/tddagents-empty-1", f"{CWD}/.tddagents"] == args[9 + 3 * 11:9 + 3 * 12]
    assert lsu.bwrap_mount_points()[0] == f"{CWD}/a"


def test_a_deny_path_with_the_same_normalized_form_is_bound_once() -> None:
    fs = project_fs()
    write = FsWriteRestrictionConfig(allow_only=["."], deny_within_allow=[
        f"{CWD}/.tddagents/project.json", ".tddagents/project.json"])
    args = fs_args(fs, None, write)
    assert args.count(f"{CWD}/.tddagents/project.json") == 2  # one --ro-bind <p> <p>


def test_a_deny_path_under_a_file_is_skipped() -> None:
    fs = project_fs()
    write = FsWriteRestrictionConfig(allow_only=["."], deny_within_allow=[f"{CWD}/.tddagents/project.json/x"])
    assert f"{CWD}/.tddagents/project.json/x" not in fs_args(fs, None, write)


def test_a_deny_path_outside_every_write_path_is_already_read_only() -> None:
    fs = project_fs(files={"/etc/passwd"}, dirs={"/opt/missing-parent"})
    write = FsWriteRestrictionConfig(allow_only=["."], deny_within_allow=["/etc/passwd", "/opt/ghost/file"])
    args = fs_args(fs, None, write)
    assert "/etc/passwd" not in args and "/opt/ghost" not in args


def test_a_symlink_component_inside_a_write_path_is_masked_with_dev_null() -> None:
    fs = project_fs(dirs={f"{CWD}/decoy"})
    fs.links[f"{CWD}/.vscode"] = f"{CWD}/decoy"
    write = FsWriteRestrictionConfig(allow_only=["."], deny_within_allow=[])
    args = fs_args(fs, None, write)
    # `.vscode` -> `decoy` is a sibling (outside the link's tree), so the link itself is masked: replacing
    # it with a real directory would otherwise escape the deny.
    index = args.index(f"{CWD}/.vscode")
    assert args[index - 2:index + 1] == ["--ro-bind", "/dev/null", f"{CWD}/.vscode"]
    assert f"{CWD}/.vscode" not in lsu.bwrap_mount_points()


def test_a_symlink_ancestor_of_an_existing_deny_target_is_bound_to_dev_null() -> None:
    fs = project_fs(dirs={"/outside/cfg"})
    fs.links[f"{CWD}/cfg"] = "/outside/cfg"
    write = FsWriteRestrictionConfig(allow_only=["."], deny_within_allow=[f"{CWD}/cfg/settings.json"])
    args = fs_args(fs, None, write)
    index = args.index(f"{CWD}/cfg")
    assert args[index - 2:index + 1] == ["--ro-bind", "/dev/null", f"{CWD}/cfg"]


def test_the_ripgrep_scan_finds_nested_denies() -> None:
    fs = project_fs(dirs={f"{CWD}/sub/.vscode", f"{CWD}/sub/.git/hooks"}, files={f"{CWD}/sub/.bashrc"})
    rg = RecordingRipgrep(matches=["sub/.vscode/settings.json", "sub/.bashrc", "sub/.git/hooks/pre-commit",
                                   "sub/.git/config"])
    deny = lsu.linux_get_mandatory_deny_paths(cwd=CWD, ripgrep_config=RG, fs=fs, run_ripgrep=rg)
    assert deny[-4:] == [f"{CWD}/sub/.vscode", f"{CWD}/sub/.bashrc", f"{CWD}/sub/.git/hooks",
                         f"{CWD}/sub/.git/config"]
    args, target, config = rg.calls[0]
    assert target == CWD and config is RG
    assert args[:4] == ["--files", "--hidden", "--max-depth", "3"]
    assert args[-2:] == ["-g", "!**/node_modules/**"]
    assert ["--iglob", "**/.tddagents/commands/**"] == args[args.index("**/.tddagents/commands/**") - 1:][:2]
    assert args[-4:-2] == ["--iglob", "**/.git/config"]


def test_allow_git_config_keeps_git_config_writable() -> None:
    rg = RecordingRipgrep()
    deny = lsu.linux_get_mandatory_deny_paths(cwd=CWD, ripgrep_config=RG, allow_git_config=True,
                                              fs=project_fs(), run_ripgrep=rg)
    assert f"{CWD}/.git/hooks" in deny and f"{CWD}/.git/config" not in deny
    assert "**/.git/config" not in rg.calls[0][0]


def test_git_denies_need_a_git_directory_and_a_failed_scan_keeps_the_static_list() -> None:
    fs = FakeFs(dirs={CWD}, files={f"{CWD}/.git"})
    deny = lsu.linux_get_mandatory_deny_paths(cwd=CWD, ripgrep_config=RG, max_depth=5, fs=fs,
                                              run_ripgrep=RecordingRipgrep(error=RuntimeError("rg died")))
    assert f"{CWD}/.git/hooks" not in deny
    assert deny == [f"{CWD}/{name}" for name in (
        ".gitconfig", ".gitmodules", ".bashrc", ".bash_profile", ".zshrc", ".zprofile", ".profile", ".ripgreprc",
        ".mcp.json", ".vscode", ".idea", ".tddagents/commands", ".tddagents/agents")]


def test_a_denied_root_becomes_its_children_except_proc_dev_and_sys() -> None:
    fs = FakeFs(dirs={"/usr", "/etc", "/proc", "/dev", "/sys", "/home/u/ws/p"})
    read = FsReadRestrictionConfig(deny_only=["/"], allow_within_deny=["/usr"])
    assert fs_args(fs, read, None) == ["--bind", "/", "/", "--tmpfs", "/etc", "--tmpfs", "/home",
                                       "--tmpfs", "/usr", "--ro-bind", "/usr", "/usr"]


def test_a_denied_file_is_masked_unless_an_allow_covers_it() -> None:
    fs = project_fs(files={f"{CWD}/.env", "/etc/secret"})
    read = FsReadRestrictionConfig(deny_only=[".env", "/etc/secret", "/nonexistent"], allow_within_deny=["."])
    assert fs_args(fs, read, None) == ["--bind", "/", "/", "--ro-bind", "/dev/null", "/etc/secret"]


def test_ssh_config_d_is_always_hidden_when_present() -> None:
    fs = project_fs(dirs={"/etc/ssh/ssh_config.d"})
    assert fs_args(fs, FsReadRestrictionConfig(deny_only=[]), None)[-2:] == ["--tmpfs", "/etc/ssh/ssh_config.d"]


def test_an_allow_path_under_a_denied_directory_is_bound_read_only_when_no_write_path_covers_it() -> None:
    fs = project_fs(dirs={f"{HOME}/.cache/keep"})
    read = FsReadRestrictionConfig(deny_only=[HOME], allow_within_deny=[f"{HOME}/.cache/keep", f"{HOME}/gone"])
    args = fs_args(fs, read, None)
    assert args[3:] == ["--tmpfs", HOME, "--ro-bind", f"{HOME}/.cache/keep", f"{HOME}/.cache/keep"]


def wrap(fs: FakeFs, **kw: object) -> list[str]:
    params = lsu.LinuxSandboxParams(cwd=CWD, home=HOME, tmpdir=TMP, fs=fs, which=lambda name: f"/usr/bin/{name}"
                                    if not name.startswith("/") else name,
                                    run_ripgrep=RecordingRipgrep(), **kw)  # type: ignore[arg-type]
    return shlex.split(lsu.wrap_command_with_sandbox_linux(params))


def test_nothing_to_restrict_returns_the_command_unchanged() -> None:
    params = lsu.LinuxSandboxParams(command="echo hi", needs_network_restriction=False, cwd=CWD, home=HOME,
                                    tmpdir=TMP, read_config=FsReadRestrictionConfig(deny_only=[]))
    assert lsu.wrap_command_with_sandbox_linux(params) == "echo hi"


def test_a_network_restricted_wrap_binds_the_bridge_sockets_and_sets_the_proxy_environment() -> None:
    fs = project_fs(files={"/tmp/h.sock", "/tmp/s.sock"})
    argv = wrap(fs, command="pip install x", needs_network_restriction=True, http_socket_path="/tmp/h.sock",
                socks_socket_path="/tmp/s.sock", http_proxy_port=4001, socks_proxy_port=4002,
                write_config=FsWriteRestrictionConfig(allow_only=["."], deny_within_allow=[]),
                bin_shell="/bin/bash", allow_all_unix_sockets=True)
    assert argv[:10] == ["bwrap", "--new-session", "--die-with-parent", "--unshare-net",
                         "--bind", "/tmp/h.sock", "/tmp/h.sock", "--bind", "/tmp/s.sock", "/tmp/s.sock"]
    env = {argv[i + 1]: argv[i + 2] for i, a in enumerate(argv) if a == "--setenv"}
    assert env["HTTP_PROXY"] == "http://localhost:3128" and env["ALL_PROXY"] == "socks5h://localhost:1080"
    assert env["TMPDIR"] == TMP and env["SANDBOX_RUNTIME"] == "1"
    assert env["TDDAGENTS_HOST_HTTP_PROXY_PORT"] == "4001" and env["TDDAGENTS_HOST_SOCKS_PROXY_PORT"] == "4002"
    assert env["GIT_SSH_COMMAND"] == "ssh -o ProxyCommand='socat - PROXY:localhost:%h:%p,proxyport=3128'"
    tail = argv[argv.index("--dev"):]
    assert tail[:8] == ["--dev", "/dev", "--unshare-pid", "--proc", "/proc", "--", "/bin/bash", "-c"]
    inner = shlex.split(tail[8])
    assert inner[:2] == ["/bin/bash", "-c"]
    lines = inner[2].split("\n")
    assert lines[0] == "socat TCP-LISTEN:3128,fork,reuseaddr UNIX-CONNECT:/tmp/h.sock >/dev/null 2>&1 &"
    assert lines[1] == "socat TCP-LISTEN:1080,fork,reuseaddr UNIX-CONNECT:/tmp/s.sock >/dev/null 2>&1 &"
    assert lines[2] == 'trap "kill %1 %2 2>/dev/null; exit" EXIT'
    assert shlex.split(lines[3]) == ["eval", "pip install x"]


def test_seccomp_wraps_the_command_in_apply_seccomp_inside_the_namespace() -> None:
    apply_path, bpf = get_apply_seccomp_binary_path(), get_pre_generated_bpf_path()
    assert apply_path and bpf, "the vendored seccomp binaries ship with the app"
    fs = project_fs(files={"/tmp/h.sock", "/tmp/s.sock"})
    write = FsWriteRestrictionConfig(allow_only=["."], deny_within_allow=[])
    argv = wrap(fs, command="npm test", needs_network_restriction=True, http_socket_path="/tmp/h.sock",
                socks_socket_path="/tmp/s.sock", write_config=write)
    inner = shlex.split(argv[-1])
    assert inner[:2] == ["/usr/bin/bash", "-c"]
    assert shlex.split(inner[2].split("\n")[3]) == [apply_path, bpf, "/usr/bin/bash", "-c", "npm test"]


def test_without_network_seccomp_runs_the_command_directly() -> None:
    argv = wrap(project_fs(), command="ls", needs_network_restriction=False,
                write_config=FsWriteRestrictionConfig(allow_only=["."], deny_within_allow=[]),
                enable_weaker_nested_sandbox=True)
    assert "--unshare-net" not in argv and "--proc" not in argv
    assert shlex.split(argv[-1])[1:] == [get_pre_generated_bpf_path(), "/usr/bin/bash", "-c", "ls"]
    plain = wrap(project_fs(), command="ls", needs_network_restriction=True, allow_all_unix_sockets=True,
                 read_config=FsReadRestrictionConfig(deny_only=[HOME]))
    # No bridge sockets: the namespace has no network at all, and the command runs as given.
    assert plain[1:4] == ["--new-session", "--die-with-parent", "--unshare-net"] and plain[-1] == "ls"
    assert "--setenv" not in plain


def test_a_missing_bridge_socket_or_shell_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="HTTP bridge socket does not exist: /tmp/h.sock"):
        wrap(project_fs(), command="x", needs_network_restriction=True, http_socket_path="/tmp/h.sock",
             socks_socket_path="/tmp/s.sock")
    with pytest.raises(RuntimeError, match="SOCKS bridge socket does not exist"):
        wrap(project_fs(files={"/tmp/h.sock"}), command="x", needs_network_restriction=True,
             http_socket_path="/tmp/h.sock", socks_socket_path="/tmp/s.sock")
    params = lsu.LinuxSandboxParams(command="x", needs_network_restriction=True, cwd=CWD, home=HOME, tmpdir=TMP,
                                    fs=project_fs(), which=lambda _name: None, bin_shell="zsh",
                                    run_ripgrep=RecordingRipgrep())
    with pytest.raises(RuntimeError, match="Shell 'zsh' not found in PATH"):
        lsu.wrap_command_with_sandbox_linux(params)


def test_explicit_seccomp_paths_that_do_not_exist_fall_back_to_the_vendored_ones(tmp_path: Path) -> None:
    argv = wrap(project_fs(), command="ls", needs_network_restriction=False,
                write_config=FsWriteRestrictionConfig(allow_only=["."], deny_within_allow=[]),
                seccomp_config=SeccompConfig(bpf_path=str(tmp_path / "no.bpf"), apply_path=str(tmp_path / "no")))
    assert shlex.split(argv[-1])[:2] == [get_apply_seccomp_binary_path(), get_pre_generated_bpf_path()]


def test_build_sandbox_command_needs_apply_seccomp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lsu, "get_apply_seccomp_binary_path", lambda _path=None: None)
    with pytest.raises(RuntimeError, match="apply-seccomp binary not found"):
        lsu.build_sandbox_command("/h", "/s", "ls", "/f.bpf")
    assert lsu.build_sandbox_command("/h", "/s", "ls", None).startswith("bash -c ")


def test_dependencies_need_bwrap_and_socat_and_warn_without_seccomp() -> None:
    check = lsu.check_linux_dependencies(which=lambda _name: None)
    assert check.errors == ["bubblewrap (bwrap) not installed", "socat not installed"]
    assert check.warnings == []
    assert lsu.check_linux_dependencies(which=lambda name: f"/usr/bin/{name}").errors == []
    status = lsu.get_linux_dependency_status(which=lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)
    assert (status.has_bwrap, status.has_socat, status.has_seccomp_bpf, status.has_seccomp_apply) == (
        True, False, True, True)


def test_cleanup_removes_only_what_bwrap_left(tmp_path: Path) -> None:
    empty_file, full_file, empty_dir, full_dir = (tmp_path / n for n in ("f", "g", "d", "e"))
    empty_file.write_text("")
    full_file.write_text("kept")
    empty_dir.mkdir()
    full_dir.mkdir()
    (full_dir / "x").write_text("")
    for path in (empty_file, full_file, empty_dir, full_dir, tmp_path / "gone"):
        lsu._bwrap_mount_points[str(path)] = None
    lsu.cleanup_bwrap_mount_points()
    assert not empty_file.exists() and not empty_dir.exists()
    assert full_file.exists() and full_dir.exists()
    assert lsu.bwrap_mount_points() == []
