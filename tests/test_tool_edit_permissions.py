import asyncio
import json
import sys
import types

import config
from core import tool_factory


def _install_fake_model_router(monkeypatch, payload):
    module = types.ModuleType("services.model_router")

    class FakeModelRouter:
        def codex_model(self):
            return "fake-model"

        async def create_json(self, *args, **kwargs):
            return json.dumps(payload)

    module.ModelRouter = FakeModelRouter
    monkeypatch.setitem(sys.modules, "services.model_router", module)


def test_edit_tool_preserves_existing_permission(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_factory, "TOOLS_DIR", tmp_path)
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)

    name = "finance_report"
    (tmp_path / f"{name}.py").write_text(
        "async def run(context: dict) -> str:\n    return 'old'\n",
        encoding="utf-8",
    )
    (tmp_path / f"{name}.json").write_text(
        json.dumps({
            "name": name,
            "description": "Finance report",
            "requires_google_auth": False,
            "trigger_keywords": ["finance"],
            "created_at": "2026-01-01T00:00:00+00:00",
            "version": 3,
            "status": "approved",
            "created_by": 123,
            "help_example": "finance",
            "permission": "finance.read",
        }),
        encoding="utf-8",
    )

    _install_fake_model_router(
        monkeypatch,
        {
            "description": "Updated finance report",
            "trigger_keywords": ["finance", "report"],
            "code": "async def run(context: dict) -> str:\n    return 'new'\n",
        },
    )

    meta, code = asyncio.run(
        tool_factory.edit_tool(name, "improve formatting", user_id=999)
    )

    assert meta is not None
    assert meta.permission == "finance.read"
    assert meta.version == 4
    assert code.endswith("return 'new'\n")
