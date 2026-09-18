import asyncio
from types import SimpleNamespace

from core.tool_factory import ToolMeta
from handlers import queen_bee


class _FakeMessage:
    def __init__(self):
        self.chat_id = 777
        self.chat = self
        self.replies = []

    async def send_action(self, *args, **kwargs):
        return None

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return self

    async def edit_text(self, *args, **kwargs):
        return None


class _FakeContext:
    def __init__(self):
        self.user_data = {}
        self.args = []


class _FakeUpdate:
    def __init__(self, user_id=123):
        self.message = _FakeMessage()
        self.effective_user = SimpleNamespace(
            id=user_id,
            username="alice",
            first_name="Alice",
        )


def test_generated_pending_tool_preserves_security_metadata(monkeypatch):
    meta = ToolMeta(
        name="demo",
        description="Demo",
        requires_google_auth=False,
        trigger_keywords=["demo"],
        created_at="2026-01-01T00:00:00+00:00",
        version=1,
        status="pending_review",
        created_by=123,
        help_example="demo",
        permission="finance.read",
    )

    async def _generate_tool(description, user_id=0):
        return meta, "async def run(context: dict) -> str:\n    return 'ok'\n"

    async def _test_tool_code(code, context, timeout_sec=30):
        return "ok"

    import core.tool_factory as tool_factory
    monkeypatch.setattr(tool_factory, "generate_tool", _generate_tool)
    monkeypatch.setattr(tool_factory, "test_tool_code", _test_tool_code)
    monkeypatch.setattr(queen_bee, "_check_qb_rate", lambda user_id: None)
    monkeypatch.setattr(queen_bee, "_record_qb_usage", lambda user_id: None)

    update = _FakeUpdate(user_id=123)
    context = _FakeContext()

    asyncio.run(
        queen_bee._handle_qb_request(update, context, "make a demo tool")
    )

    pending = context.user_data["pending_tool"]
    assert pending["status"] == "pending_review"
    assert pending["created_by"] == 123
    assert pending["permission"] == "finance.read"
    assert pending["help_example"] == "demo"


def test_qbsave_reconstructs_pending_review_meta(monkeypatch):
    saved = {}

    def _save_tool(meta, code):
        saved["meta"] = meta
        saved["code"] = code

    async def _review(*args, **kwargs):
        return None

    import core.tool_factory as tool_factory
    monkeypatch.setattr(tool_factory, "save_tool", _save_tool)
    monkeypatch.setattr(tool_factory, "review_tool_security", _review)
    monkeypatch.setattr(queen_bee, "QB_ADMIN_USER_ID", 999)

    update = _FakeUpdate(user_id=123)
    context = _FakeContext()
    context.user_data["pending_tool"] = {
        "name": "demo",
        "description": "Demo",
        "requires_google_auth": False,
        "trigger_keywords": ["demo"],
        "created_at": "2026-01-01T00:00:00+00:00",
        "version": 1,
        "status": "pending_review",
        "created_by": 123,
        "help_example": "demo",
        "permission": "public",
        "code": "async def run(context: dict) -> str:\n    return 'ok'\n",
    }

    asyncio.run(queen_bee.qbsave_command(update, context))

    assert saved["meta"].status == "pending_review"
    assert saved["meta"].created_by == 123
    assert "pending_tool" not in context.user_data
