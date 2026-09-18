import asyncio

import config
from core import tool_registry


async def _ok_handler(args, context):
    return {"args": args, "user_id": context.get("user_id")}


async def _boom_handler(args, context):
    raise RuntimeError("boom")


def setup_function():
    tool_registry._REGISTRY.clear()


def teardown_function():
    tool_registry._REGISTRY.clear()


def test_register_get_and_unregister():
    schema = {"type": "function", "function": {"name": "hello"}}

    tool_registry.register(
        "hello",
        schema=schema,
        handler=_ok_handler,
        permission="public",
    )

    tool = tool_registry.get("hello")
    assert tool is not None
    assert tool.name == "hello"
    assert tool.schema == schema

    tool_registry.unregister("hello")
    assert tool_registry.get("hello") is None


def test_get_schemas_for_user_applies_rbac_and_availability(monkeypatch):
    monkeypatch.setattr(
        config,
        "ROLE_PERMISSIONS",
        {
            "public": ["public"],
            "finance": ["public", "finance.*"],
        },
    )
    monkeypatch.setattr(
        tool_registry,
        "_user_roles",
        lambda user_id: ["finance"],
    )

    public_schema = {"type": "function", "function": {"name": "public_tool"}}
    finance_schema = {"type": "function", "function": {"name": "finance_tool"}}
    unavailable_schema = {"type": "function", "function": {"name": "unavailable_tool"}}
    run_tool_schema = {"type": "function", "function": {"name": "run_tool"}}

    tool_registry.register("public_tool", schema=public_schema, handler=_ok_handler)
    tool_registry.register(
        "finance_tool",
        schema=finance_schema,
        handler=_ok_handler,
        permission="finance.read",
    )
    tool_registry.register(
        "unavailable_tool",
        schema=unavailable_schema,
        handler=_ok_handler,
        available_check=lambda: False,
    )
    tool_registry.register("run_tool", schema=run_tool_schema, handler=_ok_handler)

    schemas = tool_registry.get_schemas_for_user(123)

    assert public_schema in schemas
    assert finance_schema in schemas
    assert unavailable_schema not in schemas
    assert run_tool_schema not in schemas


def test_dispatch_returns_result_and_converts_exceptions_to_errors():
    tool_registry.register(
        "ok",
        schema={"type": "function", "function": {"name": "ok"}},
        handler=_ok_handler,
    )
    tool_registry.register(
        "boom",
        schema={"type": "function", "function": {"name": "boom"}},
        handler=_boom_handler,
    )

    result = asyncio.run(
        tool_registry.dispatch("ok", {"x": 1}, {"user_id": 42})
    )
    error = asyncio.run(tool_registry.dispatch("boom", {}))
    missing = asyncio.run(tool_registry.dispatch("missing", {}))

    assert result == {"args": {"x": 1}, "user_id": 42}
    assert error == {"error": "boom"}
    assert missing == {"error": "Unknown tool: missing"}


def test_dispatch_enforces_rbac_for_non_public_tools(monkeypatch):
    monkeypatch.setattr(
        config,
        "ROLE_PERMISSIONS",
        {
            "public": ["public"],
            "finance": ["public", "finance.*"],
        },
    )
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    monkeypatch.setattr(
        tool_registry,
        "_user_roles",
        lambda user_id: ["finance"] if user_id == 123 else ["public"],
    )

    tool_registry.register(
        "finance_tool",
        schema={"type": "function", "function": {"name": "finance_tool"}},
        handler=_ok_handler,
        permission="finance.read",
    )

    allowed = asyncio.run(
        tool_registry.dispatch("finance_tool", {}, {"user_id": 123})
    )
    denied = asyncio.run(
        tool_registry.dispatch("finance_tool", {}, {"user_id": 456})
    )
    anonymous = asyncio.run(tool_registry.dispatch("finance_tool", {}))
    admin = asyncio.run(
        tool_registry.dispatch("finance_tool", {}, {"user_id": 999})
    )

    assert allowed["user_id"] == 123
    assert denied == {
        "error": "Permission denied: finance_tool requires finance.read"
    }
    assert anonymous == {
        "error": "Permission denied: finance_tool requires finance.read"
    }
    assert admin["user_id"] == 999


def test_dispatch_blocks_unavailable_tools():
    tool_registry.register(
        "offline",
        schema={"type": "function", "function": {"name": "offline"}},
        handler=_ok_handler,
        available_check=lambda: False,
    )

    result = asyncio.run(
        tool_registry.dispatch("offline", {}, {"user_id": 42})
    )

    assert result == {"error": "Tool unavailable: offline"}
