"""The HTTPS probe that decides a hostname is live (design 2.3 step 9, R-H4), SSRF-guarded.

The worker holds provider tokens and sits next to Postgres and Redis, and the Subscriber controls
the DNS answer. So: https only; resolve the host first and refuse private, loopback, link-local,
CGNAT, multicast, reserved and cloud-metadata addresses (IPv4, IPv6 and IPv4-mapped); connect to
the resolved address itself (the hostname is kept for SNI and certificate verification); check the
connected peer is that address (DNS rebinding); no redirect is followed (a redirect is a failed
probe); a short timeout; a small body cap; no cookies sent; certificate verification on.
"""
import http.client
import ipaddress
import json
import socket
import ssl
import secrets
from dataclasses import dataclass

TIMEOUT_SECONDS = 8
MAX_BODY_BYTES = 4096
PROBE_PATH = "/.well-known/negosyoonline-probe"
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_EXTRA_BLOCKED = (ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("0.0.0.0/8"),
                  ipaddress.ip_network("192.0.0.0/24"), ipaddress.ip_network("198.18.0.0/15"))


class ProbeRefused(Exception):
    pass


@dataclass
class ProbeResult:
    ok: bool
    site: str = ""
    reason: str = ""


def blocked_address(value):
    """True for any address a probe may never connect to. Unparseable counts as blocked."""
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and (ip.sixtofour or ip.teredo):
        return True
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or not ip.is_global):
        return True
    if ip.version == 4 and (ip in _CGNAT or any(ip in net for net in _EXTRA_BLOCKED)):
        return True
    return False


def resolve(host):
    """Every address of `host`, as strings. Raises ProbeRefused when none resolves."""
    try:
        found = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ProbeRefused("dns") from None
    addresses = []
    for family, _t, _p, _c, sockaddr in found:
        if sockaddr[0] not in addresses:
            addresses.append(sockaddr[0])
    if not addresses:
        raise ProbeRefused("dns")
    return addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to `ip`, but speaks TLS for `host` (SNI and certificate verification on)."""

    def __init__(self, host, ip, **kwargs):
        super().__init__(host, **kwargs)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port or 443), self.timeout)
        peer = sock.getpeername()[0]
        if peer != self._ip or blocked_address(peer):
            sock.close()
            raise ProbeRefused("peer")
        context = ssl.create_default_context()
        self.sock = context.wrap_socket(sock, server_hostname=self.host)


def make_nonce():
    return secrets.token_urlsafe(16)


def probe(hostname, nonce, *, resolver=resolve, connection_class=PinnedHTTPSConnection):
    """Ask `https://<hostname>/.well-known/negosyoonline-probe?n=<nonce>`. The answer must be
    `{"site": <id>, "n": <nonce>}`; the caller compares the site id."""
    try:
        addresses = resolver(hostname)
    except ProbeRefused as error:
        return ProbeResult(False, reason=str(error))
    if not addresses or any(blocked_address(a) for a in addresses):
        return ProbeResult(False, reason="blocked_address")
    connection = connection_class(hostname, addresses[0], timeout=TIMEOUT_SECONDS)
    try:
        connection.request("GET", f"{PROBE_PATH}?n={nonce}", headers={"Host": hostname, "Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:  # a redirect is a failed probe; none is followed
            return ProbeResult(False, reason=f"status_{response.status}")
        body = response.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            return ProbeResult(False, reason="too_large")
        data = json.loads(body)
    except ProbeRefused as error:
        return ProbeResult(False, reason=str(error))
    except (OSError, ssl.SSLError, http.client.HTTPException, ValueError):
        return ProbeResult(False, reason="unreachable")
    finally:
        connection.close()
    if not isinstance(data, dict) or data.get("n") != nonce or not isinstance(data.get("site"), str):
        return ProbeResult(False, reason="wrong_answer")
    return ProbeResult(True, site=data["site"])
