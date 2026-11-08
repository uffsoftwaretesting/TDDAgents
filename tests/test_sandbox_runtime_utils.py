"""The sandbox runtime's pure parts: config parsing, path rules, proxy environment, violations, seccomp lookup."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import pytest

from app.utils.sandbox.runtime import generate_seccomp_filter as seccomp
from app.utils.sandbox.runtime.platform import get_platform, get_wsl_version
from app.utils.sandbox.runtime.ripgrep import RipgrepError, rip_grep
from app.utils.sandbox.runtime.sandbox_config import (
    INVALID_DOMAIN_PATTERN_MESSAGE,
    SandboxConfigError,
    is_valid_domain_pattern,
    parse_sandbox_runtime_config,
)
from app.utils.sandbox.runtime.sandbox_utils import (
    HOST_FS,
    NO_PROXY_ADDRESSES,
    contains_glob_chars,
    decode_sandboxed_command,
    encode_sandboxed_command,
    expand_glob_pattern,
    generate_proxy_env_vars,
    get_dangerous_directories,
    get_default_write_paths,
    glob_to_regex,
    is_symlink_outside_boundary,
    normalize_case_for_comparison,
    normalize_path_for_sandbox,
    remove_trailing_glob_suffix,
)
from app.utils.sandbox.runtime.sandbox_violation_store import SandboxViolationEvent, SandboxViolationStore
from app.utils.sandbox.runtime.which import which_sync
from tests.sandbox_doubles import FakeFs

# ── sandbox_config ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pattern, valid", [
    ("example.com", True), ("localhost", True), ("*.example.com", True), ("a.b.c", True),
    ("*.com", False), ("*", False), ("*.", False), ("*..com", False), ("*.example.", False),
    ("ex*ample.com", False), ("example", False), (".example.com", False), ("example.com.", False),
    ("https://example.com", False), ("example.com/path", False), ("example.com:443", False),
])
def test_domain_patterns(pattern: str, valid: bool) -> None:
    assert is_valid_domain_pattern(pattern) is valid


def test_a_full_document_parses_into_the_dataclasses() -> None:
    config = parse_sandbox_runtime_config({
        "network": {"allowedDomains": ["pypi.org"], "deniedDomains": ["*.evil.com"], "allowUnixSockets": ["/s"],
                    "allowAllUnixSockets": False, "allowLocalBinding": True, "httpProxyPort": 8080,
                    "socksProxyPort": 1081, "mitmProxy": {"socketPath": "/m.sock", "domains": ["api.x.org"]}},
        "filesystem": {"denyRead": ["~"], "allowRead": ["."], "allowWrite": ["."], "denyWrite": ["/etc"],
                       "allowGitConfig": True},
        "ignoreViolations": {"*": ["/tmp"]}, "enableWeakerNestedSandbox": True,
        "enableWeakerNetworkIsolation": False, "ripgrep": {"command": "rg", "args": ["-j1"], "argv0": "rg"},
        "mandatoryDenySearchDepth": 4, "allowPty": True, "seccomp": {"bpfPath": "/b", "applyPath": "/a"},
    })
    assert config.network.allowed_domains == ["pypi.org"] and config.network.denied_domains == ["*.evil.com"]
    assert config.network.mitm_proxy is not None and config.network.mitm_proxy.socket_path == "/m.sock"
    assert (config.network.http_proxy_port, config.network.socks_proxy_port) == (8080, 1081)
    assert config.network.allow_unix_sockets == ["/s"] and config.network.allow_local_binding is True
    assert config.filesystem.allow_read == ["."] and config.filesystem.allow_git_config is True
    assert config.ignore_violations == {"*": ["/tmp"]} and config.mandatory_deny_search_depth == 4
    assert config.ripgrep is not None and config.ripgrep.argv0 == "rg" and config.ripgrep.args == ["-j1"]
    assert config.seccomp is not None and config.seccomp.apply_path == "/a"
    assert config.enable_weaker_nested_sandbox is True and config.allow_pty is True


def test_a_minimal_document_leaves_the_optional_parts_unset() -> None:
    config = parse_sandbox_runtime_config({
        "network": {"allowedDomains": [], "deniedDomains": []},
        "filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": []}})
    assert config.network.mitm_proxy is None and config.filesystem.allow_read is None
    assert config.ripgrep is None and config.seccomp is None and config.ignore_violations is None


@pytest.mark.parametrize("document, message", [
    (None, "(root): Required"),
    ([], "(root): Expected object"),
    ({"filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": []}}, "network: Required"),
    ({"network": {"allowedDomains": ["*.com"], "deniedDomains": []},
      "filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": []}},
     f"network.allowedDomains.0: {INVALID_DOMAIN_PATTERN_MESSAGE}"),
    ({"network": {"allowedDomains": [], "deniedDomains": "x"},
      "filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": []}}, "network.deniedDomains: Expected array"),
    ({"network": {"allowedDomains": [], "deniedDomains": [], "httpProxyPort": 70000},
      "filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": []}},
     "network.httpProxyPort: Number must be between 1 and 65535"),
    ({"network": {"allowedDomains": [], "deniedDomains": [], "socksProxyPort": True},
      "filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": []}}, "network.socksProxyPort: Expected integer"),
    ({"network": {"allowedDomains": [], "deniedDomains": []},
      "filesystem": {"denyRead": [""], "allowWrite": [], "denyWrite": []}}, "filesystem.denyRead.0: Path cannot be"),
    ({"network": {"allowedDomains": [], "deniedDomains": []},
      "filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": [], "allowGitConfig": "yes"}},
     "filesystem.allowGitConfig: Expected boolean"),
    ({"network": {"allowedDomains": [], "deniedDomains": [], "mitmProxy": {"socketPath": "", "domains": []}},
      "filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": []}}, "network.mitmProxy.domains: Array must"),
    ({"network": {"allowedDomains": [], "deniedDomains": []},
      "filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": []}, "mandatoryDenySearchDepth": 11},
     "mandatoryDenySearchDepth: Number must be between 1 and 10"),
    ({"network": {"allowedDomains": [], "deniedDomains": []},
      "filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": []}, "ripgrep": {}}, "ripgrep.command: Required"),
    ({"network": {"allowedDomains": [], "deniedDomains": []},
      "filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": []}, "ripgrep": {"command": 1}},
     "ripgrep.command: Expected string"),
    ({"network": {"allowedDomains": [], "deniedDomains": []},
      "filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": []}, "seccomp": {"bpfPath": 3}},
     "seccomp.bpfPath: Expected string"),
    ({"network": {"allowedDomains": [], "deniedDomains": []},
      "filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": []}, "ignoreViolations": {"*": "x"}},
     "ignoreViolations.*: Expected array of strings"),
])
def test_an_invalid_document_raises_the_schema_issue(document: object, message: str) -> None:
    with pytest.raises(SandboxConfigError, match=re.escape(message)):
        parse_sandbox_runtime_config(document)


# ── sandbox_utils ───────────────────────────────────────────────────────────────


def test_dangerous_directories_use_the_tddagents_config_dir() -> None:
    assert get_dangerous_directories() == [".vscode", ".idea", ".tddagents/commands", ".tddagents/agents"]


def test_small_path_helpers() -> None:
    assert normalize_case_for_comparison(".TddAgents/X") == ".tddagents/x"
    assert [contains_glob_chars(p) for p in ("a*", "a?", "a[", "a]", "abc")] == [True, True, True, True, False]
    assert remove_trailing_glob_suffix("/a/b/**") == "/a/b"
    assert remove_trailing_glob_suffix("/**") == "/"
    assert remove_trailing_glob_suffix("/a/**/b") == "/a/**/b"


@pytest.mark.parametrize("original, resolved, outside", [
    ("/a/b", "/a/b", False),
    ("/tmp/x", "/private/tmp/x", False),
    ("/var/x", "/private/var/x", False),
    ("/a/b", "/", True),
    ("/a/b", "/usr", True),
    ("/a/b/c", "/a/b", True),
    ("/tmp/x/y", "/private/tmp", True),
    ("/a/b", "/a/b/c", False),
    ("/tmp/x", "/private/tmp/x/y", False),
    ("/a/b", "/c/d", True),
    ("/a/b/", "/a/b/", False),
])
def test_symlink_boundaries(original: str, resolved: str, outside: bool) -> None:
    assert is_symlink_outside_boundary(original, resolved) is outside


def test_normalize_path_for_sandbox() -> None:
    fs = FakeFs(dirs={"/home/u/p/src", "/data/real"}, links={"/home/u/p/link": "/data/real"})
    kw = {"cwd": "/home/u/p", "home": "/home/u", "fs": fs}
    assert normalize_path_for_sandbox("~", **kw) == "/home/u"  # type: ignore[arg-type]
    assert normalize_path_for_sandbox("~/x", **kw) == "/home/u/x"  # type: ignore[arg-type]
    assert normalize_path_for_sandbox(".", **kw) == "/home/u/p"  # type: ignore[arg-type]
    assert normalize_path_for_sandbox("./src/../src", **kw) == "/home/u/p/src"  # type: ignore[arg-type]
    assert normalize_path_for_sandbox("../q", **kw) == "/home/u/q"  # type: ignore[arg-type]
    # A link out of its own tree is kept as written; an absolute path that does not exist is unchanged.
    assert normalize_path_for_sandbox("link", **kw) == "/home/u/p/link"  # type: ignore[arg-type]
    assert normalize_path_for_sandbox("/no/such/../x", **kw) == "/no/such/../x"  # type: ignore[arg-type]
    fs.links["/home/u/p/inner"] = "/home/u/p/inner-real/deep"
    fs.dirs.add("/home/u/p/inner-real/deep")
    assert normalize_path_for_sandbox("src/*.py", **kw) == "/home/u/p/src/*.py"  # type: ignore[arg-type]
    assert normalize_path_for_sandbox("/*.py", **kw) == "/*.py"  # type: ignore[arg-type]
    assert normalize_path_for_sandbox("gone/*.py", **kw) == "/home/u/p/gone/*.py"  # type: ignore[arg-type]
    fs.links["/home/u/p/up"] = "/home/u/p/src/x"
    fs.dirs.add("/home/u/p/src/x")
    assert normalize_path_for_sandbox("up/*.py", **kw) == "/home/u/p/up/*.py"  # type: ignore[arg-type]


def test_normalize_resolves_a_glob_prefix_through_a_link_that_stays_in_its_tree(tmp_path: Path) -> None:
    real = tmp_path / "p"
    (real / "src").mkdir(parents=True)
    assert normalize_path_for_sandbox("src/**/*.py", cwd=str(real), home=str(tmp_path)) == f"{real}/src/**/*.py"


def test_default_write_paths_name_the_given_temp_dir() -> None:
    paths = get_default_write_paths(home="/home/u", tmpdir="/tmp/tddagents-7")
    assert paths == ["/dev/stdout", "/dev/stderr", "/dev/null", "/dev/tty", "/dev/dtracehelper",
                     "/dev/autofs_nowait", "/tmp/tddagents-7", "/private/tmp/tddagents-7", "/home/u/.npm/_logs",
                     "/home/u/.tddagents/debug"]


def test_proxy_env_without_ports_sets_only_the_runtime_marker_and_tmpdir() -> None:
    assert generate_proxy_env_vars(tmpdir="/t") == ["SANDBOX_RUNTIME=1", "TMPDIR=/t"]


def test_proxy_env_on_linux() -> None:
    env = dict(e.split("=", 1) for e in generate_proxy_env_vars(3128, 1080, tmpdir="/t", platform="linux"))
    assert env["NO_PROXY"] == env["no_proxy"] == NO_PROXY_ADDRESSES
    assert env["HTTPS_PROXY"] == env["https_proxy"] == "http://localhost:3128"
    assert env["all_proxy"] == env["GRPC_PROXY"] == env["FTP_PROXY"] == "socks5h://localhost:1080"
    assert env["RSYNC_PROXY"] == "localhost:1080" and env["DOCKER_HTTPS_PROXY"] == "http://localhost:3128"
    assert (env["CLOUDSDK_PROXY_TYPE"], env["CLOUDSDK_PROXY_ADDRESS"], env["CLOUDSDK_PROXY_PORT"]) == (
        "https", "localhost", "3128")
    assert env["GIT_SSH_COMMAND"] == "ssh -o ProxyCommand='socat - PROXY:localhost:%h:%p,proxyport=3128'"


def test_proxy_env_with_socks_only_and_on_macos() -> None:
    socks_only = dict(e.split("=", 1) for e in generate_proxy_env_vars(None, 1080, tmpdir="/t", platform="linux"))
    assert "HTTP_PROXY" not in socks_only and "GIT_SSH_COMMAND" not in socks_only
    assert "CLOUDSDK_PROXY_TYPE" not in socks_only and socks_only["DOCKER_HTTP_PROXY"] == "http://localhost:1080"
    mac = dict(e.split("=", 1) for e in generate_proxy_env_vars(3128, 1080, tmpdir="/t", platform="macos"))
    assert mac["GIT_SSH_COMMAND"] == "ssh -o ProxyCommand='nc -X 5 -x localhost:1080 %h %p'"
    http_only = generate_proxy_env_vars(3128, None, tmpdir="/t", platform="linux")
    assert "ALL_PROXY=socks5h://localhost:None" not in http_only and not any(
        e.startswith("ALL_PROXY") for e in http_only)


def test_command_encoding_round_trips_the_first_hundred_characters() -> None:
    command = "x" * 150
    assert decode_sandboxed_command(encode_sandboxed_command(command)) == "x" * 100
    assert encode_sandboxed_command("ls") == "bHM="


@pytest.mark.parametrize("glob, path, matches", [
    ("/a/*.ts", "/a/x.ts", True), ("/a/*.ts", "/a/b/x.ts", False), ("/a/**/*.ts", "/a/x.ts", True),
    ("/a/**/*.ts", "/a/b/c/x.ts", True), ("/a/**", "/a/b/c", True), ("/a/file?.txt", "/a/file1.txt", True),
    ("/a/file?.txt", "/a/file10.txt", False), ("/a/f[0-9].txt", "/a/f3.txt", True), ("/a/(x).txt", "/a/(x).txt", True),
    ("/a/x+y", "/a/x+y", True), ("/a/[b", "/a/[b", True), ("/a/.env", "/a/xenv", False),
])
def test_glob_to_regex(glob: str, path: str, matches: bool) -> None:
    assert (re.search(glob_to_regex(glob), path) is not None) is matches


def test_expand_glob_pattern_lists_matches_below_the_static_prefix() -> None:
    fs = FakeFs(files={"/home/u/p/a.env", "/home/u/p/sub/b.env", "/home/u/p/c.txt"})
    kw = {"cwd": "/home/u/p", "home": "/home/u", "fs": fs}
    assert expand_glob_pattern("*.env", **kw) == ["/home/u/p/a.env"]  # type: ignore[arg-type]
    assert expand_glob_pattern("**/*.env", **kw) == ["/home/u/p/a.env", "/home/u/p/sub/b.env"]  # type: ignore[arg-type]
    assert expand_glob_pattern("/*.env", **kw) == []  # type: ignore[arg-type]
    assert expand_glob_pattern("missing/*.env", **kw) == []  # type: ignore[arg-type]


def test_expand_glob_pattern_on_the_host_and_a_walk_failure(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.env").write_text("")
    assert expand_glob_pattern("x/*.env", cwd=str(tmp_path), home="/h") == [str(tmp_path / "x" / "a.env")]

    class Broken(FakeFs):
        def walk(self, path: str) -> list[str]:
            raise OSError("denied")

    assert expand_glob_pattern("/d/*.env", cwd="/", home="/h", fs=Broken(dirs={"/d"})) == []


def test_host_fs_answers_like_node(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("")
    (tmp_path / "l").symlink_to(tmp_path / "f")
    (tmp_path / "broken").symlink_to(tmp_path / "nowhere")
    assert HOST_FS.exists(str(tmp_path / "f")) and not HOST_FS.exists(str(tmp_path / "broken"))
    assert HOST_FS.is_dir(str(tmp_path)) and HOST_FS.is_file(str(tmp_path / "l"))
    assert HOST_FS.is_symlink(str(tmp_path / "l")) and not HOST_FS.is_symlink(str(tmp_path / "f"))
    assert HOST_FS.realpath(str(tmp_path / "l")) == str((tmp_path / "f").resolve())
    for probe in (HOST_FS.is_dir, HOST_FS.is_file, HOST_FS.is_symlink, HOST_FS.realpath):
        with pytest.raises(OSError):
            probe(str(tmp_path / "nowhere"))
    assert sorted(HOST_FS.listdir(str(tmp_path))) == ["broken", "f", "l"]
    assert str(tmp_path / "f") in HOST_FS.walk(str(tmp_path))
    made = HOST_FS.mkdtemp("tddagents-empty-")
    assert os.path.isdir(made) and os.path.basename(made).startswith("tddagents-empty-")
    os.rmdir(made)


# ── violation store ─────────────────────────────────────────────────────────────


def test_the_violation_store_keeps_a_bounded_tail_and_notifies() -> None:
    store = SandboxViolationStore()
    seen: list[int] = []
    unsubscribe = store.subscribe(lambda violations: seen.append(len(violations)))
    assert seen == [0]
    for index in range(102):
        store.add_violation(SandboxViolationEvent(line=f"v{index}", timestamp=datetime.now(),
                                                  encoded_command=encode_sandboxed_command("ls" if index else "rm")))
    assert store.get_count() == 100 and store.get_total_count() == 102
    assert store.get_violations(2)[-1].line == "v101" and store.get_violations(0) == []
    assert len(store.get_violations_for_command("ls")) == 100 and store.get_violations_for_command("rm") == []
    assert seen[-1] == 100
    store.clear()
    assert store.get_count() == 0 and store.get_total_count() == 102 and seen[-1] == 0
    unsubscribe()
    unsubscribe()
    store.add_violation(SandboxViolationEvent(line="late", timestamp=datetime.now()))
    assert seen[-1] == 0


# ── seccomp, platform, which, ripgrep ───────────────────────────────────────────


@pytest.mark.parametrize("machine, arch", [
    ("x86_64", "x64"), ("AMD64", "x64"), ("aarch64", "arm64"), ("arm64", "arm64"), ("i686", None), ("riscv64", None)])
def test_vendor_architecture(machine: str, arch: str | None) -> None:
    assert seccomp.get_vendor_architecture(machine) == arch


def test_the_vendored_binaries_are_found_per_architecture_and_an_explicit_path_wins(tmp_path: Path) -> None:
    seccomp.clear_seccomp_path_caches()
    for machine, arch in (("x86_64", "x64"), ("aarch64", "arm64")):
        bpf = seccomp.get_pre_generated_bpf_path(machine=machine)
        apply_path = seccomp.get_apply_seccomp_binary_path(machine=machine)
        assert bpf == str(seccomp.VENDOR_SECCOMP_DIR / arch / "unix-block.bpf")
        assert apply_path == str(seccomp.VENDOR_SECCOMP_DIR / arch / "apply-seccomp")
        assert os.access(apply_path, os.X_OK)
    explicit = tmp_path / "my.bpf"
    explicit.write_bytes(b"\0")
    assert seccomp.get_pre_generated_bpf_path(str(explicit)) == str(explicit)
    assert seccomp.get_pre_generated_bpf_path(machine="i686") is None
    assert seccomp.get_apply_seccomp_binary_path(machine="riscv64") is None
    assert seccomp.get_pre_generated_bpf_path(vendor_dir=tmp_path, machine="x86_64") is None
    seccomp.cleanup_seccomp_filter(str(explicit))
    assert explicit.exists()
    seccomp.clear_seccomp_path_caches()


def test_generate_seccomp_filter_without_a_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(seccomp, "get_pre_generated_bpf_path", lambda _p=None: None)
    assert seccomp.generate_seccomp_filter() is None


@pytest.mark.parametrize("platform, proc, expected", [
    ("linux", "Linux 6.18-microsoft-standard-WSL2", "2"), ("linux", "4.4.0-19041-Microsoft", "1"),
    ("linux", "Linux 6.1 generic", None), ("darwin", "WSL2", None)])
def test_wsl_version(platform: str, proc: str, expected: str | None) -> None:
    assert get_wsl_version(platform, proc) == expected


def test_wsl_version_reads_proc_version_on_linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.utils.sandbox.runtime import platform as platform_module

    version = tmp_path / "version"
    version.write_text("Linux 5.15 WSL3")
    monkeypatch.setattr(platform_module, "PROC_VERSION", str(version))
    assert get_wsl_version("linux") == "3"
    monkeypatch.setattr(platform_module, "PROC_VERSION", str(tmp_path / "none"))
    assert get_wsl_version("linux") is None


@pytest.mark.parametrize("raw, platform", [
    ("darwin", "macos"), ("linux", "linux"), ("win32", "windows"), ("freebsd13", "unknown")])
def test_platform(raw: str, platform: str) -> None:
    assert get_platform(raw) == platform


def test_which_and_ripgrep(tmp_path: Path) -> None:
    from app.utils.ripgrep import builtin_ripgrep_path
    from app.utils.sandbox.runtime.sandbox_config import RipgrepConfig

    assert which_sync("sh") is not None and which_sync("no-such-binary-xyz") is None
    (tmp_path / "a.mcp.json").write_text("")
    (tmp_path / ".mcp.json").write_text("")
    rg = RipgrepConfig(command=builtin_ripgrep_path())
    assert rip_grep(["--files", "--hidden", "--iglob", ".mcp.json"], str(tmp_path), rg) == [
        str(tmp_path / ".mcp.json")]
    assert rip_grep(["--files", "--iglob", "nothing"], str(tmp_path), rg) == []
    renamed = RipgrepConfig(command=builtin_ripgrep_path(), args=["--hidden"], argv0="rg")
    assert rip_grep(["--files", "--iglob", ".mcp.json"], str(tmp_path), renamed) == [str(tmp_path / ".mcp.json")]
    with pytest.raises(RipgrepError, match="exit code 2"):
        rip_grep(["--no-such-flag"], str(tmp_path), rg)
    with pytest.raises(RipgrepError):
        rip_grep(["--files"], str(tmp_path), RipgrepConfig(command=str(tmp_path / "no-rg")))
