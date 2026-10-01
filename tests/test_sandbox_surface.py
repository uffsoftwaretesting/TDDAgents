"""
Static surface check for app/sandbox/adapter.py against the installed E2B SDK.

Mandatory quality gate check required by CLAUDE.md for exempt seam modules:
"assert every SDK method and kwarg they call still exists on the installed package"
"""

from __future__ import annotations

import inspect
import e2b
import e2b.exceptions
import e2b.connection_config
from e2b.sandbox_sync.filesystem.filesystem import Filesystem
from e2b.sandbox_sync.commands.command import Commands
import e2b_code_interpreter


def test_e2b_exceptions_surface() -> None:
    expected_exceptions = [
        "AuthenticationException",
        "NotEnoughSpaceException",
        "NotFoundException",
        "SandboxException",
        "TemplateException",
        "TimeoutException",
    ]
    for exc_name in expected_exceptions:
        assert hasattr(e2b, exc_name), f"Missing exception {exc_name} in e2b"
        exc_cls = getattr(e2b, exc_name)
        assert issubclass(exc_cls, Exception)

    assert hasattr(e2b.exceptions, "RateLimitException")
    assert issubclass(e2b.exceptions.RateLimitException, Exception)


def test_e2b_sandbox_class_surface() -> None:
    sandbox_cls = e2b_code_interpreter.Sandbox

    # Class methods
    assert hasattr(sandbox_cls, "create")
    assert callable(sandbox_cls.create)
    create_sig = inspect.signature(sandbox_cls.create)
    assert "timeout" in create_sig.parameters
    # api_key is part of connection_config.ApiParams (unpacked in **opts)
    assert "api_key" in e2b.connection_config.ApiParams.__annotations__

    assert hasattr(sandbox_cls, "connect")
    assert callable(sandbox_cls.connect)
    connect_sig = inspect.signature(sandbox_cls.connect)
    assert "sandbox_id" in connect_sig.parameters or "self" in connect_sig.parameters

    # Instance methods
    expected_methods = [
        "is_running",
        "get_info",
        "set_timeout",
        "kill",
        "run_code",
    ]
    for method_name in expected_methods:
        assert hasattr(sandbox_cls, method_name), f"Missing method {method_name} on Sandbox"
        assert callable(getattr(sandbox_cls, method_name))


def test_e2b_sandbox_commands_and_files_surface() -> None:
    # Files interface
    expected_fs_methods = [
        "read",
        "write",
        "write_files",
        "list",
        "exists",
        "remove",
        "rename",
        "make_dir",
    ]
    for method_name in expected_fs_methods:
        assert hasattr(Filesystem, method_name), f"Missing method {method_name} on Filesystem"
        assert callable(getattr(Filesystem, method_name))

    # Commands interface
    expected_cmd_methods = [
        "run",
        "kill",
        "list",
    ]
    for method_name in expected_cmd_methods:
        assert hasattr(Commands, method_name), f"Missing method {method_name} on Commands"
        assert callable(getattr(Commands, method_name))
