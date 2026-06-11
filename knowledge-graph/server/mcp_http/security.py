"""Request-origin guards for a localhost-bound server.

The server has no authentication — its trust boundary is "processes on this
machine". Two browser-side attack paths can cross that boundary from the web:

  * DNS rebinding: a malicious page's domain re-resolves to 127.0.0.1, making
    its requests same-origin to this server — CORS no longer applies. Guard:
    only accept requests whose Host header names this machine.

  * Cross-origin WebSockets: browsers do NOT apply CORS to WebSocket upgrades,
    so any web page could open ws://127.0.0.1:<port>/ws and receive live graph
    broadcasts. Guard: only accept upgrades with no Origin header (non-browser
    clients) or an Origin on this machine.

Both guards are deliberately host-based, not port-based: anything served from
localhost is already inside the trust boundary.
"""

from urllib.parse import urlsplit

_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _hostname(netloc: str) -> str:
    """Extract the hostname from a Host-style value, dropping any port."""
    if netloc.startswith("["):  # bracketed IPv6, e.g. [::1]:8765
        return netloc.split("]")[0] + "]"
    return netloc.rsplit(":", 1)[0] if ":" in netloc else netloc


def host_allowed(host_header: str | None, configured_host: str = "127.0.0.1") -> bool:
    """True if the Host header names this machine (anti DNS-rebinding).

    configured_host is the bind address; if the user deliberately bound a
    non-loopback interface (e.g. a LAN IP), requests addressed to that host
    are allowed too.
    """
    if not host_header:
        return False
    hostname = _hostname(host_header.strip().lower())
    return hostname in _LOCAL_HOSTNAMES or hostname == configured_host.lower()


def origin_allowed(origin_header: str | None, configured_host: str = "127.0.0.1") -> bool:
    """True if a WebSocket/HTTP Origin is absent (non-browser) or local."""
    if not origin_header:
        return True
    parts = urlsplit(origin_header.strip().lower())
    if parts.scheme not in ("http", "https"):
        return False
    hostname = parts.hostname or ""
    return hostname in _LOCAL_HOSTNAMES or hostname == configured_host.lower()
