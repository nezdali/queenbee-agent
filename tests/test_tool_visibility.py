import config
from core.tool_factory import ToolMeta
from handlers import queen_bee


def _tool(name, *, status="approved", permission="public"):
    return ToolMeta(
        name=name,
        description=name,
        requires_google_auth=False,
        trigger_keywords=[name],
        created_at="2026-01-01T00:00:00+00:00",
        status=status,
        permission=permission,
    )


def test_visible_tools_for_normal_user_filters_status_and_rbac(monkeypatch):
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    monkeypatch.setattr(queen_bee, "QB_ADMIN_USER_ID", 999)

    import core.tool_registry as registry
    monkeypatch.setattr(registry, "_user_roles", lambda user_id: ["public"])
    monkeypatch.setattr(
        registry,
        "_role_has_permission",
        lambda roles, required: required == "public",
    )

    tools = [
        _tool("public_tool"),
        _tool("finance_tool", permission="finance.read"),
        _tool("pending_tool", status="pending_review"),
        _tool("rejected_tool", status="rejected"),
    ]

    visible = queen_bee._visible_tools_for_user(123, tools)

    assert [t.name for t in visible] == ["public_tool"]


def test_visible_tools_for_authorized_role_includes_restricted_tool(monkeypatch):
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    monkeypatch.setattr(queen_bee, "QB_ADMIN_USER_ID", 999)

    import core.tool_registry as registry
    monkeypatch.setattr(registry, "_user_roles", lambda user_id: ["finance"])
    monkeypatch.setattr(
        registry,
        "_role_has_permission",
        lambda roles, required: required in {"public", "finance.read"},
    )

    tools = [
        _tool("public_tool"),
        _tool("finance_tool", permission="finance.read"),
    ]

    visible = queen_bee._visible_tools_for_user(123, tools)

    assert [t.name for t in visible] == ["public_tool", "finance_tool"]


def test_visible_tools_for_admin_includes_pending_and_restricted(monkeypatch):
    monkeypatch.setattr(config, "QB_ADMIN_USER_ID", 999)
    monkeypatch.setattr(queen_bee, "QB_ADMIN_USER_ID", 999)

    tools = [
        _tool("public_tool"),
        _tool("finance_tool", permission="finance.read"),
        _tool("pending_tool", status="pending_review"),
        _tool("rejected_tool", status="rejected"),
    ]

    visible = queen_bee._visible_tools_for_user(999, tools)

    assert [t.name for t in visible] == [
        "public_tool",
        "finance_tool",
        "pending_tool",
        "rejected_tool",
    ]
