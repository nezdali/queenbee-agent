import asyncio

from core import agent_tools


def test_validate_public_url_allows_public_https(monkeypatch):
    async def _resolve(hostname, port):
        assert hostname == "example.com"
        assert port == 443
        return {"93.184.216.34"}

    monkeypatch.setattr(agent_tools, "_resolve_host_ips", _resolve)

    allowed, reason = asyncio.run(
        agent_tools._validate_public_url("https://example.com/data")
    )

    assert allowed is True
    assert reason is None


def test_validate_public_url_blocks_loopback_literal():
    allowed, reason = asyncio.run(
        agent_tools._validate_public_url("http://127.0.0.1:8080/admin")
    )

    assert allowed is False
    assert "non-public" in reason


def test_validate_public_url_blocks_cloud_metadata_address():
    allowed, reason = asyncio.run(
        agent_tools._validate_public_url(
            "http://169.254.169.254/latest/meta-data/"
        )
    )

    assert allowed is False
    assert "non-public" in reason


def test_validate_public_url_blocks_private_dns_resolution(monkeypatch):
    async def _resolve(hostname, port):
        return {"10.0.0.5"}

    monkeypatch.setattr(agent_tools, "_resolve_host_ips", _resolve)

    allowed, reason = asyncio.run(
        agent_tools._validate_public_url("https://internal.example/")
    )

    assert allowed is False
    assert "10.0.0.5" in reason


def test_validate_public_url_blocks_mixed_public_private_dns(monkeypatch):
    async def _resolve(hostname, port):
        return {"93.184.216.34", "192.168.1.10"}

    monkeypatch.setattr(agent_tools, "_resolve_host_ips", _resolve)

    allowed, reason = asyncio.run(
        agent_tools._validate_public_url("https://mixed.example/")
    )

    assert allowed is False
    assert "192.168.1.10" in reason


def test_validate_public_url_rejects_non_http_scheme():
    allowed, reason = asyncio.run(
        agent_tools._validate_public_url("file:///etc/passwd")
    )

    assert allowed is False
    assert "http://" in reason


def test_validate_public_url_rejects_embedded_credentials():
    allowed, reason = asyncio.run(
        agent_tools._validate_public_url(
            "https://user:password@example.com/private"
        )
    )

    assert allowed is False
    assert "embedded credentials" in reason
