import asyncio

from core import tool_utils


def test_fetch_text_blocks_private_url_before_network(monkeypatch):
    called = {"session": False}

    async def _deny(url):
        return False, "URL resolves to a non-public address: 127.0.0.1"

    class _UnexpectedSession:
        def __init__(self, *args, **kwargs):
            called["session"] = True
            raise AssertionError("network session should not be created")

    monkeypatch.setattr(tool_utils, "validate_public_url", _deny)
    monkeypatch.setattr(tool_utils.aiohttp, "ClientSession", _UnexpectedSession)

    text, status = asyncio.run(
        tool_utils.fetch_text("http://127.0.0.1/private")
    )

    assert text is None
    assert status == 0
    assert called["session"] is True


def test_fetch_json_blocks_private_url(monkeypatch):
    async def _deny(url):
        return False, "blocked"

    monkeypatch.setattr(tool_utils, "validate_public_url", _deny)

    data, status = asyncio.run(
        tool_utils.fetch_json("http://169.254.169.254/latest/meta-data/")
    )

    assert data is None
    assert status == 0


def test_fetch_rendered_blocks_private_url_before_browser(monkeypatch):
    async def _deny(url):
        return False, "blocked internal address"

    monkeypatch.setattr(tool_utils, "validate_public_url", _deny)

    result = asyncio.run(
        tool_utils.fetch_rendered("http://127.0.0.1/admin")
    )

    assert result["status"] == 0
    assert "Blocked URL" in result["error"]
