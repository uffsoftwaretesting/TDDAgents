"""Tests for the HTTP-hook SSRF guard (port of `ssrfGuard.ts`)."""

from __future__ import annotations

import pytest

from app.utils.hooks.ssrf_guard import (
    SSRF_ERROR_CODE,
    SsrfBlockedError,
    default_resolver,
    expand_ipv6_groups,
    extract_mapped_ipv4,
    is_blocked_address,
    is_ip,
    ssrf_guarded_lookup,
)


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0", "0.255.1.1", "10.0.0.1", "10.255.255.255",
        "100.64.0.0", "100.100.100.200", "100.127.255.255",
        "169.254.169.254", "169.254.0.1",
        "172.16.0.1", "172.31.255.255",
        "192.168.0.1", "192.168.255.255",
        "::", "fc00::1", "fd12:3456::1", "FD00::1", "fe80::1", "febf::1", "fe9a::1",
        "::ffff:169.254.169.254", "::ffff:a9fe:a9fe", "0:0:0:0:0:ffff:a9fe:a9fe",
        "::ffff:10.0.0.1", "::FFFF:192.168.1.1",
    ],
)
def test_blocked(address: str) -> None:
    assert is_blocked_address(address) is True


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "127.255.0.1", "::1", "8.8.8.8", "1.1.1.1",
        "100.63.255.255", "100.128.0.0", "172.15.255.255", "172.32.0.0",
        "169.253.1.1", "169.255.1.1", "192.167.1.1", "192.169.1.1", "11.0.0.1", "9.255.255.255",
        "2001:4860:4860::8888", "fec0::1", "fe7f::1", "::ffff:8.8.8.8", "::ffff:127.0.0.1",
        "not-an-ip", "",
    ],
)
def test_allowed(address: str) -> None:
    assert is_blocked_address(address) is False


def test_is_ip() -> None:
    assert is_ip("1.2.3.4") == 4
    assert is_ip("::1") == 6
    assert is_ip("example.com") == 0


def test_expand_ipv6_groups() -> None:
    assert expand_ipv6_groups("::") == [0] * 8
    assert expand_ipv6_groups("1:2:3:4:5:6:7:8") == [1, 2, 3, 4, 5, 6, 7, 8]
    assert expand_ipv6_groups("1::8") == [1, 0, 0, 0, 0, 0, 0, 8]
    assert expand_ipv6_groups("::ffff:1.2.3.4") == [0, 0, 0, 0, 0, 0xFFFF, 0x0102, 0x0304]
    assert expand_ipv6_groups("::ffff:1.2.3") is None
    assert expand_ipv6_groups("::ffff:1.2.3.256") is None
    assert expand_ipv6_groups("::ffff:1.2.x.4") is None
    assert expand_ipv6_groups("1:2:3:4:5:6:7:8:9") is None
    # Upstream pads a short address with zero groups even without `::`.
    assert expand_ipv6_groups("1:2:3:4:5:6:7") == [1, 2, 3, 4, 5, 6, 7, 0]
    assert expand_ipv6_groups("zz::1") is None
    assert expand_ipv6_groups("10000::1") is None


def test_extract_mapped_ipv4() -> None:
    assert extract_mapped_ipv4("::ffff:a9fe:a9fe") == "169.254.169.254"
    assert extract_mapped_ipv4("::ffff:0102:0304") == "1.2.3.4"
    assert extract_mapped_ipv4("::fffe:a9fe:a9fe") is None
    assert extract_mapped_ipv4("1::ffff:a9fe:a9fe") is None
    assert extract_mapped_ipv4("0:0:0:0:1:ffff:a9fe:a9fe") is None
    assert extract_mapped_ipv4("garbage") is None


def test_lookup_ip_literal_checked_without_dns() -> None:
    def no_dns(host: str) -> list[tuple[str, int]]:
        raise AssertionError("resolver must not run for a literal")

    assert ssrf_guarded_lookup("127.0.0.1", no_dns) == [("127.0.0.1", 4)]
    assert ssrf_guarded_lookup("::1", no_dns) == [("::1", 6)]
    with pytest.raises(SsrfBlockedError) as info:
        ssrf_guarded_lookup("169.254.169.254", no_dns)
    assert str(info.value) == (
        "HTTP hook blocked: 169.254.169.254 resolves to 169.254.169.254 (private/link-local address). "
        "Loopback (127.0.0.1, ::1) is allowed for local dev."
    )
    assert info.value.code == SSRF_ERROR_CODE
    assert info.value.hostname == "169.254.169.254"
    assert info.value.address == "169.254.169.254"


def test_lookup_resolved_any_blocked_address_rejects() -> None:
    with pytest.raises(SsrfBlockedError) as info:
        ssrf_guarded_lookup("evil.test", lambda h: [("8.8.8.8", 4), ("10.0.0.5", 4)])
    assert info.value.hostname == "evil.test"
    assert info.value.address == "10.0.0.5"


def test_lookup_returns_all_addresses_with_family() -> None:
    assert ssrf_guarded_lookup("ok.test", lambda h: [("8.8.8.8", 4), ("2001:db8::1", 6), ("1.1.1.1", 0)]) == [
        ("8.8.8.8", 4),
        ("2001:db8::1", 6),
        ("1.1.1.1", 4),
    ]


def test_lookup_empty_answer_is_enotfound() -> None:
    with pytest.raises(OSError, match="^ENOTFOUND nothing.test$"):
        ssrf_guarded_lookup("nothing.test", lambda h: [])


def test_default_resolver_literal_and_dedup() -> None:
    assert default_resolver("127.0.0.1") == [("127.0.0.1", 4)]
    assert default_resolver("::1") == [("::1", 6)]


def test_default_used_when_no_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.utils.hooks.ssrf_guard as guard

    monkeypatch.setattr(guard, "default_resolver", lambda h: [("192.168.1.1", 4)])
    with pytest.raises(SsrfBlockedError):
        guard.ssrf_guarded_lookup("lan.test")
