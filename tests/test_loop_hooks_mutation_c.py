"""
Mutation-driven tests for the core hook modules (Phase 4 triage, cluster C).

Each block pins a behaviour a surviving mutant showed no test was looking at: the real HTTP
transport (pinned connection, TLS, proxy path, timeouts, body decoding), background-hook
bookkeeping, `if`-condition preparation with incomplete tools, dedup keys, and a few parsing
edges. Servers here are throwaway loopback servers; nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import datetime
import http.server
import ipaddress
import json
import ssl
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from langchain_core.messages import ToolMessage

from app.loop.hooks.async_registry import AsyncHookRegistry
from app.loop.hooks.config import HooksConfigSnapshot
from app.loop.hooks.exec_command import exec_command_hook
from app.loop.hooks.exec_http import HttpHookPolicy, HttpRequest, _send_blocking, exec_http_hook, should_bypass_proxy
from app.loop.hooks.matching import _find_tool, get_matching_hooks, prepare_if_condition_matcher
from app.loop.hooks.messages import reorder_attachments_for_api
from app.loop.hooks.output import validate_hook_json
from app.loop.hooks.process import CapturedProcess
from app.loop.hooks.schemas import CommandHook, HookMatcher, HttpHook, parse_hooks_settings
from app.loop.hooks.ssrf_guard import expand_ipv6_groups, is_blocked_address, ssrf_guarded_lookup

PINNED = "127.0.0.2"


# ── a recording loopback server ──────────────────────────────────────────────

class _Handler(http.server.BaseHTTPRequestHandler):
    seen: list[dict[str, Any]] = []

    def _record(self, body: str) -> None:
        _Handler.seen.append({
            "method": self.command,
            "path": self.path,
            "body": body,
            "host": self.headers.get("Host"),
            "x": self.headers.get("X-Hook"),
        })

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode()
        self._record(body)
        if "/slow" in self.path:
            time.sleep(1.5)
        if "/redirect" in self.path:
            self.send_response(302)
            self.send_header("Location", "/echo")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        status = 418 if "/err" in self.path else 200
        payload = body.encode() + b"\xff"
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        self._record("")
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_CONNECT(self) -> None:  # noqa: N802
        self._record("")
        self.send_response(200)
        self.end_headers()
        self.close_connection = True

    def log_message(self, *args: Any) -> None:
        pass


def _serve(srv: http.server.HTTPServer) -> Iterator[int]:
    _Handler.seen = []
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def pinned_server() -> Iterator[int]:
    yield from _serve(http.server.ThreadingHTTPServer((PINNED, 0), _Handler))


@pytest.fixture()
def tls_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tddagents-test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(PINNED))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
    srv = http.server.ThreadingHTTPServer((PINNED, 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_path), str(key_path))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    yield from _serve(srv)


def _request(url: str, **kwargs: Any) -> HttpRequest:
    fields: dict[str, Any] = {"body": "hi", "headers": (("X-Hook", "v"),), "timeout_s": 5, "lookup": None}
    fields.update(kwargs)
    return HttpRequest(url=url, **fields)


# ── the pinned (direct) path ─────────────────────────────────────────────────

def test_pinned_connection_posts_to_the_validated_address(pinned_server: int) -> None:
    asked: list[str] = []

    def lookup(host: str) -> list[tuple[str, int]]:
        asked.append(host)
        return [(PINNED, 4)]

    res = _send_blocking(_request(f"http://pinned.invalid:{pinned_server}/echo?x=1", lookup=lookup))
    assert (res.status, res.body) == (200, "hi�")
    assert asked == ["pinned.invalid"]
    assert _Handler.seen == [{
        "method": "POST", "path": "/echo?x=1", "body": "hi",
        "host": f"pinned.invalid:{pinned_server}", "x": "v",
    }]


def test_without_a_lookup_the_url_host_is_the_address(pinned_server: int) -> None:
    res = _send_blocking(_request(f"http://{PINNED}:{pinned_server}/echo"))
    assert (res.status, res.body) == (200, "hi�")


def test_a_hostless_url_asks_the_lookup_for_an_empty_host(pinned_server: int) -> None:
    asked: list[str] = []

    def lookup(host: str) -> list[tuple[str, int]]:
        asked.append(host)
        return [(PINNED, 4)]

    res = _send_blocking(_request(f"http://:{pinned_server}/echo", lookup=lookup))
    assert res.status == 200
    assert asked == [""]


def test_the_pinned_connection_honours_the_timeout(pinned_server: int) -> None:
    started = time.monotonic()
    with pytest.raises(OSError):
        _send_blocking(_request(f"http://{PINNED}:{pinned_server}/slow", timeout_s=0.3))
    assert time.monotonic() - started < 1.2


def test_an_https_url_speaks_tls_even_to_a_plain_server(pinned_server: int) -> None:
    with pytest.raises(ssl.SSLError):
        _send_blocking(_request(f"https://{PINNED}:{pinned_server}/echo"))


def test_https_posts_over_a_verified_tls_connection(tls_server: int) -> None:
    res = _send_blocking(_request(f"https://{PINNED}:{tls_server}/echo"))
    assert (res.status, res.body) == (200, "hi�")
    assert _Handler.seen[0]["path"] == "/echo" and _Handler.seen[0]["body"] == "hi"


def test_https_connects_to_the_pinned_address_and_verifies_the_url_host(tls_server: int) -> None:
    res = _send_blocking(_request(f"https://{PINNED}:{tls_server}/echo", lookup=lambda h: [(PINNED, 4)]))
    assert res.status == 200


def test_https_honours_the_timeout(tls_server: int) -> None:
    started = time.monotonic()
    with pytest.raises(OSError):
        _send_blocking(_request(f"https://{PINNED}:{tls_server}/slow", timeout_s=0.3))
    assert time.monotonic() - started < 1.2


# ── the env-proxy path ───────────────────────────────────────────────────────

def test_the_proxy_path_posts_the_body_and_headers(pinned_server: int) -> None:
    proxy = f"http://{PINNED}:{pinned_server}"
    res = _send_blocking(_request("http://target.invalid/echo", proxy_url=proxy))
    assert (res.status, res.body) == (200, "hi�")
    assert _Handler.seen == [{
        "method": "POST", "path": "http://target.invalid/echo", "body": "hi", "host": "target.invalid", "x": "v",
    }]


def test_the_proxy_path_returns_an_error_status_with_its_body(pinned_server: int) -> None:
    res = _send_blocking(_request("http://target.invalid/err", proxy_url=f"http://{PINNED}:{pinned_server}"))
    assert (res.status, res.body) == (418, "hi�")


def test_the_proxy_path_does_not_follow_redirects(pinned_server: int) -> None:
    res = _send_blocking(_request("http://target.invalid/redirect", proxy_url=f"http://{PINNED}:{pinned_server}"))
    assert res.status == 302
    assert [s["path"] for s in _Handler.seen] == ["http://target.invalid/redirect"]


def test_the_proxy_path_honours_the_timeout(pinned_server: int) -> None:
    started = time.monotonic()
    with pytest.raises(OSError):
        _send_blocking(_request("http://target.invalid/slow", proxy_url=f"http://{PINNED}:{pinned_server}",
                                timeout_s=0.3))
    assert time.monotonic() - started < 1.2


def test_an_https_url_is_tunnelled_through_the_proxy(pinned_server: int) -> None:
    with pytest.raises(OSError):
        _send_blocking(_request("https://target.invalid/echo", proxy_url=f"http://{PINNED}:{pinned_server}"))
    assert [(s["method"], s["path"]) for s in _Handler.seen] == [("CONNECT", "target.invalid:443")]


# ── exec_http_hook edges ─────────────────────────────────────────────────────

def test_the_guarded_lookup_resolves_the_hostname_it_is_given() -> None:
    asked: list[str] = []
    captured: list[HttpRequest] = []

    async def transport(request: HttpRequest) -> Any:
        captured.append(request)
        assert request.lookup is not None
        request.lookup("api.example")
        return SimpleNamespace(status=200, body="")

    def resolver(host: str) -> list[tuple[str, int]]:
        asked.append(host)
        return [("8.8.8.8", 4)]

    asyncio.run(exec_http_hook(HttpHook(url="https://api.example/h"), "Stop", "{}", None,
                               policy=HttpHookPolicy(), transport=transport, resolver=resolver, env={}))
    assert asked == ["api.example"]


def test_an_abort_mid_request_reports_an_empty_body() -> None:
    class Token:
        cancelled = False

    token = Token()

    async def slow(request: HttpRequest) -> Any:
        await asyncio.sleep(5)

    async def go() -> Any:
        task = asyncio.ensure_future(exec_http_hook(HttpHook(url="https://ok.test/h"), "Stop", "{}", token,
                                                    policy=HttpHookPolicy(), transport=slow, env={}))
        await asyncio.sleep(0.1)
        token.cancelled = True
        return await task

    res = asyncio.run(go())
    assert (res.ok, res.status_code, res.body, res.error, res.aborted) == (False, None, "", None, True)


def test_an_error_after_an_abort_reports_an_empty_body() -> None:
    signal = SimpleNamespace(aborted=False)

    async def boom(request: HttpRequest) -> Any:
        signal.aborted = True
        raise RuntimeError("boom")

    res = asyncio.run(exec_http_hook(HttpHook(url="https://ok.test/h"), "Stop", "{}", signal,
                                     policy=HttpHookPolicy(), transport=boom, env={}))
    assert (res.body, res.aborted) == ("", True)


def test_a_hostless_url_matches_no_no_proxy_entry() -> None:
    assert should_bypass_proxy("http:///x", "xxxx") is False


# ── SSRF guard edges ─────────────────────────────────────────────────────────

def test_a_mapped_address_with_a_255_octet_is_still_checked() -> None:
    assert expand_ipv6_groups("::ffff:10.0.0.255") == [0, 0, 0, 0, 0, 0xFFFF, 0x0A00, 0x00FF]
    assert is_blocked_address("::ffff:10.0.0.255") is True


def test_groups_before_the_double_colon_are_split_on_colons() -> None:
    assert expand_ipv6_groups("fe80:1::5") == [0xFE80, 1, 0, 0, 0, 0, 0, 5]


def test_the_resolver_is_asked_about_the_hostname() -> None:
    asked: list[str] = []

    def resolver(host: str) -> list[tuple[str, int]]:
        asked.append(host)
        return [("8.8.8.8", 4)]

    ssrf_guarded_lookup("svc.example", resolver)
    assert asked == ["svc.example"]


# ── exec_command ─────────────────────────────────────────────────────────────

def _sh(tmp_path: Path, body: str) -> str:
    path = tmp_path / "hook.sh"
    path.write_text(body)
    return f"sh {path}"


def test_only_the_first_line_decides_async(tmp_path: Path) -> None:
    hook = CommandHook(command=_sh(tmp_path, "printf '{\"async\":true}\\nmore\\n'; sleep 2"))
    registry = AsyncHookRegistry()

    async def go() -> Any:
        started = time.monotonic()
        result = await exec_command_hook(hook, "PostToolUse", "PostToolUse:Bash", "{}", None,
                                         cwd=str(tmp_path), project_dir=str(tmp_path), registry=registry)
        elapsed = time.monotonic() - started
        pending = registry.get_pending()
        await registry.finalize_pending()
        return result, elapsed, pending

    result, elapsed, pending = asyncio.run(go())
    assert elapsed < 1.5
    assert result.status == 0 and result.backgrounded is False
    assert result.stderr == "" and result.output == result.stdout
    [hook_entry] = pending
    assert hook_entry.process_id.startswith("async_hook_") and hook_entry.process_id[11:].isdigit()
    assert (hook_entry.hook_name, hook_entry.hook_event, hook_entry.command) == (
        "PostToolUse:Bash", "PostToolUse", hook.command)


def test_an_early_async_return_carries_stderr_and_output(tmp_path: Path) -> None:
    hook = CommandHook(command=_sh(tmp_path, "printf err >&2; sleep 0.2; printf '{\"async\":true}\\n'; sleep 2"))
    registry = AsyncHookRegistry()

    async def go() -> Any:
        result = await exec_command_hook(hook, "PostToolUse", "PostToolUse:Bash", "{}", None,
                                         cwd=str(tmp_path), project_dir=str(tmp_path), registry=registry)
        await registry.finalize_pending()
        return result

    result = asyncio.run(go())
    assert result.stderr == "err"
    assert sorted(result.output) == sorted('err{"async":true}\n')


def test_a_config_async_hook_gets_a_generated_hook_id(tmp_path: Path) -> None:
    hook = CommandHook(command=_sh(tmp_path, "sleep 2"), run_async=True)
    registry = AsyncHookRegistry()

    async def go() -> Any:
        await exec_command_hook(hook, "PostToolUse", "PostToolUse:Bash", "{}", None,
                                cwd=str(tmp_path), project_dir=str(tmp_path), registry=registry)
        pending = registry.get_pending()
        await registry.finalize_pending()
        return pending

    [entry] = asyncio.run(go())
    assert entry.hook_id != "None" and len(entry.hook_id) == 36


def test_a_timed_out_hook_reports_what_it_wrote(tmp_path: Path) -> None:
    hook = CommandHook(command=_sh(tmp_path, "printf out; printf err >&2; sleep 5"), timeout=0.4)
    result = asyncio.run(exec_command_hook(hook, "Stop", "Stop", "{}", None,
                                           cwd=str(tmp_path), project_dir=str(tmp_path)))
    assert result.aborted is True
    assert (result.stdout, result.stderr) == ("out", "err")
    assert sorted(result.output) == sorted("outerr")


# ── process capture ──────────────────────────────────────────────────────────

def test_large_stderr_is_captured_whole() -> None:
    async def go() -> Any:
        process = await asyncio.create_subprocess_shell(
            "head -c 200000 /dev/zero | tr '\\0' e >&2",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        capture = CapturedProcess(process)
        await capture.result()
        return capture

    capture = asyncio.run(go())
    assert capture.stderr == "e" * 200000


def test_the_capture_is_complete_when_the_process_is_reported_done() -> None:
    async def go() -> Any:
        process = await asyncio.create_subprocess_shell(
            "head -c 300000 /dev/zero | tr '\\0' o",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        capture = CapturedProcess(process)
        await capture.result()
        return len(capture.stdout)

    assert asyncio.run(go()) == 300000


# ── async registry ───────────────────────────────────────────────────────────

class _Capture:
    def __init__(self, status: str, stdout: str = "") -> None:
        self.status, self.stdout, self.stderr, self.exit_code = status, stdout, "", 0

    def kill(self) -> None:
        self.status = "killed"

    async def result(self) -> int:
        return 0


def _register(registry: AsyncHookRegistry, pid: str, capture: Any) -> None:
    registry.register_pending(process_id=pid, hook_id=pid, async_response={"async": True}, hook_name="H",
                              hook_event="PostToolUse", command="c", capture=capture)


def test_a_running_hook_does_not_hide_a_finished_one_after_it() -> None:
    registry = AsyncHookRegistry()
    _register(registry, "a", _Capture("running"))
    _register(registry, "b", _Capture("completed", '{"systemMessage": "done"}\n'))
    responses = asyncio.run(registry.check_for_responses())
    assert [r.process_id for r in responses] == ["b"]


def test_an_empty_finished_hook_does_not_hide_the_next_one() -> None:
    registry = AsyncHookRegistry()
    _register(registry, "a", _Capture("completed", "  \n"))
    _register(registry, "b", _Capture("completed", '{"systemMessage": "done"}\n'))
    responses = asyncio.run(registry.check_for_responses())
    assert [r.process_id for r in responses] == ["b"]


def test_a_finished_rewake_watcher_is_forgotten(tmp_path: Path) -> None:
    registry = AsyncHookRegistry()

    async def go() -> Any:
        process = await asyncio.create_subprocess_shell(
            "true", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        capture = CapturedProcess(process)
        registry.register_rewake(hook_name="H", capture=capture)
        await capture.result()
        for _ in range(20):
            await asyncio.sleep(0.01)
        return set(registry._rewake_tasks)

    assert asyncio.run(go()) == set()


# ── matching ─────────────────────────────────────────────────────────────────

def _tool(**kwargs: Any) -> Any:
    return SimpleNamespace(**kwargs)


def test_a_missing_tool_name_never_matches_a_named_condition() -> None:
    matcher = prepare_if_condition_matcher({"hook_event_name": "PreToolUse"}, [])
    assert matcher is not None
    assert matcher("None") is False
    assert matcher("XXXX") is False
    assert matcher("") is True


def test_an_input_failing_the_schema_matches_no_content_condition() -> None:
    tool = _tool(
        name="Bash",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        prepare_permission_matcher=lambda args: (lambda content: True),
    )
    hook_input = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": 5}}
    matcher = prepare_if_condition_matcher(hook_input, [tool])
    assert matcher is not None
    assert matcher("Bash(git *)") is False
    assert matcher("Bash") is True


def test_a_tool_without_schema_or_matcher_attributes_is_tolerated() -> None:
    bare = _tool(name="Bash")
    hook_input = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    matcher = prepare_if_condition_matcher(hook_input, [bare])
    assert matcher is not None
    assert matcher("Bash(ls)") is False


def test_find_tool_tolerates_tools_without_names_or_aliases() -> None:
    nameless = _tool()
    named = _tool(name="Edit")
    assert _find_tool([nameless, named], "Edit") is named
    assert _find_tool([nameless, named], "Missing") is None


def _snapshot(*hooks: Any) -> HooksConfigSnapshot:
    return HooksConfigSnapshot(hooks={"PreToolUse": (HookMatcher(hooks=tuple(hooks)),)})


def test_hooks_differing_only_in_their_if_condition_are_distinct() -> None:
    hook_input = {"hook_event_name": "PreToolUse", "tool_name": "XXXX", "tool_input": {}}
    with_condition = CommandHook(command="vet.sh", if_condition="XXXX")
    without = CommandHook(command="vet.sh")
    matched = get_matching_hooks(_snapshot(without, with_condition), "PreToolUse", hook_input)
    assert [m.hook for m in matched] == [without, with_condition]
    http_a = HttpHook(url="https://h.test/a", if_condition="XXXX")
    http_b = HttpHook(url="https://h.test/a")
    matched = get_matching_hooks(_snapshot(http_b, http_a), "PreToolUse", hook_input)
    assert [m.hook for m in matched] == [http_b, http_a]


# ── parsing edges ────────────────────────────────────────────────────────────

def test_a_malformed_event_does_not_stop_later_events_from_parsing() -> None:
    parsed = parse_hooks_settings({
        "PreToolUse": "not a list",
        "Stop": [{"hooks": [{"type": "command", "command": "x"}]}],
    })
    assert list(parsed) == ["Stop"]


def test_every_validation_issue_is_its_own_line() -> None:
    _, error = validate_hook_json('{"continue": "yes", "reason": 5}')
    assert error is not None
    assert error.startswith(
        "Hook JSON output validation failed:\n  - continue: Expected boolean\n  - reason: Expected string\n\n"
    )
    assert json.loads(error.split("The hook's output was: ", 1)[1]) == {"continue": "yes", "reason": 5}


def test_reorder_tolerates_messages_without_a_type() -> None:
    plain: Any = SimpleNamespace(content="x")
    assert reorder_attachments_for_api([plain]) == [plain]
    result = ToolMessage(content="r", tool_call_id="c1")
    typeless_attachment: Any = SimpleNamespace(additional_kwargs={"attachment": {"type": "hook_success"}})
    assert reorder_attachments_for_api([result, plain]) == [result, plain]
    assert reorder_attachments_for_api([result, typeless_attachment]) == [result, typeless_attachment]


def test_hooks_with_different_non_empty_if_conditions_stay_distinct() -> None:
    tool = _tool(name="Bash", input_schema=None, prepare_permission_matcher=lambda args: (lambda content: True))
    hook_input = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    a = CommandHook(command="vet.sh", if_condition="Bash(a)")
    b = CommandHook(command="vet.sh", if_condition="Bash(b)")
    matched = get_matching_hooks(_snapshot(a, b), "PreToolUse", hook_input, [tool])
    assert [m.hook for m in matched] == [a, b]
