"""Shared URL safety helpers for outbound HTTP requests."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


async def resolve_host_ips(hostname: str, port: int) -> set[str]:
    """Resolve a hostname to IP strings for SSRF validation."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(
        hostname,
        port,
        type=socket.SOCK_STREAM,
    )
    return {info[4][0].split("%", 1)[0] for info in infos}


async def validate_public_url(url: str) -> tuple[bool, str | None]:
    """Allow only HTTP(S) URLs that resolve exclusively to public IP space."""
    try:
        parsed = urlsplit(url)
    except Exception:
        return False, "Invalid URL"

    if parsed.scheme.lower() not in {"http", "https"}:
        return False, "Only http:// and https:// URLs are allowed"
    if not parsed.hostname:
        return False, "URL must include a hostname"
    if parsed.username is not None or parsed.password is not None:
        return False, "URLs with embedded credentials are not allowed"

    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError:
        return False, "Invalid URL port"

    host = parsed.hostname.rstrip(".")
    try:
        literal_ip = ipaddress.ip_address(host)
        ips = {str(literal_ip)}
    except ValueError:
        try:
            ips = await resolve_host_ips(host, port)
        except Exception:
            return False, "Hostname could not be resolved"

    if not ips:
        return False, "Hostname did not resolve to an address"

    for value in ips:
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            return False, "Hostname resolved to an invalid address"
        if not ip.is_global:
            return False, f"URL resolves to a non-public address: {ip}"

    return True, None
