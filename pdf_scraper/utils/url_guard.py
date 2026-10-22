"""Block destinations that should not be scanned by default."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
}

_PAGE_EXTENSIONS = (".html", ".htm", ".php", ".asp", ".aspx", ".jsp", ".xhtml")


class UrlBlockedError(ValueError):
    """The URL points at a local, private, or metadata address."""


def destination_is_blocked(url: str) -> bool:
    """Return True for blocked hostnames and IP literals. This does not resolve DNS."""
    hostname = _hostname(url)
    if hostname is None:
        return True
    if hostname in _BLOCKED_HOSTNAMES:
        return True
    address = _literal_ip(hostname)
    if address is None:
        return False
    return _address_is_blocked(address)


def resolves_to_blocked_address(url: str) -> bool:
    """Return True when DNS for the host includes a blocked address."""
    if destination_is_blocked(url):
        return True
    hostname = _hostname(url)
    if hostname is None:
        return True
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for info in infos:
        address = _literal_ip(info[4][0])
        if address is not None and _address_is_blocked(address):
            return True
    return False


def ensure_scan_allowed(url: str, *, allow_private_networks: bool) -> None:
    """Raise UrlBlockedError when a scan target is not allowed."""
    if allow_private_networks:
        return
    if destination_is_blocked(url) or resolves_to_blocked_address(url):
        raise UrlBlockedError(
            "That address is blocked. Private, local, and metadata addresses are not scanned."
        )


def is_web_page_url(url: str) -> bool:
    """Return True when the path looks like an HTML page rather than a file."""
    path = urlparse(url).path.lower()
    return path.endswith(_PAGE_EXTENSIONS)


def _hostname(url: str) -> str | None:
    hostname = urlparse(url).hostname
    if not hostname:
        return None
    return hostname.lower().rstrip(".")


def _literal_ip(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        pass
    if hostname.isdigit():
        try:
            return ipaddress.ip_address(int(hostname))
        except ValueError:
            return None
    return None


def _address_is_blocked(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )
