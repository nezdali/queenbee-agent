"""
Built-in OpenAI-compatible tool schemas + handlers.

The LLM can call these tools autonomously during conversation. Only the
generic, project-agnostic tools live here:
  - list_tools       : list saved Queen Bee tools the user can access
  - fetch_url         : HTTP GET against any public URL / REST API
  - run_tool         : run a saved tool (dynamic schema built per-user)

Domain-specific tools (email, banking, fitness APIs, etc.) should live in
their own modules and register themselves via core.tool_registry.register().
"""

import json
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Static tool schemas
# ---------------------------------------------------------------------------
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_tools",
            "description": (
                "List all saved Queen Bee tools that the calling user can access "
                "(filtered by RBAC role). Returns name, description, and trigger keywords "
                "for each tool. Use this when the user asks 'show tools', 'list tools', etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Make an HTTP GET request to a public URL or REST API and return the response. "
                "Use this ONLY when the user explicitly asks to fetch data from a specific URL or "
                "when you need real-time data from a KNOWN, REAL API endpoint. "
                "NEVER invent or guess URLs. Only use URLs you know are real and correct."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to fetch, including query parameters.",
                    },
                    "headers": {
                        "type": "object",
                        "description": "Optional HTTP headers as key-value pairs.",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_strava_athlete",
            "description": (
                "Get a Strava athlete's profile and all-time/YTD/recent stats. "
                "By default returns the CALLING user's own data. "
                "Pass athlete_name to look up another user who has also connected Strava "
                "(must be registered in KNOWN_USERS in config.py)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "athlete_name": {
                        "type": "string",
                        "description": "Optional alias of another connected user. Omit for the caller's own data.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_strava_activities",
            "description": (
                "List recent Strava activities with distance, duration, pace, date, type, HR and power. "
                "By default returns the caller's activities. Pass athlete_name for another connected user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Number of activities (default 30, max 200)."},
                    "page": {"type": "integer", "description": "Page number (default 1)."},
                    "athlete_name": {"type": "string", "description": "Optional alias of another connected user."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_strava_activity",
            "description": (
                "Rich detail for one activity: power, HR, laps, zone distribution. "
                "Use AFTER get_strava_activities once you have an activity_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "activity_id": {"type": "integer", "description": "Activity ID from get_strava_activities."},
                    "athlete_name": {"type": "string", "description": "Optional alias of another connected user."},
                },
                "required": ["activity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_strava_activity_kudos",
            "description": (
                "List athletes who kudosed (liked) a Strava activity, plus its comments."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "activity_id": {"type": "integer", "description": "Activity ID from get_strava_activities."},
                    "athlete_name": {"type": "string", "description": "Optional alias of another connected user."},
                },
                "required": ["activity_id"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def _handler_list_tools(args: dict, ctx: dict) -> dict:
    from core.tool_factory import list_tools
    from core.tool_registry import _user_roles, _role_has_permission
    user_id = ctx.get("user_id", 0)
    roles = _user_roles(user_id)
    tools = [
        {
            "name": a.name,
            "description": a.description,
            "trigger_keywords": a.trigger_keywords,
            "status": a.status,
        }
        for a in list_tools()
        if a.status == "approved" and _role_has_permission(roles, a.permission)
    ]
    return {"tools": tools, "count": len(tools)}


async def _handler_fetch_url(args: dict, ctx: dict) -> dict:
    import aiohttp
    url = args.get("url")
    if not url:
        return {"error": "Missing 'url' argument"}
    headers = args.get("headers") or {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                content_type = resp.content_type or ""
                if "json" in content_type:
                    data = await resp.json()
                else:
                    data = await resp.text()
                # Truncate large text responses
                if isinstance(data, str) and len(data) > 20_000:
                    data = data[:20_000] + "\n... [truncated]"
                return {"status": resp.status, "data": data}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


async def _handler_run_tool(args: dict, ctx: dict) -> dict:
    """Execute a saved Queen Bee tool. Schema is built dynamically per-user
    by build_run_tool_schema() so the LLM only sees tools this user can run.
    """
    from core.tool_factory import run_tool
    tool_name = args.get("tool_name")
    if not tool_name:
        return {"error": "Missing 'tool_name' argument"}
    raw_extra = args.get("args", "")
    run_context = {
        "user_id": ctx.get("user_id", 0),
        "chat_id": ctx.get("chat_id"),
        "username": ctx.get("username"),
        "first_name": ctx.get("first_name"),
        "args": str(raw_extra).split() if raw_extra else [],
        "raw_extra": raw_extra,
        "trigger_word": tool_name,
        "user_message": ctx.get("user_message", ""),
    }
    result = await run_tool(tool_name, run_context)
    return {"result": result}


# ---------------------------------------------------------------------------
# Strava handlers (optional; no-op if STRAVA_CLIENT_ID is unset)
# ---------------------------------------------------------------------------
def _resolve_athlete_uid(athlete_name: str | None, ctx: dict) -> tuple[int | None, str | None]:
    """Resolve 'athlete_name' (or default to caller) to a Telegram user_id.

    Returns (user_id, error_message). On success error_message is None.
    Aliases are read from `config.KNOWN_USERS` (populated from env var KNOWN_USERS).
    """
    if not athlete_name:
        return ctx.get("user_id"), None
    from config import KNOWN_USERS
    key = athlete_name.strip().lower().lstrip("@")
    uid = KNOWN_USERS.get(key)
    if uid is None:
        known = ", ".join(sorted(KNOWN_USERS.keys())) or "(none registered)"
        return None, f"Unknown athlete '{athlete_name}'. Known aliases: {known}."
    try:
        from services.strava_service import is_connected
    except ImportError:
        return None, "Strava service not available."
    if not is_connected(uid):
        return None, f"{athlete_name} has not connected Strava yet."
    return uid, None


async def _handler_get_strava_athlete(args: dict, ctx: dict) -> dict:
    target_uid, err = _resolve_athlete_uid(args.get("athlete_name"), ctx)
    if err:
        return {"error": err}
    try:
        from services.strava_service import is_connected, get_athlete, get_stats
    except ImportError:
        return {"error": "Strava service not installed."}
    if not is_connected(target_uid):
        return {"error": "Strava is not connected. Run /stravaconnect."}
    try:
        athlete = await get_athlete(user_id=target_uid)
        stats = await get_stats(athlete["id"], user_id=target_uid)
        return {"athlete": athlete, "stats": stats}
    except Exception as e:
        return {"error": str(e)}


async def _handler_get_strava_activities(args: dict, ctx: dict) -> dict:
    target_uid, err = _resolve_athlete_uid(args.get("athlete_name"), ctx)
    if err:
        return {"error": err}
    try:
        from services.strava_service import is_connected, get_activities
    except ImportError:
        return {"error": "Strava service not installed."}
    if not is_connected(target_uid):
        return {"error": "Strava is not connected. Run /stravaconnect."}
    count = min(max(1, int(args.get("count", 30))), 200)
    page = int(args.get("page", 1))
    try:
        activities = await get_activities(per_page=count, page=page, user_id=target_uid)
        slim = []
        for a in activities:
            dist_km = round(a.get("distance", 0) / 1000, 2)
            moving_s = a.get("moving_time", 0)
            speed = a.get("average_speed", 0)
            pace = round(1000 / speed / 60, 2) if speed else None
            slim.append({
                "id": a.get("id"),
                "name": a.get("name"),
                "type": a.get("sport_type") or a.get("type"),
                "date": a.get("start_date_local", "")[:10],
                "distance_km": dist_km,
                "moving_time_min": round(moving_s / 60, 1),
                "pace_min_per_km": pace,
                "avg_heartrate": a.get("average_heartrate"),
                "max_heartrate": a.get("max_heartrate"),
                "avg_watts": a.get("average_watts"),
                "weighted_avg_watts": a.get("weighted_average_watts"),
                "max_watts": a.get("max_watts"),
                "kilojoules": a.get("kilojoules"),
                "total_elevation_gain_m": a.get("total_elevation_gain"),
            })
        return {"activities": slim, "count": len(slim)}
    except Exception as e:
        return {"error": str(e)}


async def _handler_get_strava_activity(args: dict, ctx: dict) -> dict:
    target_uid, err = _resolve_athlete_uid(args.get("athlete_name"), ctx)
    if err:
        return {"error": err}
    activity_id = args.get("activity_id")
    if not activity_id:
        return {"error": "activity_id is required."}
    try:
        from services.strava_service import (
            is_connected, get_activity, get_activity_zones,
        )
    except ImportError:
        return {"error": "Strava service not installed."}
    if not is_connected(target_uid):
        return {"error": "Strava is not connected. Run /stravaconnect."}
    try:
        a = await get_activity(int(activity_id), user_id=target_uid)
    except Exception as e:
        return {"error": f"get_activity failed: {e}"}
    zones_summary = None
    try:
        zones_raw = await get_activity_zones(int(activity_id), user_id=target_uid)
        zones_summary = []
        for z in zones_raw or []:
            total = sum((b.get("time", 0) or 0) for b in z.get("distribution_buckets", []))
            buckets = []
            for i, b in enumerate(z.get("distribution_buckets", [])):
                t = b.get("time", 0) or 0
                buckets.append({
                    "zone": i + 1,
                    "min": b.get("min"),
                    "max": b.get("max"),
                    "seconds": t,
                    "percent": round(100 * t / total, 1) if total else 0,
                })
            zones_summary.append({"type": z.get("type"), "buckets": buckets})
    except Exception as e:
        zones_summary = {"error": str(e)}
    return {
        "id": a.get("id"),
        "name": a.get("name"),
        "type": a.get("sport_type") or a.get("type"),
        "date": (a.get("start_date_local") or "")[:19],
        "distance_km": round((a.get("distance") or 0) / 1000, 2),
        "moving_time_min": round((a.get("moving_time") or 0) / 60, 1),
        "avg_heartrate": a.get("average_heartrate"),
        "max_heartrate": a.get("max_heartrate"),
        "avg_watts": a.get("average_watts"),
        "weighted_avg_watts": a.get("weighted_average_watts"),
        "max_watts": a.get("max_watts"),
        "kilojoules": a.get("kilojoules"),
        "total_elevation_gain_m": a.get("total_elevation_gain"),
        "kudos_count": a.get("kudos_count"),
        "zones": zones_summary,
    }


async def _handler_get_strava_activity_kudos(args: dict, ctx: dict) -> dict:
    target_uid, err = _resolve_athlete_uid(args.get("athlete_name"), ctx)
    if err:
        return {"error": err}
    activity_id = args.get("activity_id")
    if not activity_id:
        return {"error": "activity_id is required."}
    try:
        from services.strava_service import (
            is_connected, get_activity_kudoers, get_activity_comments,
        )
    except ImportError:
        return {"error": "Strava service not installed."}
    if not is_connected(target_uid):
        return {"error": "Strava is not connected. Run /stravaconnect."}
    out: dict = {"activity_id": int(activity_id)}
    try:
        kudoers = await get_activity_kudoers(int(activity_id), user_id=target_uid)
        out["kudos_count"] = len(kudoers or [])
        out["kudoers"] = [
            {
                "name": (f"{k.get('firstname', '')} {k.get('lastname', '')}".strip()
                         or k.get("username") or str(k.get("id"))),
                "city": k.get("city"),
                "country": k.get("country"),
            }
            for k in kudoers or []
        ]
    except Exception as e:
        out["kudoers_error"] = str(e)
    try:
        comments = await get_activity_comments(int(activity_id), user_id=target_uid)
        out["comment_count"] = len(comments or [])
        out["comments"] = [
            {
                "athlete": (f"{(c.get('athlete') or {}).get('firstname', '')} "
                            f"{(c.get('athlete') or {}).get('lastname', '')}".strip()),
                "text": c.get("text"),
                "posted_at": c.get("created_at"),
            }
            for c in comments or []
        ]
    except Exception as e:
        out["comments_error"] = str(e)
    return out


# ---------------------------------------------------------------------------
# Dynamic per-user run_tool schema
# ---------------------------------------------------------------------------
def build_run_tool_schema(user_id: int = 0) -> dict | None:
    """Build a run_tool schema listing only tools this user can access."""
    from core.tool_factory import list_tools
    from core.tool_registry import _user_roles, _role_has_permission
    roles = _user_roles(user_id)
    approved = [
        a for a in list_tools()
        if a.status == "approved" and _role_has_permission(roles, a.permission)
    ]
    if not approved:
        return None

    def _fmt(a):
        kws = ", ".join(a.trigger_keywords) if a.trigger_keywords else ""
        kw_part = f" [keywords: {kws}]" if kws else ""
        return f"- {a.name}: {a.description}{kw_part}"

    tool_lines = "\n".join(_fmt(a) for a in approved)
    return {
        "type": "function",
        "function": {
            "name": "run_tool",
            "description": (
                "Run a saved bot tool to fulfill the user's request. Use this to get "
                "data from specialized tools the user has created via the Queen Bee "
                "factory (e.g. crypto prices, weather, scraping, etc.). "
                "You can call this multiple times and combine with other tools.\n\n"
                f"Available tools:\n{tool_lines}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": "The exact name of the tool to run.",
                        "enum": [a.name for a in approved],
                    },
                    "args": {
                        "type": "string",
                        "description": (
                            "Optional space-separated arguments for the tool "
                            "(e.g. search query, date range)."
                        ),
                    },
                },
                "required": ["tool_name"],
            },
        },
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
async def dispatch_tool(name: str, arguments: str, *, _user_context: dict | None = None) -> str:
    """Parse JSON arguments and dispatch to the registered tool handler."""
    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid tool arguments JSON"})

    logger.info("Tool call: %s args=%s", name, args)

    import core.tool_registry as tool_registry
    result = await tool_registry.dispatch(name, args, user_context=_user_context)
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# RBAC permission mapping
# ---------------------------------------------------------------------------
_BUILTIN_PERMISSIONS = {
    "list_tools":  "public",
    "fetch_url":    "public",
    "run_tool":    "public",
    "get_strava_athlete":         "public",
    "get_strava_activities":      "public",
    "get_strava_activity":        "public",
    "get_strava_activity_kudos":  "public",
}

_BUILTIN_HANDLERS = {
    "list_tools":  _handler_list_tools,
    "fetch_url":    _handler_fetch_url,
    "run_tool":    _handler_run_tool,
    "get_strava_athlete":         _handler_get_strava_athlete,
    "get_strava_activities":      _handler_get_strava_activities,
    "get_strava_activity":        _handler_get_strava_activity,
    "get_strava_activity_kudos":  _handler_get_strava_activity_kudos,
}


def register_builtin_tools() -> None:
    """Register the built-in tools into the unified registry. Called once at startup."""
    import core.tool_registry as tool_registry

    for schema in TOOL_SCHEMAS:
        name = schema["function"]["name"]
        handler = _BUILTIN_HANDLERS.get(name)
        if handler is None:
            continue
        tool_registry.register(
            name,
            schema=schema,
            handler=handler,
            permission=_BUILTIN_PERMISSIONS.get(name, "public"),
        )

    # run_tool has a placeholder schema; the real schema is built dynamically
    # per-user via build_run_tool_schema().
    tool_registry.register(
        "run_tool",
        schema={
            "type": "function",
            "function": {
                "name": "run_tool",
                "description": "Run a saved Queen Bee tool.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tool_name": {"type": "string"},
                        "args": {"type": "string"},
                    },
                    "required": ["tool_name"],
                },
            },
        },
        handler=_handler_run_tool,
        permission=_BUILTIN_PERMISSIONS["run_tool"],
    )


# Register on import so tool_registry is populated for every consumer.
register_builtin_tools()
