import os
import sys
import importlib
from pathlib import Path

import pytest


def test_load_env_from_dotenv(tmp_path, monkeypatch):
    # Create a sample .env file in the temporary directory
    env_file = tmp_path / ".env"
    env_file.write_text("MY_TEST_VAR=hello_world")

    # Change current working directory to tmp_path so load_dotenv finds the .env file
    monkeypatch.chdir(tmp_path)

    # Prepend src/ to sys.path so we can import core.config
    project_root = Path(__file__).parent.parent
    src_path = project_root / "src"
    monkeypatch.syspath_prepend(str(src_path))

    # Ensure the variable is not already defined in the environment
    monkeypatch.delenv("MY_TEST_VAR", raising=False)

    # Remove core.config from sys.modules if already imported
    if "core.config" in sys.modules:
        del sys.modules["core.config"]

    # Import the config module, which should call load_dotenv()
    config = importlib.import_module("core.config")

    # Assert that the environment variable from .env is now available
    assert os.getenv("MY_TEST_VAR") == "hello_world"
