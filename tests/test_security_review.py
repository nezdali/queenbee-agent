import asyncio
import json
import sys
import types

import config
from core import tool_factory


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


def _install_fake_model_router(monkeypatch, create_json_impl):
    module = types.ModuleType("services.model_router")

    class FakeModelRouter:
        def codex_model(self):
            return "fake-model"

        async def create_json(self, *args, **kwargs):
            return await create_json_impl(*args, **kwargs)

    module.ModelRouter = FakeModelRouter
    monkeypatch.setitem(sys.modules, "services.model_router", module)


def _install_fake_telegram(monkeypatch):
    module = types.ModuleType("telegram")

    class InlineKeyboardButton:
        def __init__(self, text, callback_data=None):
            self.text = text
            self.callback_data = callback_data

    class InlineKeyboardMarkup:
        def __init__(self, rows):
            self.inline_keyboard = rows

    module.InlineKeyboardButton = InlineKeyboardButton
    module.InlineKeyboardMarkup = InlineKeyboardMarkup
    monkeypatch.setitem(sys.modules, "telegram", module)


def _write_manifest(tmp_path, name):
    manifest = tmp_path / f"{name}.json"
    manifest.write_text(
        json.dumps({"name": name, "status": "pending_review"}),
        encoding="utf-8",
    )
    return manifest


def test_security_review_exception_keeps_tool_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_factory, "TOOLS_DIR", tmp_path)
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    _install_fake_telegram(monkeypatch)

    async def _raise(*args, **kwargs):
        raise RuntimeError("review unavailable")

    _install_fake_model_router(monkeypatch, _raise)

    manifest = _write_manifest(tmp_path, "demo")
    bot = _FakeBot()

    asyncio.run(
        tool_factory.review_tool_security(
            "demo",
            "async def run(context): return 'ok'",
            created_by=123,
            bot=bot,
        )
    )

    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["status"] == "pending_review"
    assert any(m.get("chat_id") == 999 for m in bot.messages)
    assert any("manual admin review" in m.get("text", "") for m in bot.messages)


def test_security_review_malformed_response_keeps_tool_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_factory, "TOOLS_DIR", tmp_path)
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    _install_fake_telegram(monkeypatch)

    async def _malformed(*args, **kwargs):
        return "{}"

    _install_fake_model_router(monkeypatch, _malformed)

    manifest = _write_manifest(tmp_path, "demo")
    bot = _FakeBot()

    asyncio.run(
        tool_factory.review_tool_security(
            "demo",
            "async def run(context): return 'ok'",
            created_by=123,
            bot=bot,
        )
    )

    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["status"] == "pending_review"
    assert any(m.get("chat_id") == 999 for m in bot.messages)


def test_security_review_explicit_safe_true_approves(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_factory, "TOOLS_DIR", tmp_path)
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    _install_fake_telegram(monkeypatch)

    async def _safe(*args, **kwargs):
        return '{"safe": true, "issues": []}'

    _install_fake_model_router(monkeypatch, _safe)

    manifest = _write_manifest(tmp_path, "demo")
    bot = _FakeBot()

    asyncio.run(
        tool_factory.review_tool_security(
            "demo",
            "async def run(context): return 'ok'",
            created_by=123,
            bot=bot,
        )
    )

    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["status"] == "approved"
    assert any("passed security review" in m.get("text", "") for m in bot.messages)
