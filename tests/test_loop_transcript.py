import pytest
from pathlib import Path
from app.loop.transcript import TranscriptLogger
from app.loop.messages import Message
from langchain_core.messages import HumanMessage
from app.loop.transitions import Terminated, Terminal

def test_transcript_logger(tmp_path: Path):
    logger = TranscriptLogger(run_id="test_run", base_dir=tmp_path)
    
    msg = HumanMessage(content="Hello")
    logger.append(msg)
    
    term = Terminated(reason=Terminal.COMPLETED)
    logger.append(term)
    
    lines = logger.file_path.read_text().splitlines()
    assert len(lines) == 2
    assert "Hello" in lines[0]
    assert "completed" in lines[1].lower()
