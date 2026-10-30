import os

from dotenv import load_dotenv

load_dotenv()

if not os.getenv("OPENAI_API_KEY"):
    raise RuntimeError("No API key configured")

# E2B_API_KEY is optional: runs use a local workspace, and the sandbox adapter checks the
# key only when a sandbox is actually requested (`app/sandbox/adapter.py`).


class Config:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
    E2B_API_KEY = os.getenv("E2B_API_KEY")

    # Claude Haiku 5.5 (user decision, 2026-10-29; gpt-5-mini before). The small fast model
    # is upstream's default Haiku 4.5 (`app/utils/model/model.py::get_default_haiku_model`).
    CHAT_MODEL = "anthropic"
    MODEL = "claude-haiku-5-5"
    TEMPERATURE = 1.0

    # ── Sandbox lifecycle ────────────────────────────────────────────────────
    # The E2B SDK defaults to a 300s sandbox lifetime, which is far shorter than a
    # real run. The adapter creates with SANDBOX_TIMEOUT and slides the window
    # forward with set_timeout() whenever SANDBOX_REFRESH_INTERVAL has elapsed, so a
    # run's total length is unbounded while the sandbox still dies promptly if the
    # process crashes. 3600 is the cap on an E2B Hobby account.
    SANDBOX_TIMEOUT = 3600
    SANDBOX_REFRESH_INTERVAL = 600

    # ── Command execution ────────────────────────────────────────────────────
    # E2B's commands.run() defaults to timeout=60, which silently truncates long
    # pip installs and test suites. This is the adapter default and can be overridden
    # per call.
    COMMAND_TIMEOUT = 300

    # ── Workspace roots ──────────────────────────────────────────────────────
    # Every workspace-relative path resolves against these. Pinning the sandbox root
    # explicitly is what makes the path contract in app/workspace/base.py a
    # guarantee rather than an accident of the E2B default working directory.
    SANDBOX_WORKSPACE_ROOT = "/home/user"
    # Local projects live outside TDDAgents' folder (Phase 13): `app/workspace/local.py::workspaces_root`.
