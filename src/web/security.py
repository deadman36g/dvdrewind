"""Security helpers for outbound URL fetching and privileged web controls.

The app intentionally fetches a small number of external resources.  Keep all
user-controlled URL handling here so SSRF protections stay consistent across
features.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hmac
import ipaddress
import os
import socket
from typing import Awaitable, Callable, Iterable, Optional, Sequence
from urllib.parse import SplitResult, urljoin, urlsplit

import aiohttp
from aiohttp import web


DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_HTML_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_IMAGE_BYTES = 15 * 1024 * 1024
_ALLOWED_SCHEMES = {"http", "https"}
_LOCAL_HOST_SUFFIXES = (
    "localhost",
    "local",
    "localdomain",
    "lan",
    "home",
    "home.arpa",
    "internal",
    "intranet",
    "corp",
    "private",
)
_ARCHIVE_CONTROL_PATHS = {
    "/api/archive/sync",
    "/api/archive/posters",
    "/api/archive/vacuum",
    "/api/archive/cancel",
}


class URLSecurityError(ValueError):
    """Raised when an outbound URL fails the public-network security policy."""


class ResponseTooLarge(URLSecurityError):
    """Raised when an outbound response exceeds the configured byte limit."""


class TooManyRedirects(URLSecurityError):
    """Raised when a manually-followed redirect chain exceeds the limit."""


@dataclass(frozen=True)
class SafeFetchResult:
    status: int
    body: bytes
    final_url: str
    content_type: str = ""
    charset: Optional[str] = None


def _normalize_hostname(hostname: str) -> str:
    host = (hostname or "").strip().rstrip(".").lower()
    if not host:
        raise URLSecurityError("URL has no hostname")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise URLSecurityError("Invalid hostname") from exc


def _is_local_hostname(hostname: str) -> bool:
    host = _normalize_hostname(hostname)
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _LOCAL_HOST_SUFFIXES)


def _as_ip(hostname: str) -> Optional[ipaddress._BaseAddress]:
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _ensure_public_ip(value: str) -> None:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError as exc:
        raise URLSecurityError("Resolved destination is not a valid IP address") from exc
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    # is_global rejects loopback, private/RFC1918, link-local, unspecified,
    # documentation/reserved ranges, shared space, and other non-public ranges.
    if (
        not ip.is_global
        or ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise URLSecurityError(f"Destination IP is not public: {ip}")


def hostname_matches_domain(hostname: str, domain: str) -> bool:
    """Return True only for an exact domain or a real DNS subdomain boundary."""
    host = _normalize_hostname(hostname)
    allowed = _normalize_hostname(domain)
    return host == allowed or host.endswith(f".{allowed}")


def validate_url_structure(
    url: str,
    allowed_domains: Optional[Iterable[str]] = None,
) -> SplitResult:
    """Validate URL syntax, scheme, hostname, port, local ranges and allowlist.

    DNS is intentionally not performed here.  Use one of the public-network
    validators below before any outbound connection.
    """
    if not isinstance(url, str) or not url.strip():
        raise URLSecurityError("URL is empty")
    if len(url) > 4096:
        raise URLSecurityError("URL is too long")

    try:
        parsed = urlsplit(url.strip())
    except ValueError as exc:
        raise URLSecurityError("Malformed URL") from exc

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise URLSecurityError("Only http and https URLs are permitted")
    if parsed.username is not None or parsed.password is not None:
        raise URLSecurityError("Userinfo in URLs is not permitted")
    if not parsed.hostname:
        raise URLSecurityError("URL has no hostname")

    host = _normalize_hostname(parsed.hostname)
    if _is_local_hostname(host):
        raise URLSecurityError("Local-network hostnames are not permitted")

    try:
        port = parsed.port
    except ValueError as exc:
        raise URLSecurityError("Invalid URL port") from exc

    if port is not None:
        expected = 443 if parsed.scheme.lower() == "https" else 80
        if port != expected:
            raise URLSecurityError("Non-standard destination ports are not permitted")

    literal_ip = _as_ip(host)
    if literal_ip is not None:
        _ensure_public_ip(str(literal_ip))

    if allowed_domains:
        domains = tuple(allowed_domains)
        if not any(hostname_matches_domain(host, domain) for domain in domains):
            raise URLSecurityError("Hostname is not permitted for this service")

    return parsed


def _validate_resolved_addresses(addresses: Sequence[str]) -> None:
    if not addresses:
        raise URLSecurityError("Hostname did not resolve")
    for address in addresses:
        _ensure_public_ip(address)


def validate_public_http_url(
    url: str,
    allowed_domains: Optional[Iterable[str]] = None,
) -> SplitResult:
    """Synchronously validate a URL and every DNS result as public-routable."""
    parsed = validate_url_structure(url, allowed_domains)
    host = _normalize_hostname(parsed.hostname or "")
    literal_ip = _as_ip(host)
    if literal_ip is not None:
        return parsed

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise URLSecurityError("Hostname could not be resolved") from exc
    addresses = sorted({info[4][0] for info in infos})
    _validate_resolved_addresses(addresses)
    return parsed


async def validate_public_http_url_async(
    url: str,
    allowed_domains: Optional[Iterable[str]] = None,
) -> SplitResult:
    """Asynchronously validate a URL and every DNS result as public-routable."""
    parsed = validate_url_structure(url, allowed_domains)
    host = _normalize_hostname(parsed.hostname or "")
    literal_ip = _as_ip(host)
    if literal_ip is not None:
        return parsed

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise URLSecurityError("Hostname could not be resolved") from exc
    addresses = sorted({info[4][0] for info in infos})
    _validate_resolved_addresses(addresses)
    return parsed


class PublicDNSResolver(aiohttp.abc.AbstractResolver):
    """aiohttp resolver that refuses any non-public address at connect time.

    This repeats the preflight DNS check deliberately: the connector validates
    the addresses it actually receives, reducing DNS-rebinding exposure.
    """

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_UNSPEC):
        normalized = _normalize_hostname(host)
        if _is_local_hostname(normalized):
            raise OSError("Local-network hostname blocked")

        literal_ip = _as_ip(normalized)
        if literal_ip is not None:
            _ensure_public_ip(str(literal_ip))

        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(
            normalized,
            port,
            type=socket.SOCK_STREAM,
            family=family or socket.AF_UNSPEC,
        )
        addresses = sorted({info[4][0] for info in infos})
        _validate_resolved_addresses(addresses)

        results = []
        seen = set()
        for fam, socktype, proto, _canonname, sockaddr in infos:
            address = sockaddr[0]
            key = (fam, address, port)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "hostname": normalized,
                    "host": address,
                    "port": port,
                    "family": fam,
                    "proto": proto,
                    "flags": 0,
                }
            )
        return results

    async def close(self) -> None:
        return None


async def fetch_with_aiohttp_session(
    session,
    url: str,
    *,
    allowed_domains: Optional[Iterable[str]] = None,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    max_bytes: int = DEFAULT_MAX_HTML_BYTES,
    validator: Callable[[str, Optional[Iterable[str]]], Awaitable[SplitResult]] = validate_public_http_url_async,
) -> SafeFetchResult:
    """Fetch with manual redirects so every hop is revalidated."""
    current_url = url
    for hop in range(max_redirects + 1):
        await validator(current_url, allowed_domains)
        async with session.get(current_url, allow_redirects=False) as response:
            status = int(response.status)
            if 300 <= status < 400:
                location = response.headers.get("Location")
                if not location:
                    raise URLSecurityError("Redirect response had no Location header")
                if hop >= max_redirects:
                    raise TooManyRedirects("Redirect limit exceeded")
                current_url = urljoin(current_url, location)
                # Validate immediately as well as again before the next request.
                await validator(current_url, allowed_domains)
                continue

            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    parsed_length = int(content_length)
                except (TypeError, ValueError):
                    parsed_length = None
                if parsed_length is not None and parsed_length > max_bytes:
                    raise ResponseTooLarge("Response exceeds maximum allowed size")

            chunks = []
            total = 0
            async for chunk in response.content.iter_chunked(64 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise ResponseTooLarge("Response exceeds maximum allowed size")
                chunks.append(chunk)

            return SafeFetchResult(
                status=status,
                body=b"".join(chunks),
                final_url=current_url,
                content_type=response.headers.get("Content-Type", "").split(";", 1)[0].strip(),
                charset=getattr(response, "charset", None),
            )

    raise TooManyRedirects("Redirect limit exceeded")


async def fetch_public_bytes(
    url: str,
    *,
    allowed_domains: Optional[Iterable[str]] = None,
    headers: Optional[dict] = None,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    max_bytes: int = DEFAULT_MAX_HTML_BYTES,
    timeout_seconds: float = 12.0,
) -> SafeFetchResult:
    """Fetch a public HTTP(S) URL with connect-time DNS enforcement."""
    connector = aiohttp.TCPConnector(
        resolver=PublicDNSResolver(),
        use_dns_cache=False,
    )
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(
        headers=headers or {},
        timeout=timeout,
        connector=connector,
        trust_env=False,
    ) as session:
        return await fetch_with_aiohttp_session(
            session,
            url,
            allowed_domains=allowed_domains,
            max_redirects=max_redirects,
            max_bytes=max_bytes,
        )


def _host_header_is_loopback(host_header: str) -> bool:
    if not host_header:
        return False
    try:
        parsed = urlsplit(f"//{host_header}")
        host = _normalize_hostname(parsed.hostname or "")
    except (ValueError, URLSecurityError):
        return False
    if host == "localhost":
        return True
    ip = _as_ip(host)
    return bool(ip and ip.is_loopback)


def _peer_is_loopback(request: web.Request) -> bool:
    peer = None
    if request.transport is not None:
        peer = request.transport.get_extra_info("peername")
    if not peer:
        return False
    address = peer[0] if isinstance(peer, tuple) else str(peer)
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _forwarded_request_is_nonlocal(request: web.Request) -> bool:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        first = forwarded_for.split(",", 1)[0].strip()
        try:
            if not ipaddress.ip_address(first).is_loopback:
                return True
        except ValueError:
            return True

    forwarded = request.headers.get("Forwarded", "")
    if forwarded:
        # A proxy header means this request was forwarded.  Only accept an
        # explicitly loopback for= value as local; otherwise require auth.
        lowered = forwarded.lower()
        if "for=127.0.0.1" not in lowered and "for=\"[::1]\"" not in lowered and "for=::1" not in lowered:
            return True
    return False


def is_trusted_local_request(request: web.Request) -> bool:
    return (
        _peer_is_loopback(request)
        and _host_header_is_loopback(request.headers.get("Host", ""))
        and not _forwarded_request_is_nonlocal(request)
    )


def archive_control_authorized(request: web.Request) -> bool:
    """Allow normal localhost use; require an optional admin token otherwise."""
    if is_trusted_local_request(request):
        return True

    expected = os.environ.get("DVDREWIND_ADMIN_TOKEN", "").strip()
    provided = request.headers.get("X-DVDRewind-Admin-Token", "").strip()
    return bool(expected and provided and hmac.compare_digest(expected, provided))


@web.middleware
async def archive_control_middleware(request: web.Request, handler):
    if request.method.upper() == "POST" and request.path in _ARCHIVE_CONTROL_PATHS:
        if not archive_control_authorized(request):
            return web.json_response(
                {
                    "ok": False,
                    "error": "Archive controls are restricted to localhost unless an admin token is configured.",
                },
                status=403,
            )
    return await handler(request)
