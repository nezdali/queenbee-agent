"""
Unified tool registry with RBAC.

Every tool (built-in *and* QB-generated tool modules) is registered here.
The registry controls which OpenAI function-calling schemas are exposed to
each user based on their roles.
"""

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ToolDef:
    """A registered tool."""
    name: str
    schema: dict                       # OpenAI function-calling schema
    handler: Callable[..., Awaitable]  # async (args_dict, user_context) -> dict
    permission: str = "public"         # required role name
    is_dynamic: bool = False           # True for QB-generated modules
    available_check: Callable[[], bool] | None = None  # optional runtime check


# ---------------------------------------------------------------------------
# Registry singleton
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ToolDef] = {}


def register(
    name: str,
    *,
    schema: dict,
    handler: Callable,
    permission: str = "public",
    is_dynamic: bool = False,
    available_check: Callable[[], bool] | None = None,
) -> None:
    """Register (or re-register) a tool."""
    _REGISTRY[name] = ToolDef(
        name=name,
        schema=schema,
        handler=handler,
        permission=permission,
        is_dynamic=is_dynamic,
        available_check=available_check,
    )


def unregister(name: str) -> None:
    """Remove a tool from the registry."""
    _REGISTRY.pop(name, None)


def get(name: str) -> ToolDef | None:
    return _REGISTRY.get(name)


def all_tools() -> list[ToolDef]:
    return list(_REGISTRY.values())


# ---------------------------------------------------------------------------
# RBAC helpers
# ---------------------------------------------------------------------------

def _user_roles(user_id: int) -> list[str]:
    """Return the list of roles for a user.

    Checks the persistent JSON store first (live updates from /adduser),
    then falls back to the env-var-built USER_ROLES dict.
    """
    try:
        from core import user_roles as _store
        info = _store.list_all().get(int(user_id))
        if info:
            return list(info.get("roles", ["public"]))
    except Exception:
        pass
    from config import USER_ROLES
    return USER_ROLES.get(user_id, ["public"])


def _role_has_permission(roles: list[str], required: str) -> bool:
    """Check if any of the user's roles grant access to the required permission."""
    from config import ROLE_PERMISSIONS
    for role in roles:
        patterns = ROLE_PERMISSIONS.get(role, [])
        for pat in patterns:
            if fnmatch.fnmatch(required, pat):
                return True
    return False


def user_has_access(user_id: int, tool_name: str) -> bool:
    """Check if a user can use a specific tool."""
    tool = _REGISTRY.get(tool_name)
    if not tool:
        return False
    roles = _user_roles(user_id)
    return _role_has_permission(roles, tool.permission)


def get_schemas_for_user(user_id: int) -> list[dict]:
    """Return the OpenAI function-calling schemas the user is allowed to use.

    Excludes tools whose available_check returns False and the special
    'run_tool' tool (its schema is built dynamically per-user).
    """
    roles = _user_roles(user_id)
    result = []
    for tool in _REGISTRY.values():
        # run_tool has a dynamic schema built separately
        if tool.name == "run_tool":
            continue
        if tool.available_check and not tool.available_check():
            continue
        if _role_has_permission(roles, tool.permission):
            result.append(tool.schema)
    return result


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

async def dispatch(name: str, args: dict, user_context: dict | None = None) -> dict:
    """Call a registered tool by name. Returns a result dict."""
    tool = _REGISTRY.get(name)
    if not tool:
        return {"error": f"Unknown tool: {name}"}
    try:
        return await tool.handler(args, user_context or {})
    except Exception as e:
        logger.error("Tool %s raised exception: %s", name, e)
        return {"error": str(e)}
