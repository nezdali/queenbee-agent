import asyncio

import config
from core import tool_factory


def test_forbidden_intent_blocks_shell_execution(monkeypatch):
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)

    error = tool_factory.check_qb_request_intent(
        "create a tool that runs a shell command",
        user_id=123,
    )

    assert error is not None
    assert "command/shell execution" in error


def test_admin_bypasses_generated_code_pattern_validation(monkeypatch):
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    dangerous_code = """
async def run(context: dict) -> str:
    import os
    os.system("echo unsafe")
    return "done"
"""

    assert tool_factory._validate_tool_code(dangerous_code, user_id=123) is not None
    assert tool_factory._validate_tool_code(dangerous_code, user_id=999) is None


def test_test_tool_code_executes_valid_async_tool():
    code = """
async def run(context: dict) -> str:
    return f"hello {context['name']}"
"""

    result = asyncio.run(
        tool_factory.test_tool_code(code, {"name": "QueenBee"}, timeout_sec=1)
    )

    assert result == "hello QueenBee"


def test_generated_code_blocks_builtin_open(monkeypatch):
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    code = """
async def run(context: dict) -> str:
    with open("/etc/passwd", "r") as f:
        return f.read()
"""

    error = tool_factory._validate_tool_code(code, user_id=123)

    assert error is not None
    assert "file access/modification" in error


def test_generated_code_blocks_path_read_text(monkeypatch):
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    code = """
async def run(context: dict) -> str:
    from pathlib import Path
    return Path("/etc/hostname").read_text()
"""

    error = tool_factory._validate_tool_code(code, user_id=123)

    assert error is not None
    assert "file access/modification" in error


def test_generated_code_blocks_path_write_text(monkeypatch):
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    code = """
async def run(context: dict) -> str:
    from pathlib import Path
    Path("/tmp/qb.txt").write_text("hello")
    return "done"
"""

    error = tool_factory._validate_tool_code(code, user_id=123)

    assert error is not None
    assert "file access/modification" in error


def test_generated_code_blocks_shutil_move(monkeypatch):
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    code = """
async def run(context: dict) -> str:
    import shutil
    shutil.move("/tmp/a", "/tmp/b")
    return "done"
"""

    error = tool_factory._validate_tool_code(code, user_id=123)

    assert error is not None
    assert "file access/modification" in error


def test_generated_code_still_allows_normal_http_tool(monkeypatch):
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    code = """
async def run(context: dict) -> str:
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get("https://example.com") as resp:
            return await resp.text()
"""

    assert tool_factory._validate_tool_code(code, user_id=123) is None
