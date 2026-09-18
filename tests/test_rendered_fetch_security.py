import asyncio

from core import tool_utils


def test_browser_request_guard_allows_non_network_resources():
    cache = {}

    assert asyncio.run(
        tool_utils._validate_browser_request_url("data:text/plain,hello", cache)
    ) == (True, None)
    assert asyncio.run(
        tool_utils._validate_browser_request_url("about:blank", cache)
    ) == (True, None)


def test_browser_request_guard_blocks_non_http_scheme():
    allowed, reason = asyncio.run(
        tool_utils._validate_browser_request_url("file:///etc/passwd", {})
    )

    assert allowed is False
    assert "scheme" in reason


def test_browser_request_guard_uses_shared_validator_and_cache(monkeypatch):
    calls = []

    async def _validate(url):
        calls.append(url)
        return True, None

    monkeypatch.setattr(tool_utils, "validate_public_url", _validate)
    cache = {}

    first = asyncio.run(
        tool_utils._validate_browser_request_url(
            "https://example.com/a.js",
            cache,
        )
    )
    second = asyncio.run(
        tool_utils._validate_browser_request_url(
            "https://example.com/b.css",
            cache,
        )
    )

    assert first == (True, None)
    assert second == (True, None)
    assert calls == ["https://example.com/a.js"]


def test_fetch_rendered_blocks_private_url_before_playwright(monkeypatch):
    async def _deny(url):
        return False, "private address"

    monkeypatch.setattr(tool_utils, "validate_public_url", _deny)

    result = asyncio.run(
        tool_utils.fetch_rendered("http://127.0.0.1/admin")
    )

    assert result["status"] == 0
    assert "Blocked URL" in result["error"]


def test_fetch_rendered_reports_missing_playwright(monkeypatch):
    async def _allow(url):
        return True, None

    monkeypatch.setattr(tool_utils, "validate_public_url", _allow)

    import builtins
    original_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == "playwright.async_api":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)

    result = asyncio.run(
        tool_utils.fetch_rendered("https://example.com")
    )

    assert result["status"] == 0
    assert "Playwright is not installed" in result["error"]
