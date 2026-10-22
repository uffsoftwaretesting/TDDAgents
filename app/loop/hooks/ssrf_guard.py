"""
SSRF guard for HTTP hooks.

Ported from `reference/claude-code/src/utils/hooks/ssrfGuard.ts` -> `isBlockedAddress`,
`isBlockedV4`, `isBlockedV6`, `expandIPv6Groups`, `extractMappedIPv4`,
`ssrfGuardedLookup`, `ssrfError`.

Blocks private, link-local and other non-routable ranges so a project-configured HTTP hook
cannot reach a cloud metadata endpoint (169.254.169.254) or internal infrastructure.
Loopback (127.0.0.0/8, ::1) is deliberately **allowed**: a local policy server is a primary
HTTP-hook use case.

Blocked IPv4: 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10 (CGNAT, e.g. Alibaba's metadata at
100.100.100.200), 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16. Blocked IPv6: `::`,
fc00::/7, fe80::/10, and an IPv4-mapped address (`::ffff:X`, any representation) whose IPv4
is blocked.

`ssrf_guarded_lookup` returns the validated addresses the socket must connect to, so there
is no rebinding window between validation and connection (upstream passes it to axios as
`lookup` for the same reason). The resolver is injectable so tests never touch DNS.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable

#: `(address, family)` pairs, family 4 or 6 — the shape of `dns.lookup(..., {all: true})`.
Resolver = Callable[[str], list[tuple[str, int]]]

SSRF_ERROR_CODE = "ERR_HTTP_HOOK_BLOCKED_ADDRESS"


class SsrfBlockedError(OSError):
    """`ssrfError`: the hostname resolved to a blocked address."""

    def __init__(self, hostname: str, address: str) -> None:
        super().__init__(
            f"HTTP hook blocked: {hostname} resolves to {address} (private/link-local address). "
            "Loopback (127.0.0.1, ::1) is allowed for local dev."
        )
        self.code = SSRF_ERROR_CODE
        self.hostname = hostname
        self.address = address


def is_ip(address: str) -> int:
    """Node's `net.isIP`: 4, 6, or 0 for anything that is not an IP literal."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return 0
    return parsed.version


def is_blocked_address(address: str) -> bool:
    """`isBlockedAddress`: is this IP literal one HTTP hooks must not reach?"""
    version = is_ip(address)
    if version == 4:
        return _is_blocked_v4(address)
    if version == 6:
        return _is_blocked_v6(address)
    return False


def _is_blocked_v4(address: str) -> bool:
    # Only ever called with a validated dotted quad (`is_ip`, or `extract_mapped_ipv4`'s
    # own formatting), so the octets always parse.
    parts = address.split(".")
    a, b = int(parts[0]), int(parts[1])
    if a == 127:
        return False
    if a == 0 or a == 10:
        return True
    if a == 169 and b == 254:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 100 and 64 <= b <= 127:
        return True
    return a == 192 and b == 168


def _is_blocked_v6(address: str) -> bool:
    lower = address.lower()
    if lower == "::1":
        return False
    if lower == "::":
        return True
    mapped = extract_mapped_ipv4(lower)
    if mapped is not None:
        return _is_blocked_v4(mapped)
    if lower.startswith(("fc", "fd")):
        return True
    first = lower.split(":")[0]
    return len(first) == 4 and "fe80" <= first <= "febf"


def expand_ipv6_groups(addr: str) -> list[int] | None:
    """`expandIPv6Groups`: exactly eight 16-bit groups, or `None` when malformed."""
    tail_hextets: list[int] = []
    if "." in addr:
        last_colon = addr.rfind(":")
        v4 = addr[last_colon + 1:]
        addr = addr[:last_colon]
        try:
            octets = [int(o) for o in v4.split(".")]
        except ValueError:
            return None
        if len(octets) != 4 or any(o < 0 or o > 255 for o in octets):
            return None
        tail_hextets = [(octets[0] << 8) | octets[1], (octets[2] << 8) | octets[3]]

    dbl = addr.find("::")
    if dbl == -1:
        head = addr.split(":")
        tail: list[str] = []
    else:
        head_str, tail_str = addr[:dbl], addr[dbl + 2:]
        head = head_str.split(":") if head_str else []
        tail = tail_str.split(":") if tail_str else []

    fill = 8 - len(tail_hextets) - len(head) - len(tail)
    if fill < 0:
        return None
    try:
        nums = [int(h, 16) for h in [*head, *(["0"] * fill), *tail]]
    except ValueError:
        return None
    if any(n < 0 or n > 0xFFFF for n in nums):
        return None
    nums.extend(tail_hextets)
    return nums if len(nums) == 8 else None


def extract_mapped_ipv4(addr: str) -> str | None:
    """`extractMappedIPv4`: the IPv4 inside `0:0:0:0:0:ffff:X:Y`, in any representation."""
    groups = expand_ipv6_groups(addr)
    if groups is None:
        return None
    if groups[:5] == [0, 0, 0, 0, 0] and groups[5] == 0xFFFF:
        hi, lo = groups[6], groups[7]
        return f"{hi >> 8}.{hi & 0xFF}.{lo >> 8}.{lo & 0xFF}"
    return None


def default_resolver(hostname: str) -> list[tuple[str, int]]:
    """`dns.lookup(hostname, {all: true})` via `getaddrinfo`, deduplicated in order."""
    seen: list[tuple[str, int]] = []
    for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP):
        entry = (str(sockaddr[0]), 6 if family == socket.AF_INET6 else 4)
        if entry not in seen:
            seen.append(entry)
    return seen


def ssrf_guarded_lookup(hostname: str, resolver: Resolver | None = None) -> list[tuple[str, int]]:
    """
    `ssrfGuardedLookup` (the `all: true` shape): every resolved address, in order, once none
    of them is blocked. An IP literal is checked directly without DNS. Raises
    `SsrfBlockedError` on a blocked address and `OSError` (`ENOTFOUND`) on an empty answer.
    """
    version = is_ip(hostname)
    if version != 0:
        if is_blocked_address(hostname):
            raise SsrfBlockedError(hostname, hostname)
        return [(hostname, 6 if version == 6 else 4)]
    addresses = (resolver or default_resolver)(hostname)
    for address, _family in addresses:
        if is_blocked_address(address):
            raise SsrfBlockedError(hostname, address)
    if not addresses:
        raise OSError(f"ENOTFOUND {hostname}")
    return [(address, 6 if family == 6 else 4) for address, family in addresses]


__all__ = [
    "SSRF_ERROR_CODE",
    "Resolver",
    "SsrfBlockedError",
    "default_resolver",
    "expand_ipv6_groups",
    "extract_mapped_ipv4",
    "is_blocked_address",
    "is_ip",
    "ssrf_guarded_lookup",
]
