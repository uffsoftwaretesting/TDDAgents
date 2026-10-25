"""Tests for `app/loop/hooks/async_registry.py`: background hooks and rewake notifications."""

from __future__ import annotations

import asyncio
from typing import Any

from app.loop.hooks.async_registry import (
    DEFAULT_ASYNC_TIMEOUT_MS,
    AsyncHookRegistry,
    AsyncHookResponse,
)
from app.loop.hooks.process import CapturedProcess


class FakeCapture:
    """The fields `check_for_responses` and `finalize_pending` read off a capture."""

    def __init__(self, status: str = "completed", stdout: str = "", stderr: str = "", exit_code: int | None = 0):
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.killed = 0
        self.awaited = 0

    def kill(self) -> None:
        self.killed += 1
        self.status = "killed"

    async def result(self) -> int:
        self.awaited += 1
        return self.exit_code if self.exit_code is not None else 1


def register(registry: AsyncHookRegistry, pid: str, capture: Any, **kwargs: Any) -> None:
    fields: dict[str, Any] = {
        "process_id": pid,
        "hook_id": f"id-{pid}",
        "async_response": {"async": True},
        "hook_name": "PostToolUse:Bash",
        "hook_event": "PostToolUse",
        "command": "notify.sh",
        "capture": capture,
    }
    fields.update(kwargs)
    registry.register_pending(**fields)


def test_default_timeout_and_registration_fields():
    assert DEFAULT_ASYNC_TIMEOUT_MS == 15000
    registry = AsyncHookRegistry()
    capture = FakeCapture(status="running")
    register(registry, "p1", capture, tool_name="Bash")
    register(registry, "p2", capture, async_response={"async": True, "asyncTimeout": 250})
    p1, p2 = registry.get_pending()
    assert (p1.process_id, p1.hook_id, p1.hook_name, p1.hook_event, p1.command) == (
        "p1", "id-p1", "PostToolUse:Bash", "PostToolUse", "notify.sh")
    assert p1.timeout == 15000 and p2.timeout == 250
    assert p1.capture is (capture  # type: ignore[comparison-overlap]
                          ) and p1.tool_name == "Bash" and p2.tool_name is None
    assert p1.response_attachment_sent is False
    assert p1.start_time > 0


def test_running_hooks_are_skipped_and_kept():
    registry = AsyncHookRegistry()
    register(registry, "p1", FakeCapture(status="running", stdout='{"systemMessage": "x"}'))
    assert asyncio.run(registry.check_for_responses()) == []
    assert [h.process_id for h in registry.get_pending()] == ["p1"]


def test_killed_and_captureless_hooks_are_dropped():
    registry = AsyncHookRegistry()
    register(registry, "killed", FakeCapture(status="killed", stdout='{"a": 1}'))
    register(registry, "none", None)
    assert asyncio.run(registry.check_for_responses()) == []
    assert registry.get_pending() == []
    assert asyncio.run(registry.check_for_responses()) == []


def test_completed_hook_without_output_is_dropped_silently():
    registry = AsyncHookRegistry()
    register(registry, "p1", FakeCapture(stdout="  \n"))
    assert asyncio.run(registry.check_for_responses()) == []
    assert registry.get_pending() == []


def test_first_non_async_json_line_is_the_response_and_is_delivered_once():
    registry = AsyncHookRegistry()
    stdout = 'noise\n{"async": true}\n{not json\n  {"systemMessage": "first"}\n{"systemMessage": "second"}\n'
    register(registry, "p1", FakeCapture(stdout=stdout, stderr="warn", exit_code=3), tool_name="Bash")
    [response] = asyncio.run(registry.check_for_responses())
    assert response == AsyncHookResponse(
        process_id="p1",
        response={"systemMessage": "first"},
        hook_name="PostToolUse:Bash",
        hook_event="PostToolUse",
        stdout=stdout,
        stderr="warn",
        exit_code=3,
        tool_name="Bash",
    )
    assert registry.get_pending() == []
    assert asyncio.run(registry.check_for_responses()) == []


def test_completed_hook_with_no_json_line_answers_an_empty_response():
    registry = AsyncHookRegistry()
    register(registry, "p1", FakeCapture(stdout="plain text\n[1, 2]\n"))
    [response] = asyncio.run(registry.check_for_responses())
    assert response.response == {}
    assert response.tool_name is None


def test_json_arrays_and_scalars_are_not_responses():
    registry = AsyncHookRegistry()
    register(registry, "p1", FakeCapture(stdout='{"async": false}\n{"ok": 1}\n'))
    [response] = asyncio.run(registry.check_for_responses())
    # Any object carrying an `async` key is skipped, whatever its value.
    assert response.response == {"ok": 1}


def test_an_already_delivered_hook_is_removed_without_a_second_response():
    registry = AsyncHookRegistry()
    register(registry, "p1", FakeCapture(stdout='{"a": 1}'))
    registry._pending["p1"].response_attachment_sent = True
    assert registry.get_pending() == []
    assert asyncio.run(registry.check_for_responses()) == []
    assert "p1" not in registry._pending


def test_response_attachment_shape():
    response = AsyncHookResponse(
        process_id="p", response={"systemMessage": "s"}, hook_name="Stop", hook_event="Stop",
        stdout="o", stderr="e", exit_code=0, tool_name="Bash",
    )
    assert response.attachment() == {
        "type": "async_hook_response",
        "processId": "p",
        "hookName": "Stop",
        "hookEvent": "Stop",
        "toolName": "Bash",
        "response": {"systemMessage": "s"},
        "stdout": "o",
        "stderr": "e",
        "exitCode": 0,
    }


def test_remove_delivered_only_removes_sent_hooks():
    registry = AsyncHookRegistry()
    register(registry, "sent", FakeCapture(status="running"))
    register(registry, "unsent", FakeCapture(status="running"))
    registry._pending["sent"].response_attachment_sent = True
    registry.remove_delivered(["sent", "unsent", "unknown"])
    assert list(registry._pending) == ["unsent"]


def test_clear_forgets_everything():
    registry = AsyncHookRegistry()
    register(registry, "p1", FakeCapture(status="running"))
    registry.clear()
    assert registry.get_pending() == []


def test_finalize_kills_running_hooks_and_forgets_all():
    registry = AsyncHookRegistry()
    running = FakeCapture(status="running")
    done = FakeCapture(status="completed")
    register(registry, "a", running)
    register(registry, "b", done)
    register(registry, "c", None)
    asyncio.run(registry.finalize_pending())
    assert (running.killed, running.awaited) == (1, 1)
    assert (done.killed, done.awaited) == (0, 0)
    assert registry.get_pending() == []


async def _spawn(command: str) -> CapturedProcess:
    process = await asyncio.create_subprocess_shell(
        command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    capture = CapturedProcess(process)
    await capture.write_stdin("")
    return capture


def test_rewake_exit_two_queues_the_blocking_notification():
    async def go() -> list[str]:
        registry = AsyncHookRegistry()
        registry.register_rewake(hook_name="Stop", capture=await _spawn("echo out; echo bad >&2; exit 2"))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if registry.queue.has_commands():
                break
        return [c.value for c in registry.queue.dequeue_all_matching(lambda c: True)]

    assert asyncio.run(go()) == [
        '<system-reminder>\nStop hook blocking error from command "Stop": bad\n\n</system-reminder>'
    ]


def test_rewake_falls_back_to_stdout_and_drain_empties_the_queue():
    async def go() -> tuple[list[str], list[str]]:
        registry = AsyncHookRegistry()
        capture = await _spawn("echo only-out; exit 2")
        registry.register_rewake(hook_name="H", capture=capture)
        await capture.result()
        await asyncio.sleep(0.02)
        first = [c.value for c in registry.queue.dequeue_all_matching(lambda c: True)]
        return first, [c.value for c in registry.queue.dequeue_all_matching(lambda c: True)]

    first, second = asyncio.run(go())
    assert first == ['<system-reminder>\nStop hook blocking error from command "H": only-out\n\n</system-reminder>']
    assert second == []


def test_rewake_other_exit_codes_queue_nothing():
    async def go() -> list[str]:
        registry = AsyncHookRegistry()
        for code in (0, 1, 3):
            capture = await _spawn(f"echo x; exit {code}")
            registry.register_rewake(hook_name="H", capture=capture)
            await capture.result()
        await asyncio.sleep(0.02)
        return [c.value for c in registry.queue.dequeue_all_matching(lambda c: True)]

    assert asyncio.run(go()) == []


def test_finalize_kills_a_running_rewake_hook():
    async def go() -> CapturedProcess:
        registry = AsyncHookRegistry()
        capture = await _spawn("sleep 5")
        registry.register_rewake(hook_name="H", capture=capture)
        await asyncio.wait_for(registry.finalize_pending(), timeout=2)
        assert registry._rewake_captures == []
        await asyncio.sleep(0)
        assert all(task.done() for task in registry._rewake_tasks) or not registry._rewake_tasks
        return capture

    capture = asyncio.run(go())
    assert capture.status == "killed"
    assert capture.exit_code is not None


def test_finalize_leaves_a_finished_rewake_capture_alone():
    async def go() -> CapturedProcess:
        registry = AsyncHookRegistry()
        capture = await _spawn("exit 0")
        registry.register_rewake(hook_name="H", capture=capture)
        await capture.result()
        await registry.finalize_pending()
        return capture

    assert asyncio.run(go()).status == "completed"
