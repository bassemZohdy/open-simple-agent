"""Shared policy for application-initiated outbound HTTP requests.

The policy is intentionally small and framework-neutral. It blocks common
SSRF destinations before a request is issued, disables ambient proxy trust by
default, and requires callers to disable redirects so every hop is checked.
Network egress controls are still required because DNS can change after this
application-level check.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import SplitResult, urlsplit

OUTBOUND_ALLOWED_HOSTS_ENV = "OSA_OUTBOUND_ALLOWED_HOSTS"
OUTBOUND_ALLOW_PRIVATE_ENV = "OSA_OUTBOUND_ALLOW_PRIVATE_NETWORKS"
OUTBOUND_TRUST_ENV_ENV = "OSA_OUTBOUND_TRUST_ENV"


class OutboundUrlError(ValueError):
    """An outbound destination violates the OSA URL policy."""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def outbound_trust_env() -> bool:
    """Whether HTTP clients may use ambient proxy/environment settings."""
    return _truthy(os.environ.get(OUTBOUND_TRUST_ENV_ENV))


def _allowed_hosts() -> set[str]:
    return {
        host.strip().lower().rstrip(".")
        for host in os.environ.get(OUTBOUND_ALLOWED_HOSTS_ENV, "").split(",")
        if host.strip()
    }


def _address_is_private(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_reserved,
            address.is_multicast,
            address.is_unspecified,
        )
    )


def _host_addresses(parsed: SplitResult) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    hostname = parsed.hostname
    assert hostname is not None
    try:
        direct = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            infos = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise OutboundUrlError(f"unable to resolve outbound host '{hostname}'") from exc
        addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except (IndexError, ValueError):
                continue
        return addresses
    return [direct]


def validate_outbound_url(url: str, *, purpose: str = "outbound URL") -> str:
    """Validate an HTTP(S) destination before making an outbound request.

    Operators can allow an exact host through ``OSA_OUTBOUND_ALLOWED_HOSTS``
    for local development or a private service. ``OSA_OUTBOUND_ALLOW_PRIVATE_NETWORKS``
    is a broad, explicit escape hatch for environments where private egress is
    intentionally required; network policy remains the authoritative control.
    """
    if not isinstance(url, str) or any(ord(char) < 32 for char in url):
        raise OutboundUrlError(f"{purpose} contains invalid control characters")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise OutboundUrlError(f"{purpose} must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise OutboundUrlError(f"{purpose} must not contain credentials or a fragment")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise OutboundUrlError(f"{purpose} contains an invalid port") from exc

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in _allowed_hosts() or _truthy(os.environ.get(OUTBOUND_ALLOW_PRIVATE_ENV)):
        return url
    addresses = _host_addresses(parsed)
    if not addresses:
        raise OutboundUrlError(f"{purpose} host '{hostname}' did not resolve")
    for address in addresses:
        if _address_is_private(address):
            raise OutboundUrlError(f"{purpose} resolves to a private or reserved address")
    return url


__all__ = [
    "OUTBOUND_ALLOWED_HOSTS_ENV",
    "OUTBOUND_ALLOW_PRIVATE_ENV",
    "OUTBOUND_TRUST_ENV_ENV",
    "OutboundUrlError",
    "outbound_trust_env",
    "validate_outbound_url",
]
