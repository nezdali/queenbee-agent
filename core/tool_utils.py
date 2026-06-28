"""
Shared utility helpers for QB tools.

Tools can import these inside their run() function:
    from tool_utils import fetch_json, fetch_text, fetch_html, parse_args
"""

import aiohttp
from bs4 import BeautifulSoup

_DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
_DEFAULT_TIMEOUT = 15


async def fetch_json(url: str, *, params: dict | None = None,
                     headers: dict | None = None, timeout: int = _DEFAULT_TIMEOUT,
                     method: str = "GET", json_body: dict | None = None) -> tuple[dict | list | None, int]:
    """Fetch JSON from a URL.

    Returns (parsed_data, http_status).
    On network error returns (None, 0).
    """
    h = {"User-Agent": _DEFAULT_UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout), headers=h
        ) as session:
            if method.upper() == "POST":
                async with session.post(url, params=params, json=json_body) as resp:
                    data = await resp.json(content_type=None)
                    return data, resp.status
            else:
                async with session.get(url, params=params) as resp:
                    data = await resp.json(content_type=None)
                    return data, resp.status
    except Exception:
        return None, 0


async def fetch_text(url: str, *, params: dict | None = None,
                     headers: dict | None = None,
                     timeout: int = _DEFAULT_TIMEOUT) -> tuple[str | None, int]:
    """Fetch raw text from a URL.

    Returns (text, http_status).
    On network error returns (None, 0).
    """
    h = {"User-Agent": _DEFAULT_UA}
    if headers:
        h.update(headers)
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout), headers=h
        ) as session:
            async with session.get(url, params=params) as resp:
                text = await resp.text()
                return text, resp.status
    except Exception:
        return None, 0


async def fetch_html(url: str, *, params: dict | None = None,
                     headers: dict | None = None,
                     timeout: int = _DEFAULT_TIMEOUT) -> tuple[BeautifulSoup | None, int]:
    """Fetch a page and return a BeautifulSoup object.

    Returns (soup, http_status).
    On network error returns (None, 0).
    """
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

    Use this for SPAs / Cloudflare-protected sites where fetch_html returns an
    empty shell. Returns a dict with keys:
        status, url, title, text, text_truncated, stealth
        html, html_truncated   (only if return_html=True)
        error                  (only on failure)

    Args:
        url:         full URL to fetch.
        selector:    optional CSS selector — if given, only innerText of matching
                     elements is returned in `text`.
        wait_for:    CSS selector to wait for before extracting (e.g. '.product-card').
        timeout:     max wait in seconds (3..45, default 20).
        return_html: include raw HTML in result.
        stealth:     hide automation fingerprints + load CSS/images. Slower but
                     bypasses many bot challenges.
    """
    from core.agent_tools import _tool_fetch_rendered_url
    return await _tool_fetch_rendered_url(
        url=url, selector=selector, wait_for=wait_for,
        timeout=timeout, return_html=return_html, stealth=stealth,
    )
