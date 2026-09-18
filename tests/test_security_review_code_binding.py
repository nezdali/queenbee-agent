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


def test_successful_review_does_not_approve_changed_code(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_factory, "TOOLS_DIR", tmp_path)
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    _install_fake_telegram(monkeypatch)

    original_code = "async def run(context: dict) -> str:\n    return 'old'\n"
    changed_code = "async def run(context: dict) -> str:\n    return 'new'\n"

    (tmp_path / "demo.py").write_text(original_code, encoding="utf-8")
    (tmp_path / "demo.json").write_text(
        json.dumps({
            "name": "demo",
            "description": "Demo",
            "requires_google_auth": False,
            "trigger_keywords": ["demo"],
            "created_at": "2026-01-01T00:00:00+00:00",
            "status": "pending_review",
            "created_by": 123,
        }),
        encoding="utf-8",
    )

    async def _review(*args, **kwargs):
        (tmp_path / "demo.py").write_text(changed_code, encoding="utf-8")
        return '{"safe": true, "issues": []}'

    _install_fake_model_router(monkeypatch, _review)

    asyncio.run(
        tool_factory.review_tool_security(
            "demo",
            original_code,
            created_by=123,
            bot=_FakeBot(),
        )
    )

    data = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    assert data["status"] == "pending_review"


def test_manual_review_callback_token_fits_telegram_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_factory, "TOOLS_DIR", tmp_path)
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    _install_fake_telegram(monkeypatch)

    name = "x" * 50
    code = "async def run(context: dict) -> str:\n    return 'ok'\n"
    (tmp_path / f"{name}.py").write_text(code, encoding="utf-8")
    (tmp_path / f"{name}.json").write_text(
        json.dumps({
            "name": name,
            "description": "Demo",
            "requires_google_auth": False,
            "trigger_keywords": ["demo"],
            "created_at": "2026-01-01T00:00:00+00:00",
            "status": "pending_review",
            "created_by": 123,
        }),
        encoding="utf-8",
    )

    async def _unsafe(*args, **kwargs):
        return '{"safe": false, "issues": ["review me"]}'

    _install_fake_model_router(monkeypatch, _unsafe)
    bot = _FakeBot()

    asyncio.run(
        tool_factory.review_tool_security(
            name,
            code,
            created_by=123,
            bot=bot,
        )
    )

    admin_message = next(m for m in bot.messages if m.get("chat_id") == 999)
    keyboard = admin_message["reply_markup"]
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]

    assert callbacks
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks)
    assert callbacks[0].startswith(f"qb_a:{name}:")
    assert callbacks[1].startswith(f"qb_r:{name}:")
