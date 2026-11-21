"""
WebFetch against the real internet: real HTTP requests to public hosts, and in the end-to-end tests the real
small, fast model (`default_model_for`, upstream's `queryHaiku`).

Live tests (`@pytest.mark.live`): they run only with `RUN_LIVE_LLM_TESTS=1` and a real key in `.env`
(`tests/conftest.py`), so the offline suite stays offline. Each asserts what the public page reliably holds
(example.com's "Example Domain", a PDF's `%PDF` header, a status code), never volatile text.

    RUN_LIVE_LLM_TESTS=1 .venv/bin/pytest tests/integration/test_live_web_fetch.py -v

The hosts and why each is used:

* `example.com` — an HTML page (200), an unknown path (404), `http://` (upgraded to https);
* `httpbin.org/redirect-to` — a 301 within the host (followed) and a 302 to another host (reported);
* `youtu.be` — a 303, which upstream does not follow;
* `nextjs.org/docs.md` — preapproved, short `text/markdown`: returned as is, without the model;
* `vercel.com/docs.md` — `text/markdown` from a host that is not preapproved: through the model;
* `raw.githubusercontent.com` — `text/plain`, passed raw;
* `w3.org`'s dummy PDF — binary content, saved to disk and still summarised.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolCall

from app.hooks.use_can_use_tool import PermissionAnswer, PermissionPrompt, build_interactive_can_use_tool
from app.services.tools.tool_execution import run_tool_use
from app.state.app_state_store import AppState, AppStateStore
from app.tool import tool_context_for
from app.tools.web_fetch_tool import utils as fetch_utils
from app.tools.web_fetch_tool.web_fetch_tool import build_web_fetch_tool
from app.utils.settings.settings import build_tool_permission_context
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace

pytestmark = pytest.mark.live

EXAMPLE = "https://example.com/"
PDF = "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"


@pytest.fixture(autouse=True)
def _fresh_cache() -> Any:
    fetch_utils.clear_web_fetch_cache()
    yield
    fetch_utils.clear_web_fetch_cache()


class Recorder:
    """The small model replaced by a recorder: what the real page put in the model's prompt is observable."""

    def __init__(self, answer: str = "RECORDED") -> None:
        self.prompts: list[str] = []
        self.answer = answer

    def __call__(self, context: Any) -> Any:
        async def ask(prompt: str) -> str:
            self.prompts.append(prompt)
            return self.answer
        return ask


def fetch(tmp_path: Path, url: str, prompt: str = "Summarise the page.", model: Any = None) -> Any:
    tool = build_web_fetch_tool(model_for=model or Recorder())
    ctx = tool_context_for(AppStateStore(), workspace=LocalWorkspace(str(tmp_path / "project")), run_id="live")
    return asyncio.run(tool.call({"url": url, "prompt": prompt}, ctx))


# ── real HTTP, the model recorded ──────────────────────────────────────────────

def test_an_html_page_is_fetched_converted_and_handed_to_the_model(tmp_path: Path) -> None:
    model = Recorder()
    result = fetch(tmp_path, EXAMPLE, "What is the page's title?", model)
    assert result.is_error is False and result.content == "RECORDED"
    assert result.metadata["code"] == 200 and result.metadata["codeText"] == "OK"
    assert result.metadata["url"] == EXAMPLE and result.metadata["bytes"] > 0 and result.metadata["durationMs"] >= 0
    (prompt,) = model.prompts
    assert "Example Domain" in prompt and "<html" not in prompt.lower()
    assert "What is the page's title?" in prompt and "strict 125-character maximum" in prompt


def test_http_is_upgraded_and_the_second_call_is_served_from_the_cache(tmp_path: Path) -> None:
    model = Recorder()
    first = fetch(tmp_path, "http://example.com/", model=model)
    cached = fetch_utils.URL_CACHE.get("http://example.com/")
    assert first.is_error is False and cached is not None and "Example Domain" in cached.content
    second = fetch(tmp_path, "http://example.com/", model=model)
    assert second.is_error is False and len(model.prompts) == 2 and model.prompts[0] == model.prompts[1]


def test_an_unknown_path_is_the_calls_error(tmp_path: Path) -> None:
    result = fetch(tmp_path, "https://example.com/no-such-page-tddagents")
    assert result.is_error is True and result.content.startswith("Error fetching https://example.com/no-such-page")
    assert "404" in result.content


def test_a_redirect_within_the_host_is_followed(tmp_path: Path) -> None:
    model = Recorder()
    url = "https://httpbin.org/redirect-to?url=https%3A%2F%2Fhttpbin.org%2Fget&status_code=301"
    result = fetch(tmp_path, url, model=model)
    assert result.is_error is False and result.metadata["code"] == 200
    assert '"url": "https://httpbin.org/get"' in model.prompts[0]
    assert "TDDAgents-User (" in model.prompts[0]  # httpbin echoes the request's User-Agent


def test_a_redirect_to_another_host_is_reported_not_followed(tmp_path: Path) -> None:
    model = Recorder()
    url = "https://httpbin.org/redirect-to?url=https%3A%2F%2Fexample.com%2F&status_code=302"
    result = fetch(tmp_path, url, "Find the title.", model)
    assert result.is_error is False and model.prompts == []
    assert result.content.startswith("REDIRECT DETECTED: The URL redirects to a different host.")
    assert "Redirect URL: https://example.com/" in result.content and "Status: 302 Found" in result.content
    assert result.content.endswith('- url: "https://example.com/"\n- prompt: "Find the title."')
    assert result.metadata["code"] == 302 and result.metadata["codeText"] == "Found"


def test_a_303_is_not_followed(tmp_path: Path) -> None:
    result = fetch(tmp_path, "https://youtu.be/dQw4w9WgXcQ")
    assert result.is_error is True and result.content.startswith("Error fetching https://youtu.be/")


def test_short_preapproved_markdown_is_returned_without_the_model(tmp_path: Path) -> None:
    model = Recorder()
    result = fetch(tmp_path, "https://nextjs.org/docs.md", model=model)
    fetched = fetch_utils.URL_CACHE.get("https://nextjs.org/docs.md")
    assert result.is_error is False and model.prompts == [] and fetched is not None
    assert "text/markdown" in fetched.content_type and len(fetched.content) < fetch_utils.MAX_MARKDOWN_LENGTH
    assert result.content == fetched.content and "Next.js" in result.content  # the page itself, unchanged


def test_markdown_from_a_host_not_preapproved_goes_through_the_model(tmp_path: Path) -> None:
    model = Recorder()
    result = fetch(tmp_path, "https://vercel.com/docs.md", model=model)
    assert result.content == "RECORDED" and len(model.prompts) == 1
    assert "Vercel" in model.prompts[0] and "strict 125-character maximum" in model.prompts[0]


def test_plain_text_is_passed_raw(tmp_path: Path) -> None:
    model = Recorder()
    result = fetch(tmp_path, "https://raw.githubusercontent.com/python/cpython/main/README.rst", model=model)
    assert result.is_error is False
    assert "This is Python version" in model.prompts[0] and "====" in model.prompts[0]


def test_a_pdf_is_saved_to_disk_and_still_summarised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    model = Recorder()
    result = fetch(tmp_path, PDF, model=model)
    saved = re.search(r"\[Binary content \(application/pdf[^,]*, [^)]+\) also saved to (.+\.pdf)\]", result.content)
    assert result.content.startswith("RECORDED\n\n") and saved is not None
    assert Path(saved.group(1)).read_bytes().startswith(b"%PDF")
    assert "%PDF" in model.prompts[0]


@pytest.mark.parametrize("url", ["https://localhost/", "https://user:secret@example.com/", "https://intranet/"])
def test_a_url_validate_url_refuses_is_never_requested(tmp_path: Path, url: str) -> None:
    result = fetch(tmp_path, url)
    assert result.is_error is True and "Invalid URL" in result.content


# ── end to end: the permission pipeline and the real small model ───────────────

class Asker:
    def __init__(self, *answers: PermissionAnswer) -> None:
        self.answers = list(answers)
        self.prompts: list[PermissionPrompt] = []

    async def __call__(self, prompt: PermissionPrompt) -> PermissionAnswer:
        self.prompts.append(prompt)
        return self.answers.pop(0)


def session(tmp_path: Path, asker: Asker | None) -> Any:
    (tmp_path / "home").mkdir(exist_ok=True)
    perm = build_tool_permission_context(tmp_path, tmp_path / "home", managed_dir=tmp_path / "etc",
                                         permission_mode_cli="default", working_dir=tmp_path / "ws",
                                         should_avoid_permission_prompts=asker is None)
    ws = FakeWorkspace()
    ws.root = tmp_path / "ws"  # type: ignore[attr-defined]
    ctx = tool_context_for(AppStateStore(state=AppState(tool_permission_context=perm)), workspace=ws, run_id="live")
    if asker is not None:
        ctx.can_use_tool = build_interactive_can_use_tool(asker)
    return ctx


def run(tool: Any, url: str, prompt: str, ctx: Any) -> Any:
    ctx.tools = (tool,)
    call = ToolCall(name=tool.name, args={"url": url, "prompt": prompt}, id="live1")
    return asyncio.run(run_tool_use(call, AIMessage(content="", tool_calls=[call]), ctx, (tool,)))


def test_the_real_small_model_answers_about_the_real_page(tmp_path: Path) -> None:
    """The whole tool: real HTTP, the real small model (`default_model_for`), the interactive allow."""
    asker = Asker(PermissionAnswer("yes"))
    outcome = run(build_web_fetch_tool(), EXAMPLE,
                  "What is the exact text of this page's main heading? Answer with the heading only.",
                  session(tmp_path, asker))
    assert outcome.message.status == "success", outcome.message.content
    assert "example domain" in str(outcome.message.content).lower()
    (prompt,) = asker.prompts
    assert [o.value for o in prompt.options] == ["yes", "yes-dont-ask-again-domain", "no"]


def test_dont_ask_again_for_the_domain_then_fetches_it_without_asking(tmp_path: Path) -> None:
    asker = Asker(PermissionAnswer("yes-dont-ask-again-domain"))
    ctx = session(tmp_path, asker)
    tool = build_web_fetch_tool()
    first = run(tool, EXAMPLE, "Answer with the page's main heading only.", ctx)
    second = run(tool, "https://example.com/?again=1", "Answer with the page's main heading only.", ctx)
    assert first.message.status == second.message.status == "success"
    assert len(asker.prompts) == 1
    settings = json.loads((tmp_path / ".tddagents" / "settings.local.json").read_text())
    assert settings["permissions"]["allow"] == ["WebFetch(domain:example.com)"]


def test_a_preapproved_host_is_fetched_without_a_question_and_answered_as_is(tmp_path: Path) -> None:
    asker = Asker()
    outcome = run(build_web_fetch_tool(), "https://nextjs.org/docs.md", "Summarise.", session(tmp_path, asker))
    assert outcome.message.status == "success" and asker.prompts == []
    assert "Next.js" in str(outcome.message.content)


def test_headless_a_domain_without_a_rule_is_denied_and_never_fetched(tmp_path: Path) -> None:
    outcome = run(build_web_fetch_tool(model_for=Recorder()), EXAMPLE, "q", session(tmp_path, None))
    assert outcome.message.status == "error"
    assert fetch_utils.URL_CACHE.get(EXAMPLE) is None
