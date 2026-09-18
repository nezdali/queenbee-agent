"""
Shared utility helpers for QB tools.

Tools can import these inside their run() function:
    from tool_utils import fetch_json, fetch_text, fetch_html, parse_args
"""

import json
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from core.url_safety import validate_public_url

_DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
_DEFAULT_TIMEOUT = 15
_MAX_REDIRECTS = 5


def _safe_headers(headers: dict | None, *, accept_json: bool = False) -> dict:
    out = {"User-Agent": _DEFAULT_UA}
    if accept_json:
        out["Accept"] = "application/json"
    for key, value in (headers or {}).items():
        if str(key).lower() in {"host", "connection", "proxy-authorization"}:
            continue
        out[str(key)] = str(value)
    return out


async def _request_text(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
    method: str = "GET",
    json_body: dict | None = None,
) -> tuple[str | None, int]:
    """Perform a guarded HTTP request and validate every redirect target."""
    current_url = str(url)
    current_method = method.upper()
    current_params = params
    current_json = json_body

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers=_safe_headers(headers),
        ) as session:
            for redirect_count in range(_MAX_REDIRECTS + 1):
                allowed, _ = await validate_public_url(current_url)
                if not allowed:
                    return None, 0

                async with session.request(
                    current_method,
                    current_url,
                    params=current_params,
                    json=current_json if current_method != "GET" else None,
                    allow_redirects=False,
                ) as resp:
                    if 300 <= resp.status < 400 and resp.headers.get("Location"):
                        if redirect_count >= _MAX_REDIRECTS:
                            return None, 0

                        current_url = urljoin(current_url, resp.headers["Location"])
                        current_params = None

                        # Match common browser/client redirect behavior.
                        if resp.status == 303 or (
                            resp.status in {301, 302} and current_method == "POST"
                        ):
                            current_method = "GET"
                            current_json = None
                        continue

                    return await resp.text(), resp.status
    except Exception:
        return None, 0

    return None, 0


async def fetch_json(url: str, *, params: dict | None = None,
                     headers: dict | None = None, timeout: int = _DEFAULT_TIMEOUT,
                     method: str = "GET", json_body: dict | None = None) -> tuple[dict | list | None, int]:
    """Fetch JSON from a public HTTP(S) URL.

    Every request and redirect target is checked against the shared SSRF guard.
    Returns (parsed_data, http_status). On validation/network/JSON error returns
    (None, 0) for transport/validation errors or (None, status) for bad JSON.
    """
    text, status = await _request_text(
        url,
        params=params,
        headers=_safe_headers(headers, accept_json=True),
        timeout=timeout,
        method=method,
        json_body=json_body,
    )
    if text is None:
        return None, status
    try:
        return json.loads(text), status
    except Exception:
        return None, status


async def fetch_text(url: str, *, params: dict | None = None,
                     headers: dict | None = None,
                     timeout: int = _DEFAULT_TIMEOUT) -> tuple[str | None, int]:
    """Fetch raw text from a public HTTP(S) URL.

    Every request and redirect target is checked against the shared SSRF guard.
    """
    return await _request_text(
        url,
        params=params,
        headers=headers,
        timeout=timeout,
        method="GET",
    )


async def fetch_html(url: str, *, params: dict | None = None,
                     headers: dict | None = None,
                     timeout: int = _DEFAULT_TIMEOUT) -> tuple[BeautifulSoup | None, int]:
    """Fetch a public page and return a BeautifulSoup object."""
    text, status = await fetch_text(url, params=params, headers=headers, timeout=timeout)
    if text is None:
        return None, 0
    return BeautifulSoup(text, "html.parser"), status


def parse_args(context: dict) -> list[str]:
    """Extract the args list from a tool context dict."""
    return context.get("args", []) or []


async def fetch_rendered(url: str, *, selector: str | None = None,
                         wait_for: str | None = None,
                         timeout: int = 20,
                         return_html: bool = False,
                         stealth: bool = False) -> dict:
    """Fetch a fully JS-rendered page via shared headless Chromium (Playwright).

    The initial URL is checked by the shared SSRF guard before browser launch.
    """
    allowed, reason = await validate_public_url(url)
    if not allowed:
        return {"error": f"Blocked URL: {reason}", "status": 0, "url": url}

    from core.agent_tools import _tool_fetch_rendered_url
    return await _tool_fetch_rendered_url(
        url=url, selector=selector, wait_for=wait_for,
        timeout=timeout, return_html=return_html, stealth=stealth,
    )
