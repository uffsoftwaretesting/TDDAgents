"""
Where the sandbox's network infrastructure lives on the host: its own thread and event loop.

Ported from `@anthropic-ai/sandbox-runtime` 0.0.44 `src/sandbox/sandbox-manager.ts` -> `HostNetworkManagerContext`,
`startHttpProxyServer`, `startSocksProxyServer`, the network half of `initialize` (an external proxy port
is used as given, else a local proxy is started; on Linux the socat bridge follows) and of `reset`.

**TDDAgents**: Node runs the proxies on the one event loop of the process. Here a command can block the
caller's loop (`LocalWorkspace.execute` is synchronous), so the proxies get a loop on a daemon thread of
their own (user decision 2026-11-08). A network ask belongs to the caller's loop (the REPL's dialog, the
SDK host's control request): `call_on_loop` sends it there with `run_coroutine_threadsafe` and the
proxy awaits the answer without blocking either loop.

**Mutation-exempt seam** (`[tool.mutmut] do_not_mutate`): the thread, the event loop and the servers.
Verified end to end by `tests/test_sandbox_proxies.py` and `tests/test_sandbox_manager.py`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, TypeVar

from app.utils.sandbox.runtime.http_proxy import HttpProxyServerOptions, start_http_proxy_server
from app.utils.sandbox.runtime.linux_network_bridge import (
    LinuxNetworkBridgeContext,
    initialize_linux_network_bridge,
    shutdown_linux_network_bridge,
)
from app.utils.sandbox.runtime.socks_proxy import start_socks_proxy_server

logger = logging.getLogger("TDDOrchestrator.Sandbox")

T = TypeVar("T")

#: How long the proxies may take to start or stop, in seconds.
LOOP_CALL_TIMEOUT_SECONDS = 30

Filter = Callable[[int, str], Awaitable[bool]]
GetMitmSocketPath = Callable[[str], "str | None"]


class ProxyLoop:
    """An event loop running on a daemon thread, for the proxy servers."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="tddagents-sandbox-proxy", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call(self, coro: Coroutine[Any, Any, T]) -> T:
        """Run `coro` on the proxy loop and wait for its result."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=LOOP_CALL_TIMEOUT_SECONDS)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=LOOP_CALL_TIMEOUT_SECONDS)
        if not self._thread.is_alive():
            self.loop.close()


def call_on_loop(ask: Callable[[Any], Awaitable[bool]], loop: asyncio.AbstractEventLoop | None
                 ) -> Callable[[Any], Awaitable[bool]]:
    """`ask`, called on `loop` (the caller's) and awaited from the proxy's loop; as is without a loop."""
    if loop is None:
        return ask

    async def on_caller_loop(pattern: Any) -> bool:
        async def run() -> bool:
            return await ask(pattern)

        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(run(), loop))

    return on_caller_loop


@dataclass
class HostNetworkContext:
    """`HostNetworkManagerContext`, with what `reset` needs to take it down."""

    http_proxy_port: int
    socks_proxy_port: int
    linux_bridge: LinuxNetworkBridgeContext | None = None
    proxy_loop: ProxyLoop | None = None
    servers: list[asyncio.Server] = field(default_factory=list)

    def close(self) -> None:
        """`reset`'s network half: the bridge stopped, then the servers closed and the loop stopped."""
        if self.linux_bridge is not None:
            shutdown_linux_network_bridge(self.linux_bridge)
            self.linux_bridge = None
        if self.proxy_loop is not None:
            servers = list(self.servers)

            async def close_all() -> None:
                for server in servers:
                    server.close()
                for server in servers:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(server.wait_closed(), timeout=1)

            with contextlib.suppress(Exception):
                self.proxy_loop.call(close_all())
            self.proxy_loop.stop()
            self.proxy_loop = None
        self.servers = []


def start_host_network(
    filter_request: Filter, *, get_mitm_socket_path: GetMitmSocketPath | None = None,
    http_proxy_port: int | None = None, socks_proxy_port: int | None = None, linux: bool = True,
) -> HostNetworkContext:
    """
    `initialize`'s network infrastructure: the HTTP and SOCKS proxies (unless external ports are given)
    on a proxy loop, then, on Linux, the socat bridge to them. A failure takes down what started.
    """
    context = HostNetworkContext(http_proxy_port=0, socks_proxy_port=0)
    try:
        if http_proxy_port is None or socks_proxy_port is None:
            context.proxy_loop = ProxyLoop()
        if http_proxy_port is not None:
            logger.debug("Using external HTTP proxy on port %s", http_proxy_port)
            context.http_proxy_port = http_proxy_port
        else:
            assert context.proxy_loop is not None
            server, port = context.proxy_loop.call(start_http_proxy_server(
                HttpProxyServerOptions(filter=filter_request, get_mitm_socket_path=get_mitm_socket_path)))
            context.servers.append(server)
            context.http_proxy_port = port
        if socks_proxy_port is not None:
            logger.debug("Using external SOCKS proxy on port %s", socks_proxy_port)
            context.socks_proxy_port = socks_proxy_port
        else:
            assert context.proxy_loop is not None
            server, port = context.proxy_loop.call(start_socks_proxy_server(filter_request))
            context.servers.append(server)
            context.socks_proxy_port = port
        if linux:
            context.linux_bridge = initialize_linux_network_bridge(context.http_proxy_port, context.socks_proxy_port)
        logger.debug("Network infrastructure initialized")
        return context
    except BaseException:
        context.close()
        raise


__all__ = [
    "LOOP_CALL_TIMEOUT_SECONDS",
    "HostNetworkContext",
    "ProxyLoop",
    "call_on_loop",
    "start_host_network",
]
