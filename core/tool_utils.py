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


async def _validate_browser_request_url(
    url: str,
    cache: dict[str, tuple[bool, str | None]],
) -> tuple[bool, str | None]:
    """Validate a browser request URL, caching repeated HTTP(S) origins."""
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()

    # Browser-internal, non-network resources cannot reach host services.
    if scheme in {"data", "blob", "about"}:
        return True, None

    if scheme not in {"http", "https"}:
        return False, f"Blocked browser request scheme: {scheme or '(none)'}"

    origin = f"{scheme}://{parsed.hostname or ''}:{parsed.port or (443 if scheme == 'https' else 80)}"
    if origin not in cache:
        cache[origin] = await validate_public_url(url)
    return cache[origin]


async def fetch_rendered(url: str, *, selector: str | None = None,
                         wait_for: str | None = None,
                         timeout: int = 20,
                         return_html: bool = False,
                         stealth: bool = False) -> dict:
    """Fetch a JS-rendered public page with Playwright.

    All HTTP(S) browser requests, including redirects and subresources, are
    checked against the shared SSRF guard. Playwright remains an optional
    dependency.
    """
    allowed, reason = await validate_public_url(url)
    if not allowed:
        return {"error": f"Blocked URL: {reason}", "status": 0, "url": url}

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "error": (
                "Playwright is not installed. Install optional dependency "
                "'playwright' and run 'playwright install chromium'."
            ),
            "status": 0,
            "url": url,
        }

    timeout = max(3, min(int(timeout), 45))
    timeout_ms = timeout * 1000
    validation_cache: dict[str, tuple[bool, str | None]] = {}

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=_DEFAULT_UA,
                ignore_https_errors=False,
            )
            page = await context.new_page()

            async def _guard_route(route, request):
                req_allowed, _ = await _validate_browser_request_url(
                    request.url,
                    validation_cache,
                )
                if req_allowed:
                    await route.continue_()
                else:
                    await route.abort("blockedbyclient")

            await page.route("**/*", _guard_route)

            if stealth:
                try:
                    from playwright_stealth import stealth_async
                    await stealth_async(page)
                except ImportError:
                    pass
                except Exception:
                    pass

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )

            if wait_for:
                await page.wait_for_selector(wait_for, timeout=timeout_ms)

            if selector:
                elements = await page.locator(selector).all_inner_texts()
                text = "\n".join(elements)
            else:
                text = await page.locator("body").inner_text()

            title = await page.title()
            final_url = page.url
            status = response.status if response is not None else 0

            max_text = 20_000
            text_truncated = len(text) > max_text
            if text_truncated:
                text = text[:max_text]

            result = {
                "status": status,
                "url": final_url,
                "title": title,
                "text": text,
                "text_truncated": text_truncated,
                "stealth": bool(stealth),
            }

            if return_html:
                html = await page.content()
                max_html = 100_000
                html_truncated = len(html) > max_html
                if html_truncated:
                    html = html[:max_html]
                result["html"] = html
                result["html_truncated"] = html_truncated

            await context.close()
            await browser.close()
            return result
    except Exception as exc:
        return {
            "error": f"{type(exc).__name__}: {exc}",
            "status": 0,
            "url": url,
        }
