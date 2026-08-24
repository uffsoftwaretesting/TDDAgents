"""
RunTests, HostRead, WebSearch, WebFetch, TodoWrite, and the assembled roster.

The web tools are exercised against a transport stub rather than the network: what is
under test is how a response is turned into something a model can read, and hitting a real
endpoint would test the endpoint.
"""

from __future__ import annotations

import httpx
import pytest

from app.config.config import Config
from app.tools.base import Capability, execute_tool
from app.tools.host_read import MAX_HOST_FILE_BYTES, HostRead
from app.tools.roster import ALL_TOOLS, EXPECTED_TOOL_COUNT, default_registry
from app.tools.run_tests import RunTests
from app.tools.todo import TodoWrite
from app.tools.web import WebFetch, WebSearch, html_to_markdown
from tests.test_tools_exec import FakeAdapter, FakeSandboxWorkspace


def run(tool, args, ctx):
    return execute_tool(tool, args, ctx)


class RecordingAdapter(FakeAdapter):
    """Adds the `execute` surface RunTests needs on top of the exec-test fake."""

    def __init__(self, stdout="", stderr="", exit_code=0):
        super().__init__()
        self.stdout, self.stderr, self.exit_code = stdout, stderr, exit_code
        self.commands: list[str] = []
        self.timeouts: list[float | None] = []

    def execute(self, cmd, timeout=None, env=None, cwd=None):
        from app.workspace.base import CommandResult

        self.commands.append(cmd)
        self.timeouts.append(timeout)
        if "import pytest" in cmd:
            return CommandResult(stdout="", stderr="", exit_code=0, duration=0.0, workspace="sandbox")
        return CommandResult(
            stdout=self.stdout, stderr=self.stderr, exit_code=self.exit_code,
            duration=0.0, workspace="sandbox",
        )


def sandbox_ctx_with(make_tool_ctx, adapter):
    return make_tool_ctx(workspace=FakeSandboxWorkspace(adapter))


class TestRunTests:
    def test_passing_run(self, make_tool_ctx):
        adapter = RecordingAdapter(stdout="3 passed", exit_code=0)
        result = run(RunTests, {}, sandbox_ctx_with(make_tool_ctx, adapter))
        assert result.is_error is False
        assert result.exit_code == 0
        assert "All tests passed" in result.content
        assert "3 passed" in result.content

    def test_a_failing_run_is_a_result_not_an_error(self, make_tool_ctx):
        """In TDD a red test is frequently the point; it must not look like a malfunction."""
        adapter = RecordingAdapter(stdout="1 failed", exit_code=1)
        result = run(RunTests, {}, sandbox_ctx_with(make_tool_ctx, adapter))
        assert result.is_error is False
        assert result.exit_code == 1
        assert "Tests failed (exit 1)" in result.content

    def test_stderr_is_included_when_present(self, make_tool_ctx):
        adapter = RecordingAdapter(stdout="out", stderr="collection error", exit_code=2)
        content = run(RunTests, {}, sandbox_ctx_with(make_tool_ctx, adapter)).content
        assert "STDERR" in content
        assert "collection error" in content

    def test_uses_the_shared_pytest_flags(self, make_tool_ctx):
        from app.agents.langgraph.runner import _PYTEST_FLAGS

        adapter = RecordingAdapter()
        run(RunTests, {}, sandbox_ctx_with(make_tool_ctx, adapter))
        assert any(_PYTEST_FLAGS in cmd for cmd in adapter.commands)

    def test_uses_the_test_timeout_not_the_command_timeout(self, make_tool_ctx):
        adapter = RecordingAdapter()
        run(RunTests, {}, sandbox_ctx_with(make_tool_ctx, adapter))
        assert Config.TEST_TIMEOUT in adapter.timeouts

    def test_ensures_pytest_is_installed_first(self, make_tool_ctx):
        adapter = RecordingAdapter()
        run(RunTests, {}, sandbox_ctx_with(make_tool_ctx, adapter))
        assert "import pytest" in adapter.commands[0]

    def test_test_path_is_honored(self, make_tool_ctx):
        adapter = RecordingAdapter()
        run(RunTests, {"test_path": "tests/test_a.py"}, sandbox_ctx_with(make_tool_ctx, adapter))
        assert any("tests/test_a.py" in cmd for cmd in adapter.commands)

    def test_is_sandbox_pinned(self):
        assert RunTests.required_workspace(RunTests.args_schema()) == "sandbox"

    def test_is_exempt_from_the_capability_ladder(self, make_tool_ctx):
        """
        The refactorer holds RunTests at workspace_write. The exemption is what reconciles
        that with 'only full admits execution', and it is safe because RunTests runs no
        agent-authored command.
        """
        assert RunTests.required_capability(RunTests.args_schema()) is Capability.READ

        adapter = RecordingAdapter(stdout="ok")
        ctx = make_tool_ctx(
            workspace=FakeSandboxWorkspace(adapter), permission_mode="workspace_write"
        )
        assert run(RunTests, {}, ctx).is_error is False

    def test_reports_clearly_without_a_sandbox(self, make_tool_ctx, local_ws):
        ctx = make_tool_ctx(workspace=local_ws, workspace_spec="both")
        result = run(RunTests, {}, ctx)
        assert result.is_error is True
        assert "sandbox workspace" in result.content

    def test_an_infrastructure_failure_is_a_tool_error(self, make_tool_ctx):
        from app.workspace.base import WorkspaceError

        adapter = RecordingAdapter()

        def _boom(cmd, timeout=None, env=None, cwd=None):
            raise WorkspaceError("sandbox expired")

        adapter.execute = _boom  # type: ignore[method-assign]
        result = run(RunTests, {}, sandbox_ctx_with(make_tool_ctx, adapter))
        assert result.is_error is True
        assert "sandbox expired" in result.content


class TestHostRead:
    @pytest.fixture
    def host_ctx(self, make_tool_ctx):
        return make_tool_ctx(workspace_spec="local")

    def test_reads_an_absolute_path_with_line_numbers(self, host_ctx, tmp_path):
        target = tmp_path / "notes.txt"
        target.write_text("alpha\nbeta", encoding="utf-8")
        result = run(HostRead, {"path": str(target)}, host_ctx)
        assert result.content == "1\talpha\n2\tbeta"

    def test_reaches_outside_the_workspace_root(self, host_ctx, tmp_path):
        """The whole point: LocalWorkspace refuses `..`, and HostRead must not."""
        outside = tmp_path.parent / "outside-the-root.txt"
        outside.write_text("reachable", encoding="utf-8")
        try:
            assert "reachable" in run(HostRead, {"path": str(outside)}, host_ctx).content
        finally:
            outside.unlink()

    def test_missing_file(self, host_ctx, tmp_path):
        result = run(HostRead, {"path": str(tmp_path / "nope")}, host_ctx)
        assert result.is_error is True
        assert "No such file" in result.content

    def test_a_directory_is_rejected(self, host_ctx, tmp_path):
        result = run(HostRead, {"path": str(tmp_path)}, host_ctx)
        assert result.is_error is True
        assert "directory" in result.content

    def test_an_oversized_file_is_refused(self, host_ctx, tmp_path):
        big = tmp_path / "big.bin"
        big.write_text("x" * (MAX_HOST_FILE_BYTES + 1), encoding="utf-8")
        result = run(HostRead, {"path": str(big)}, host_ctx)
        assert result.is_error is True
        assert "larger than" in result.content

    def test_invalid_utf8_is_replaced_not_fatal(self, host_ctx, tmp_path):
        target = tmp_path / "binary.dat"
        target.write_bytes(b"before \xff\xfe after")
        result = run(HostRead, {"path": str(target)}, host_ctx)
        assert result.is_error is False
        assert "before" in result.content

    def test_paging(self, host_ctx, tmp_path):
        target = tmp_path / "long.txt"
        target.write_text("\n".join(str(i) for i in range(50)), encoding="utf-8")
        result = run(HostRead, {"path": str(target), "offset": 10, "limit": 2}, host_ctx)
        assert "11\t10" in result.content
        assert "38 more line(s)" in result.content

    def test_empty_file(self, host_ctx, tmp_path):
        target = tmp_path / "empty.txt"
        target.write_text("", encoding="utf-8")
        assert "empty" in run(HostRead, {"path": str(target)}, host_ctx).content

    def test_expands_a_tilde(self, host_ctx, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "notes.txt").write_text("home file", encoding="utf-8")
        assert "home file" in run(HostRead, {"path": "~/notes.txt"}, host_ctx).content

    def test_an_error_echoes_the_path_the_agent_typed(self, host_ctx):
        """Reporting the expanded path back would not match what the agent asked for."""
        result = run(HostRead, {"path": "~/definitely-not-a-real-file-xyz"}, host_ctx)
        assert "~/definitely-not-a-real-file-xyz" in result.content

    def test_is_pinned_to_local_and_read_only(self):
        args = HostRead.args_schema(path="/etc/hostname")
        assert HostRead.required_workspace(args) == "local"
        assert HostRead.is_read_only(args) is True
        assert HostRead.required_capability(args) is Capability.READ

    def test_a_sandbox_agent_is_refused_it(self, make_tool_ctx, tmp_path):
        """The workspace field is the boundary, and this is where it is enforced."""
        target = tmp_path / "a.txt"
        target.write_text("secret", encoding="utf-8")
        ctx = make_tool_ctx(workspace_spec="sandbox")
        result = run(HostRead, {"path": str(target)}, ctx)
        assert result.is_error is True
        assert "workspace 'sandbox'" in result.content


class TestTodoWrite:
    def test_renders_a_checklist(self, tool_ctx):
        result = run(
            TodoWrite,
            {"todos": [
                {"content": "Write the test", "status": "completed"},
                {"content": "Make it pass", "status": "in_progress"},
                {"content": "Refactor", "status": "pending"},
            ]},
            tool_ctx,
        )
        assert "[x] Write the test" in result.content
        assert "[~] Make it pass" in result.content
        assert "[ ] Refactor" in result.content
        assert "1/3 complete" in result.content

    def test_stores_the_list_on_the_context(self, tool_ctx):
        run(TodoWrite, {"todos": [{"content": "A"}]}, tool_ctx)
        assert tool_ctx.todos == [{"content": "A", "status": "pending"}]

    def test_replaces_rather_than_appends(self, tool_ctx):
        run(TodoWrite, {"todos": [{"content": "A"}, {"content": "B"}]}, tool_ctx)
        run(TodoWrite, {"todos": [{"content": "C"}]}, tool_ctx)
        assert [t["content"] for t in tool_ctx.todos] == ["C"]

    def test_an_empty_list_is_rejected(self, tool_ctx):
        result = run(TodoWrite, {"todos": []}, tool_ctx)
        assert result.is_error is True
        assert "must not be empty" in result.content

    def test_two_in_progress_items_are_rejected(self, tool_ctx):
        result = run(
            TodoWrite,
            {"todos": [
                {"content": "A", "status": "in_progress"},
                {"content": "B", "status": "in_progress"},
            ]},
            tool_ctx,
        )
        assert result.is_error is True
        assert "one at a time" in result.content

    def test_an_invalid_status_is_a_schema_error(self, tool_ctx):
        result = run(TodoWrite, {"todos": [{"content": "A", "status": "done"}]}, tool_ctx)
        assert result.is_error is True

    def test_it_touches_no_workspace_and_triggers_no_sync(self, make_tool_ctx):
        class _Engine:
            calls = 0

            def reconcile_ledger(self, ledger):
                _Engine.calls += 1
                return dict(ledger), object()

        ctx = make_tool_ctx(sync_engine=_Engine())
        run(TodoWrite, {"todos": [{"content": "A"}]}, ctx)
        assert _Engine.calls == 0
        assert ctx.workspace.files == {}


class TestWebSearch:
    def test_is_disabled_without_a_key(self, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", None)
        assert WebSearch.is_enabled() is False

    def test_is_enabled_with_a_key(self, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "tvly-x")
        assert WebSearch.is_enabled() is True

    def test_a_disabled_tool_never_runs(self, tool_ctx, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", None)
        result = run(WebSearch, {"query": "x"}, tool_ctx)
        assert result.is_error is True
        assert "not available" in result.content

    def _stub_post(self, monkeypatch, payload=None, exc=None):
        def _post(url, json=None, timeout=None):
            if exc is not None:
                raise exc
            request = httpx.Request("POST", url)
            return httpx.Response(200, json=payload, request=request)

        monkeypatch.setattr(httpx, "post", _post)

    def test_renders_results(self, tool_ctx, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "tvly-x")
        self._stub_post(monkeypatch, {
            "answer": "Short summary.",
            "results": [
                {"title": "Docs", "url": "https://x/docs", "content": "How to do it."},
            ],
        })
        content = run(WebSearch, {"query": "how"}, tool_ctx).content
        assert "Summary: Short summary." in content
        assert "1. Docs" in content
        assert "https://x/docs" in content
        assert "How to do it." in content

    def test_no_results(self, tool_ctx, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "tvly-x")
        self._stub_post(monkeypatch, {"results": []})
        assert "No results" in run(WebSearch, {"query": "zzz"}, tool_ctx).content

    def test_a_transport_error_is_a_tool_error(self, tool_ctx, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "tvly-x")
        self._stub_post(monkeypatch, exc=httpx.ConnectError("no route"))
        result = run(WebSearch, {"query": "x"}, tool_ctx)
        assert result.is_error is True
        assert "Search failed" in result.content

    def test_max_results_is_clamped(self, tool_ctx, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "tvly-x")
        seen: dict[str, object] = {}

        def _post(url, json=None, timeout=None):
            seen.update(json or {})
            return httpx.Response(200, json={"results": []}, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", _post)
        run(WebSearch, {"query": "x", "max_results": 99}, tool_ctx)
        assert seen["max_results"] == 10

    def test_is_read_only(self):
        args = WebSearch.args_schema(query="x")
        assert WebSearch.is_read_only(args) is True
        assert WebSearch.required_capability(args) is Capability.READ


class TestWebFetch:
    def _stub_get(self, monkeypatch, *, text="", content_type="text/html", status=200, exc=None):
        def _get(url, timeout=None, follow_redirects=None, headers=None):
            if exc is not None:
                raise exc
            request = httpx.Request("GET", url)
            return httpx.Response(
                status, text=text, headers={"content-type": content_type}, request=request
            )

        monkeypatch.setattr(httpx, "get", _get)

    def test_converts_html_to_markdown(self, tool_ctx, monkeypatch):
        self._stub_get(monkeypatch, text="<html><body><h1>Title</h1><p>Body text.</p></body></html>")
        content = run(WebFetch, {"url": "https://x/page"}, tool_ctx).content
        assert "# Title" in content
        assert "Body text." in content

    def test_non_html_is_returned_untouched(self, tool_ctx, monkeypatch):
        self._stub_get(monkeypatch, text='{"a": 1}', content_type="application/json")
        assert run(WebFetch, {"url": "https://x/a.json"}, tool_ctx).content == '{"a": 1}'

    def test_a_non_http_scheme_is_rejected(self, tool_ctx):
        result = run(WebFetch, {"url": "file:///etc/passwd"}, tool_ctx)
        assert result.is_error is True
        assert "http and https" in result.content

    def test_an_http_error_is_a_tool_error(self, tool_ctx, monkeypatch):
        self._stub_get(monkeypatch, text="nope", status=404)
        result = run(WebFetch, {"url": "https://x/missing"}, tool_ctx)
        assert result.is_error is True
        assert "404" in result.content

    def test_a_transport_error_is_a_tool_error(self, tool_ctx, monkeypatch):
        self._stub_get(monkeypatch, exc=httpx.ConnectError("no route"))
        result = run(WebFetch, {"url": "https://x/"}, tool_ctx)
        assert result.is_error is True
        assert "Fetch failed" in result.content

    def test_is_read_only(self):
        args = WebFetch.args_schema(url="https://x/")
        assert WebFetch.is_read_only(args) is True
        assert WebFetch.is_concurrency_safe(args) is True


class TestHtmlToMarkdown:
    def test_strips_scripts_and_styles(self):
        html = "<body><script>evil()</script><style>p{}</style><p>Kept.</p></body>"
        out = html_to_markdown(html)
        assert "evil()" not in out
        assert "p{}" not in out
        assert "Kept." in out

    def test_strips_navigation_chrome(self):
        html = "<body><nav>Home About</nav><p>Content.</p><footer>(c) 2026</footer></body>"
        out = html_to_markdown(html)
        assert "Home About" not in out
        assert "(c) 2026" not in out
        assert "Content." in out

    def test_headings_use_atx_style(self):
        assert "## Sub" in html_to_markdown("<body><h2>Sub</h2></body>")

    def test_links_are_preserved(self):
        out = html_to_markdown('<body><a href="https://x/">Link</a></body>')
        assert "https://x/" in out

    def test_blank_line_runs_are_collapsed(self):
        out = html_to_markdown("<body><p>A</p><script>x</script><p>B</p></body>")
        assert "\n\n\n" not in out

    def test_empty_document(self):
        assert html_to_markdown("<html></html>") == ""


class TestRoster:
    def test_the_roster_is_eighteen_tools(self):
        assert len(ALL_TOOLS) == EXPECTED_TOOL_COUNT
        assert len(default_registry()) == EXPECTED_TOOL_COUNT

    def test_names_are_unique(self):
        names = [tool.name for tool in ALL_TOOLS]
        assert len(names) == len(set(names))

    def test_the_expected_names_are_present(self):
        assert set(default_registry().names()) == {
            "ReadFile", "ListDir", "Glob", "Grep", "WriteFile", "Edit", "MultiEdit",
            "Delete", "Move", "Bash", "BashOutput", "KillShell", "RunCode", "RunTests",
            "HostRead", "WebSearch", "WebFetch", "TodoWrite",
        }

    def test_agent_and_skill_are_not_in_the_roster(self):
        """Phase 5 and Phase 4 respectively; neither ships here."""
        names = default_registry().names()
        assert "Agent" not in names
        assert "Skill" not in names

    def test_every_tool_has_a_model_facing_prompt(self):
        for tool in ALL_TOOLS:
            assert tool.prompt().strip(), f"{tool.name} has no prompt"

    def test_every_tool_has_a_schema_the_model_can_read(self):
        for tool in ALL_TOOLS:
            schema = tool.args_schema.model_json_schema()
            assert schema.get("properties") is not None or schema.get("type") == "object"

    def test_every_tool_converts_for_bind_tools(self):
        from app.tools.langchain import to_langchain_tools

        converted = to_langchain_tools(ALL_TOOLS)
        assert len(converted) == EXPECTED_TOOL_COUNT
        assert all(c["function"]["name"] for c in converted)

    def test_read_tools_are_concurrency_safe_and_write_tools_are_not(self):
        registry = default_registry()
        for name in ("ReadFile", "ListDir", "Glob", "Grep", "HostRead"):
            tool = registry.get(name)
            assert tool is not None
            args = tool.args_schema.model_construct()
            assert tool.is_concurrency_safe(args) is True, name

        for name in ("WriteFile", "Edit", "MultiEdit", "Delete", "Move"):
            tool = registry.get(name)
            assert tool is not None
            args = tool.args_schema.model_construct()
            assert tool.is_concurrency_safe(args) is False, name

    def test_the_three_sandbox_pins_hold(self):
        """RunTests, BashOutput and KillShell stay sandbox-only whatever an agent declares."""
        registry = default_registry()
        for name in ("RunTests", "BashOutput", "KillShell"):
            tool = registry.get(name)
            assert tool is not None
            assert tool.required_workspace(tool.args_schema.model_construct()) == "sandbox", name

    def test_only_host_read_asks_for_the_local_workspace(self):
        registry = default_registry()
        local = [
            tool.name
            for tool in registry.all()
            if tool.required_workspace(tool.args_schema.model_construct()) == "local"
        ]
        assert local == ["HostRead"]

    def test_nothing_defaults_to_a_read_capability_by_accident(self):
        """Every tool states its capability explicitly rather than inheriting the default."""
        registry = default_registry()
        writers = {"WriteFile", "Edit", "MultiEdit", "Delete", "Move"}
        for name in writers:
            tool = registry.get(name)
            assert tool is not None
            assert tool.required_capability(tool.args_schema.model_construct()) is Capability.WRITE


class TestTavilyRequestContract:
    """
    The request body is a contract with the provider. A renamed key does not fail loudly —
    Tavily just ignores it — so the field names are worth pinning.
    """

    def _capture(self, monkeypatch, **overrides):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "tvly-secret")
        seen: dict[str, object] = {}

        def _post(url, json=None, timeout=None):
            seen["url"] = url
            seen["payload"] = json
            seen["timeout"] = timeout
            return httpx.Response(200, json={"results": []}, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", _post)
        return seen

    def test_the_payload_uses_the_documented_field_names(self, tool_ctx, monkeypatch):
        seen = self._capture(monkeypatch)
        run(WebSearch, {"query": "how to x", "max_results": 3}, tool_ctx)
        assert seen["payload"] == {
            "api_key": "tvly-secret",
            "query": "how to x",
            "max_results": 3,
            "search_depth": "basic",
            "include_answer": True,
        }

    def test_it_posts_to_the_documented_endpoint(self, tool_ctx, monkeypatch):
        from app.tools.web import TAVILY_ENDPOINT

        seen = self._capture(monkeypatch)
        run(WebSearch, {"query": "x"}, tool_ctx)
        assert seen["url"] == TAVILY_ENDPOINT

    def test_a_timeout_is_always_set(self, tool_ctx, monkeypatch):
        """Without one, httpx waits forever and a tool call can hang a whole run."""
        from app.tools.web import WEB_TIMEOUT

        seen = self._capture(monkeypatch)
        run(WebSearch, {"query": "x"}, tool_ctx)
        assert seen["timeout"] == WEB_TIMEOUT

    def test_max_results_is_clamped_up_to_one(self, tool_ctx, monkeypatch):
        seen = self._capture(monkeypatch)
        run(WebSearch, {"query": "x", "max_results": 0}, tool_ctx)
        assert seen["payload"]["max_results"] == 1

    def test_the_default_max_results(self, tool_ctx, monkeypatch):
        seen = self._capture(monkeypatch)
        run(WebSearch, {"query": "x"}, tool_ctx)
        assert seen["payload"]["max_results"] == 5


class TestWebErrorMessages:
    """Exact wording, because it is the only thing the model gets back on failure."""

    def test_a_status_error_names_the_code(self, tool_ctx, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "k")

        def _post(url, json=None, timeout=None):
            return httpx.Response(503, text="down", request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", _post)
        result = run(WebSearch, {"query": "x"}, tool_ctx)
        assert result.content == "Search failed: the provider returned 503."

    def test_malformed_json_has_its_own_message(self, tool_ctx, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "k")

        def _post(url, json=None, timeout=None):
            return httpx.Response(200, text="not json", request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", _post)
        result = run(WebSearch, {"query": "x"}, tool_ctx)
        assert result.content == "Search failed: the provider returned malformed JSON."

    def test_no_results_names_the_query(self, tool_ctx, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "k")

        def _post(url, json=None, timeout=None):
            return httpx.Response(200, json={"results": []}, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", _post)
        assert run(WebSearch, {"query": "zebra"}, tool_ctx).content == "No results for 'zebra'."

    def test_a_non_dict_response_is_treated_as_no_results(self, tool_ctx, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "k")

        def _post(url, json=None, timeout=None):
            return httpx.Response(200, json=[1, 2], request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", _post)
        assert "No results for 'x'" in run(WebSearch, {"query": "x"}, tool_ctx).content

    def test_a_blank_answer_is_omitted(self, tool_ctx, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "k")

        def _post(url, json=None, timeout=None):
            return httpx.Response(
                200,
                json={"answer": "   ", "results": [{"title": "T", "url": "u", "content": "c"}]},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx, "post", _post)
        assert "Summary:" not in run(WebSearch, {"query": "x"}, tool_ctx).content

    def test_a_non_dict_result_entry_is_skipped(self, tool_ctx, monkeypatch):
        monkeypatch.setattr(Config, "TAVILY_API_KEY", "k")

        def _post(url, json=None, timeout=None):
            return httpx.Response(
                200,
                json={"results": ["junk", {"title": "T", "url": "u", "content": "c"}]},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx, "post", _post)
        content = run(WebSearch, {"query": "x"}, tool_ctx).content
        assert "junk" not in content
        assert "1. T" in content


class TestWebFetchRequestContract:
    def test_it_follows_redirects_and_sets_a_timeout_and_agent(self, tool_ctx, monkeypatch):
        from app.tools.web import WEB_TIMEOUT

        seen: dict[str, object] = {}

        def _get(url, timeout=None, follow_redirects=None, headers=None):
            seen.update(timeout=timeout, follow_redirects=follow_redirects, headers=headers)
            return httpx.Response(
                200, text="hi", headers={"content-type": "text/plain"},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(httpx, "get", _get)
        run(WebFetch, {"url": "https://x/"}, tool_ctx)

        assert seen["timeout"] == WEB_TIMEOUT
        assert seen["follow_redirects"] is True
        assert "TDDAgents" in str(seen["headers"])

    def test_an_oversized_response_is_refused(self, tool_ctx, monkeypatch):
        from app.tools.web import MAX_FETCH_BYTES

        def _get(url, timeout=None, follow_redirects=None, headers=None):
            return httpx.Response(
                200, text="x" * (MAX_FETCH_BYTES + 1),
                headers={"content-type": "text/plain"}, request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(httpx, "get", _get)
        result = run(WebFetch, {"url": "https://x/"}, tool_ctx)
        assert result.is_error is True
        assert "larger than" in result.content

    def test_the_scheme_check_is_case_insensitive(self, tool_ctx, monkeypatch):
        def _get(url, timeout=None, follow_redirects=None, headers=None):
            return httpx.Response(
                200, text="ok", headers={"content-type": "text/plain"},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(httpx, "get", _get)
        assert run(WebFetch, {"url": "HTTPS://x/"}, tool_ctx).is_error is False

    def test_the_content_type_check_is_case_insensitive(self, tool_ctx, monkeypatch):
        def _get(url, timeout=None, follow_redirects=None, headers=None):
            return httpx.Response(
                200, text="<body><p>Hi</p></body>", headers={"content-type": "TEXT/HTML"},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(httpx, "get", _get)
        assert run(WebFetch, {"url": "https://x/"}, tool_ctx).content == "Hi"


class TestHtmlChromeStripping:
    """
    Tag names alone are not enough. A live fetch of the pytest docs came back more than
    half navigation, because that theme builds its sidebar out of plain divs carrying ARIA
    roles rather than semantic elements.
    """

    def test_role_based_navigation_is_stripped(self):
        html = '<body><div role="navigation">Nav links</div><p>Real content.</p></body>'
        out = html_to_markdown(html)
        assert "Nav links" not in out
        assert "Real content." in out

    def test_role_based_banner_and_footer_are_stripped(self):
        html = (
            '<body><div role="banner">Site name</div><p>Body.</p>'
            '<div role="contentinfo">Copyright</div></body>'
        )
        out = html_to_markdown(html)
        assert "Site name" not in out
        assert "Copyright" not in out
        assert "Body." in out

    def test_a_complementary_sidebar_is_stripped(self):
        html = '<body><div role="complementary">Related</div><p>Main.</p></body>'
        assert "Related" not in html_to_markdown(html)

    def test_aside_and_form_elements_are_stripped(self):
        html = "<body><aside>Ad</aside><form>Search box</form><p>Kept.</p></body>"
        out = html_to_markdown(html)
        assert "Ad" not in out
        assert "Search box" not in out
        assert "Kept." in out

    def test_content_with_no_chrome_is_untouched(self):
        assert html_to_markdown("<body><p>Just content.</p></body>") == "Just content."
