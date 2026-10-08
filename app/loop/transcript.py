import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


class TranscriptLogger:
    def __init__(self, run_id: str, base_dir: Path | str | None = None):
        self.run_id = run_id
        base_dir = Path(base_dir or Path.cwd()).resolve()
        self.transcript_dir = base_dir / ".tddagents" / "transcripts"
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.transcript_dir / f"transcript_{run_id}.jsonl"

    def _serialize_event(self, event: Any) -> dict[str, Any]:
        if hasattr(event, "model_dump"):
            return {"type": "message", "message": event.model_dump()}
        if hasattr(event, "reason"):
            return {"type": "terminal_or_transition", "reason": str(event.reason)}
        return {"type": "unknown", "event_str": str(event)}

    def append(self, event: Any) -> None:
        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self._serialize_event(event)) + "\n")
        except Exception as e:
            logger.warning(f"Failed to append to transcript: {e}")

    def checkpoint_state(self, state: Any) -> None:
        """Serialize a full LoopState checkpoint into append-only JSONL (Plan C1)."""
        phase_str = None
        if getattr(state, "phase_ledger", None) is not None:
            phase_val = getattr(state.phase_ledger, "phase", None)
            phase_str = getattr(phase_val, "value", str(phase_val)) if phase_val is not None else None

        transition_str = None
        if getattr(state, "transition", None) is not None:
            reason = getattr(state.transition, "reason", None)
            transition_str = getattr(reason, "value", str(reason)) if reason is not None else None

        messages_data: list[dict[str, Any]] = []
        for m in getattr(state, "messages", ()):
            if hasattr(m, "model_dump"):
                messages_data.append(m.model_dump())
            else:
                messages_data.append({
                    "content": str(getattr(m, "content", "")),
                    "type": getattr(m, "type", "message"),
                })

        record: dict[str, Any] = {
            "type": "state_checkpoint",
            "run_id": self.run_id,
            "turn_count": getattr(state, "turn_count", 0),
            "phase": phase_str,
            "transition": transition_str,
            "messages_count": len(messages_data),
            "messages": messages_data,
        }

        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.warning(f"Failed to checkpoint state to transcript: {e}")

    def read_events(self) -> list[dict[str, Any]]:
        """Read all serialized JSONL records from the transcript."""
        if not self.file_path.is_file():
            return []
        events: list[dict[str, Any]] = []
        with open(self.file_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    try:
                        events.append(json.loads(stripped))
                    except Exception:
                        pass
        return events

    def get_last_checkpoint(self) -> dict[str, Any] | None:
        """Scan backwards to retrieve the latest state checkpoint."""
        events = self.read_events()
        for ev in reversed(events):
            if ev.get("type") == "state_checkpoint":
                return ev
        return None


async def with_transcript_logger(events: AsyncIterator[Any], logger_instance: TranscriptLogger) -> AsyncIterator[Any]:
    async for event in events:
        logger_instance.append(event)
        yield event
