"""
Task Notifier tool.

Schedule Telegram reminders for any task. A monitor loop (not included in
this repo by default) is expected to call ``due_tasks()`` periodically and
deliver each due notification to the creator's chat, then call
``mark_fired()`` to advance or remove the task.

Commands:
  notify add in 30m Take pills
  notify add in 2h Stand up and stretch
  notify add at 2026-04-29T14:30 Doctor appointment
  notify add every 1h Drink water
  notify add every 1mo Pay rent
  notify list
  notify enable <id>
  notify disable <id>
  notify delete <id>
  notify clear              # delete all of your own tasks
  notify                    # show help
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Tallinn")
except Exception:  # pragma: no cover - tzdata always present on supported pythons
    LOCAL_TZ = timezone(timedelta(hours=3))  # EEST fallback

STORE_FILE = Path(__file__).parent / "task_notifications.json"

_UNIT_SECONDS = {
    "s": 1,
    "sec": 1,
    "m": 60,
    "min": 60,
    "h": 3600,
    "hr": 3600,
    "d": 86400,
    "day": 86400,
}


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _load() -> dict:
    try:
        return json.loads(STORE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"tasks": [], "next_id": 1}


def _save(data: dict) -> None:
    STORE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_dt(dt: datetime) -> str:
    """Display in Europe/Tallinn local time with EET/EEST suffix."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(LOCAL_TZ)
    tzname = local.strftime("%Z") or "EET"
    return local.strftime("%Y-%m-%d %H:%M ") + tzname


def _parse_iso(s: str) -> datetime | None:
    """Parse stored UTC ISO timestamps. Always returns a UTC-aware datetime."""
    try:
        s = s.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _parse_user_dt(s: str) -> datetime | None:
    """Parse user-supplied datetime. Naive values are treated as Europe/Tallinn local."""
    try:
        raw = s.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _add_months(dt: datetime, months: int) -> datetime:
    """Return dt advanced by `months` calendar months in the local timezone.

    Preserves local hour/minute and day-of-month, clamping the day to the last
    day of the target month when needed (e.g. Jan 31 + 1 month -> Feb 28/29).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(LOCAL_TZ)
    y = local.year
    m = local.month + months
    # Normalize month overflow/underflow
    y += (m - 1) // 12
    m = ((m - 1) % 12) + 1
    # Clamp day to last day of target month
    import calendar as _cal
    last_day = _cal.monthrange(y, m)[1]
    day = min(local.day, last_day)
    new_local = local.replace(year=y, month=m, day=day)
    return new_local.astimezone(timezone.utc)


_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
_WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def _parse_hhmm(token: str) -> tuple[int, int] | None:
    m = _TIME_RE.match(token)
    if not m:
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    if 0 <= h < 24 and 0 <= mn < 60:
        return h, mn
    return None


def _parse_natural_at(tokens: list[str], now: datetime) -> tuple[datetime | None, int]:
    """
    Parse user-friendly 'at' arguments. Returns (datetime_utc, tokens_consumed).
    Supports:
      18:00                          → today at 18:00 (or tomorrow if past)
      today 18:00 | tomorrow 09:30
      mon 09:00 | monday 09:00       → next occurrence
      2026-04-29 14:30 | 2026-04-29T14:30
      2026-04-29                     → midnight that day
    Returns (None, 0) if nothing matches.
    """
    if not tokens:
        return None, 0

    local_now = now.astimezone(LOCAL_TZ)

    # 1) Bare HH:MM → today (or tomorrow if past)
    hhmm = _parse_hhmm(tokens[0])
    if hhmm is not None:
        h, mn = hhmm
        cand = local_now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if cand <= local_now:
            cand = cand + timedelta(days=1)
        return cand.astimezone(timezone.utc), 1

    head = tokens[0].lower()

    # 2) today / tomorrow [HH:MM]
    if head in ("today", "tomorrow", "tom", "tmrw"):
        delta = 0 if head == "today" else 1
        base = (local_now + timedelta(days=delta)).replace(second=0, microsecond=0)
        if len(tokens) >= 2 and (hhmm := _parse_hhmm(tokens[1])):
            h, mn = hhmm
            cand = base.replace(hour=h, minute=mn)
            consumed = 2
        else:
            cand = base.replace(hour=9, minute=0)  # default 09:00
            consumed = 1
        if cand <= local_now:
            return None, 0
        return cand.astimezone(timezone.utc), consumed

    # 3) weekday [HH:MM] → next occurrence
    if head in _WEEKDAYS:
        target_dow = _WEEKDAYS[head]
        days_ahead = (target_dow - local_now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7  # always future
        base = (local_now + timedelta(days=days_ahead)).replace(second=0, microsecond=0)
        if len(tokens) >= 2 and (hhmm := _parse_hhmm(tokens[1])):
            h, mn = hhmm
            cand = base.replace(hour=h, minute=mn)
            consumed = 2
        else:
            cand = base.replace(hour=9, minute=0)
            consumed = 1
        return cand.astimezone(timezone.utc), consumed

    # 4) "YYYY-MM-DD HH:MM" (two tokens)
    if len(tokens) >= 2:
        joined = f"{tokens[0]}T{tokens[1]}"
        dt = _parse_user_dt(joined)
        if dt is not None:
            return dt, 2

    # 5) Single ISO token "YYYY-MM-DDTHH:MM" or "YYYY-MM-DD"
    dt = _parse_user_dt(tokens[0])
    if dt is not None:
        return dt, 1

    return None, 0


# ---------------------------------------------------------------------------
# Schedule parsing
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([a-zA-Z]+)\s*$")


def _parse_duration(text: str) -> int | None:
    m = _DURATION_RE.match(text)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower().rstrip("s")
    if unit not in _UNIT_SECONDS:
        return None
    return n * _UNIT_SECONDS[unit]


def parse_schedule(tokens: list[str], now: datetime | None = None) -> tuple[dict, list[str]] | tuple[None, str]:
    """
    Parse leading schedule tokens. Returns (schedule_dict, remaining_tokens)
    or (None, error_message).

    schedule_dict has keys:
      - kind: "once" | "recurring"
      - next_fire_at: ISO UTC string
      - interval_seconds: int (for recurring)
    """
    now = now or _now()
    if not tokens:
        return None, "Missing schedule. Use: in <N><unit>, at <YYYY-MM-DDTHH:MM>, or every <N><unit>."

    head = tokens[0].lower()

    if head == "in":
        if len(tokens) < 2:
            return None, "Missing duration after `in`."
        # Accept either "in 30m" or "in 30 m" → join up to 2 tokens
        cand = tokens[1]
        rest_index = 2
        if not _DURATION_RE.match(cand) and len(tokens) >= 3:
            cand = f"{tokens[1]}{tokens[2]}"
            rest_index = 3
        secs = _parse_duration(cand)
        if secs is None or secs <= 0:
            return None, f"Invalid duration: `{cand}`. Examples: 30s, 15m, 2h, 1d."
        fire_at = now + timedelta(seconds=secs)
        return {
            "kind": "once",
            "next_fire_at": fire_at.isoformat(),
            "interval_seconds": 0,
        }, tokens[rest_index:]

    if head == "at":
        if len(tokens) < 2:
            return None, "Missing datetime after `at`. Examples: at 18:00, at tomorrow 09:30, at mon 14:00, at 2026-04-29 14:30"
        dt, consumed = _parse_natural_at(tokens[1:], now)
        if dt is None:
            return None, (
                "Could not parse datetime. Examples:\n"
                "• at 18:00 (today, or tomorrow if past)\n"
                "• at today 18:00 / at tomorrow 09:30\n"
                "• at mon 09:00 / at friday 17:00\n"
                "• at 2026-04-29 14:30"
            )
        if dt <= now:
            return None, f"Datetime {_fmt_dt(dt)} is in the past."
        return {
            "kind": "once",
            "next_fire_at": dt.isoformat(),
            "interval_seconds": 0,
        }, tokens[1 + consumed:]

    if head == "every":
        if len(tokens) < 2:
            return None, "Missing interval after `every`."
        cand = tokens[1]
        rest_index = 2
        if not _DURATION_RE.match(cand) and len(tokens) >= 3:
            cand = f"{tokens[1]}{tokens[2]}"
            rest_index = 3
        # Check for monthly units first (not in _UNIT_SECONDS since months vary)
        m = _DURATION_RE.match(cand)
        if m:
            n = int(m.group(1))
            unit = m.group(2).lower().rstrip("s")
            if unit in ("mo", "month"):
                if n < 1:
                    return None, "Monthly interval must be at least 1 month."
                fire_at = _add_months(now, n)
                return {
                    "kind": "monthly",
                    "next_fire_at": fire_at.isoformat(),
                    "interval_months": n,
                    "interval_seconds": 0,
                }, tokens[rest_index:]
        secs = _parse_duration(cand)
        if secs is None or secs < 60:
            return None, f"Invalid interval: `{cand}`. Minimum 1m. Examples: 5m, 1h, 1d, 1mo."
        fire_at = now + timedelta(seconds=secs)
        return {
            "kind": "recurring",
            "next_fire_at": fire_at.isoformat(),
            "interval_seconds": secs,
        }, tokens[rest_index:]

    return None, f"Unknown schedule prefix `{head}`. Use `in`, `at`, or `every`."


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def add_task(user_id: int, chat_id: int, schedule: dict, message: str,
             username: str | None = None, first_name: str | None = None) -> dict:
    data = _load()
    task = {
        "id": data.get("next_id", 1),
        "user_id": user_id,
        "chat_id": chat_id,
        "username": username,
        "first_name": first_name,
        "message": message,
        "kind": schedule["kind"],
        "interval_seconds": schedule["interval_seconds"],
        "next_fire_at": schedule["next_fire_at"],
        "enabled": True,
        "created_at": _now().isoformat(),
    }
    data.setdefault("tasks", []).append(task)
    data["next_id"] = task["id"] + 1
    _save(data)
    return task


def list_tasks(user_id: int, admin: bool = False) -> list[dict]:
    data = _load()
    tasks = data.get("tasks", [])
    if admin:
        return tasks
    return [t for t in tasks if t.get("user_id") == user_id]


def _find(data: dict, task_id: int, user_id: int, admin: bool) -> dict | None:
    for t in data.get("tasks", []):
        if t.get("id") == task_id and (admin or t.get("user_id") == user_id):
            return t
    return None


def set_enabled(task_id: int, user_id: int, enabled: bool, admin: bool = False) -> dict | None:
    data = _load()
    t = _find(data, task_id, user_id, admin)
    if t is None:
        return None
    t["enabled"] = enabled
    _save(data)
    return t


def delete_task(task_id: int, user_id: int, admin: bool = False) -> dict | None:
    data = _load()
    t = _find(data, task_id, user_id, admin)
    if t is None:
        return None
    data["tasks"] = [x for x in data["tasks"] if x is not t]
    _save(data)
    return t


def clear_user_tasks(user_id: int) -> int:
    data = _load()
    before = len(data.get("tasks", []))
    data["tasks"] = [t for t in data.get("tasks", []) if t.get("user_id") != user_id]
    _save(data)
    return before - len(data["tasks"])


def due_tasks(now: datetime | None = None) -> list[dict]:
    """Return enabled tasks whose next_fire_at is <= now."""
    now = now or _now()
    out = []
    for t in _load().get("tasks", []):
        if not t.get("enabled"):
            continue
        fire = _parse_iso(t.get("next_fire_at", ""))
        if fire is None:
            continue
        if fire <= now:
            out.append(t)
    return out


def mark_fired(task_id: int, now: datetime | None = None) -> dict | None:
    """After firing: reschedule recurring, delete one-shot. Returns updated/deleted task."""
    now = now or _now()
    data = _load()
    for i, t in enumerate(data.get("tasks", [])):
        if t.get("id") != task_id:
            continue
        if t.get("kind") == "recurring" and t.get("interval_seconds"):
            next_fire = now + timedelta(seconds=t["interval_seconds"])
            t["next_fire_at"] = next_fire.isoformat()
            _save(data)
            return t
        if t.get("kind") == "monthly":
            months = int(t.get("interval_months") or 1)
            # Advance from the original scheduled time so day-of-month is preserved
            # across firings (rather than drifting by `now`).
            base = _parse_iso(t.get("next_fire_at", "")) or now
            next_fire = _add_months(base, months)
            # If we missed a window, keep advancing until next_fire is in the future
            while next_fire <= now:
                next_fire = _add_months(next_fire, months)
            t["next_fire_at"] = next_fire.isoformat()
            _save(data)
            return t
        # one-shot → delete
        data["tasks"].pop(i)
        _save(data)
        return t
    return None


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _format_task(t: dict) -> str:
    state = "🟢" if t.get("enabled") else "⚪"
    fire = _parse_iso(t.get("next_fire_at", ""))
    when = _fmt_dt(fire) if fire else "?"
    if t.get("kind") == "recurring":
        secs = t.get("interval_seconds", 0)
        every = _pretty_secs(secs)
        sched = f"every {every} (next {when})"
    elif t.get("kind") == "monthly":
        months = int(t.get("interval_months") or 1)
        every = "month" if months == 1 else f"{months} months"
        sched = f"every {every} (next {when})"
    else:
        sched = f"at {when}"
    msg = t.get("message", "").strip() or "(no message)"
    return f"{state} #{t['id']} — {sched}\n   📝 {msg}"


def _pretty_secs(secs: int) -> str:
    if secs % 86400 == 0:
        return f"{secs // 86400}d"
    if secs % 3600 == 0:
        return f"{secs // 3600}h"
    if secs % 60 == 0:
        return f"{secs // 60}m"
    return f"{secs}s"


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

HELP = (
    "🔔 *Task Notifier*\n\n"
    "All times are in Estonia local time (EET / EEST).\n\n"
    "💬 *You can speak naturally:*\n"
    "• `notify remind me in 20 minutes to call mom`\n"
    "• `notify every weekday at 9am stand-up meeting`\n"
    "• `notify drop the third reminder`\n"
    "• `notify pause everything`\n\n"
    "Or use structured form:\n"
    "• `notify add in 30m Take pills`\n"
    "• `notify add at 18:00 Call mom`\n"
    "• `notify add at tomorrow 09:30 Standup`\n"
    "• `notify add at fri 17:00 Weekend!`\n"
    "• `notify add every 1h Drink water`\n"
    "• `notify add every 1mo Pay rent`\n\n"
    "Manage:\n"
    "• `notify list` — show your tasks\n"
    "• `notify enable <id>` — re-activate a task\n"
    "• `notify disable <id>` — pause without deleting\n"
    "• `notify delete <id>` — remove a task\n"
    "• `notify clear` — delete all of your tasks"
)


# ---------------------------------------------------------------------------
# LLM-powered natural language understanding
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """You are the natural language router for a Telegram task notifier tool.
Convert the user's free-text request into a SINGLE JSON object describing the action to take.

Output JSON schema:
{{
  "action": "add" | "list" | "enable" | "disable" | "delete" | "clear" | "help" | "unknown",
  "kind": "once" | "recurring" | "monthly",
  "fire_at_local": "YYYY-MM-DDTHH:MM",
  "interval_seconds": <integer>,
  "interval_months": <integer>,
  "message": "<reminder text>",
  "id": <integer>,
  "error": "<short reason>"
}}

Field rules:
- action="add" requires "message" plus one of:
    ("kind"="once"      and "fire_at_local")
    ("kind"="recurring" and "interval_seconds">=60)
    ("kind"="monthly"   and "interval_months">=1; "fire_at_local" optional, picks first fire date)
- action="enable"/"disable"/"delete" requires "id".
- action="unknown" requires "error".

Time rules:
- "Now" is {now_local} in {tz_name}. All times are Estonia local time unless stated otherwise.
- For relative times like "in 20 minutes", "in 2 hours", "tonight", "tomorrow morning", compute the absolute fire_at_local in Estonia local time.
- Time-of-day defaults: "morning"=09:00, "noon"=12:00, "afternoon"=15:00, "evening"=19:00, "tonight"=21:00.
- Recurring: "every hour"->3600, "every day"->86400, "every 15 minutes"->900.
  For "every weekday at 9am" use kind="recurring", interval_seconds=86400, AND set fire_at_local to the next 09:00 weekday occurrence.
- Monthly: for "every month", "monthly", "every 3 months", "on the 1st of each month" use kind="monthly" with interval_months (default 1). NEVER convert months to interval_seconds — use kind="monthly".
  - If the user specifies a day-of-month and/or time-of-day (e.g. "on the 5th at 09:00", "every 3 months at 10am"), set fire_at_local to the FIRST such occurrence in the future. The day-of-month and time will then be preserved on every recurrence.
  - If NO day-of-month and NO time-of-day are given (e.g. plain "every 3 months <message>"), OMIT fire_at_local entirely so the first fire is scheduled exactly N months from now.

Action mapping:
- "list", "show me my reminders", "what reminders do I have" -> action="list".
- Ordinals like "third one" -> id=3; "task 5" -> id=5.
- "delete all" / "remove everything" / "pause everything" -> action="clear".
- Ambiguous or unrelated -> action="unknown" with a short error.

Return ONLY the JSON object, no commentary, no markdown."""


async def _llm_route(text: str) -> dict | None:
    """Call the LLM to convert natural language into a structured action dict."""
    try:
        from services.llm_service import client
        from config import LLM_MODEL
    except Exception:
        return None

    now_local = datetime.now(LOCAL_TZ)
    tz_name = now_local.strftime("%Z") or "EET"
    # Avoid str.format because the prompt contains JSON braces.
    system = (
        _LLM_SYSTEM_PROMPT
        .replace("{{", "\x00LB\x00").replace("}}", "\x00RB\x00")
        .replace("{now_local}", now_local.strftime("%Y-%m-%d %H:%M (%A)"))
        .replace("{tz_name}", tz_name)
        .replace("\x00LB\x00", "{").replace("\x00RB\x00", "}")
    )

    try:
        kwargs = dict(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
        )
        try:
            resp = await client.chat.completions.create(**kwargs, temperature=0)
        except Exception as exc_t:
            if "temperature" in str(exc_t).lower():
                resp = await client.chat.completions.create(**kwargs)
            else:
                raise
        content = resp.choices[0].message.content or "{}"
        return json.loads(content)
    except Exception as exc:
        return {"action": "unknown", "error": f"LLM call failed: {exc}"}


def _apply_action(action: dict, user_id: int, chat_id: int, is_admin: bool) -> str:
    """Apply a structured action dict (from LLM or any normalized source)."""
    act = (action.get("action") or "").lower()

    if act == "help" or act == "":
        return HELP

    if act == "add":
        message = (action.get("message") or "").strip()
        if not message:
            return "❌ I didn't catch what you want to be reminded about. Try again with the reminder text."
        kind = (action.get("kind") or "once").lower()
        if kind == "monthly":
            months = int(action.get("interval_months") or 1)
            if months < 1:
                return "❌ Monthly interval must be at least 1 month."
            fire_at_local = action.get("fire_at_local")
            if fire_at_local:
                dt = _parse_user_dt(fire_at_local)
                if dt is None:
                    return f"❌ Invalid datetime: `{fire_at_local}`"
                if dt <= _now():
                    dt = _add_months(dt, months)
            else:
                dt = _add_months(_now(), months)
            schedule = {
                "kind": "monthly",
                "next_fire_at": dt.isoformat(),
                "interval_months": months,
                "interval_seconds": 0,
            }
        elif kind == "recurring":
            secs = int(action.get("interval_seconds") or 0)
            if secs < 60:
                return "❌ Recurring interval must be at least 60 seconds."
            # Optional first fire_at; if absent, schedule first run after one interval
            fire_at_local = action.get("fire_at_local")
            if fire_at_local:
                dt = _parse_user_dt(fire_at_local)
                if dt is None or dt <= _now():
                    dt = _now() + timedelta(seconds=secs)
            else:
                dt = _now() + timedelta(seconds=secs)
            schedule = {"kind": "recurring", "next_fire_at": dt.isoformat(), "interval_seconds": secs}
        else:
            fire_at_local = action.get("fire_at_local")
            if not fire_at_local:
                return "❌ I couldn't determine when to fire. Please include a time."
            dt = _parse_user_dt(fire_at_local)
            if dt is None:
                return f"❌ Invalid datetime: `{fire_at_local}`"
            if dt <= _now():
                return f"❌ Datetime {_fmt_dt(dt)} is in the past."
            schedule = {"kind": "once", "next_fire_at": dt.isoformat(), "interval_seconds": 0}
        if not user_id:
            return "❌ Cannot determine your user id."
        username = action.get("username") or None
        first_name = action.get("first_name") or None
        task = add_task(user_id, chat_id, schedule, message, username=username, first_name=first_name)
        return f"✅ Notification scheduled\n\n{_format_task(task)}"

    if act == "list":
        tasks = list_tasks(user_id, admin=False)
        if not tasks:
            return "📭 You have no scheduled notifications."
        tasks.sort(key=lambda t: (not t.get("enabled"), t.get("next_fire_at", "")))
        lines = [f"📋 *Your notifications* ({len(tasks)})", ""]
        for t in tasks:
            lines.append(_format_task(t))
        return "\n".join(lines)

    if act in ("enable", "disable", "delete"):
        try:
            tid = int(action.get("id"))
        except (TypeError, ValueError):
            return f"❌ I couldn't determine which task. Try `notify list` first."
        if act == "enable":
            t = set_enabled(tid, user_id, True, admin=is_admin)
            return f"🟢 Task #{tid} enabled.\n\n{_format_task(t)}" if t else f"❌ No task #{tid} found (or not yours)."
        if act == "disable":
            t = set_enabled(tid, user_id, False, admin=is_admin)
            return f"⚪ Task #{tid} disabled.\n\n{_format_task(t)}" if t else f"❌ No task #{tid} found (or not yours)."
        t = delete_task(tid, user_id, admin=is_admin)
        return f"🗑️ Task #{tid} deleted." if t else f"❌ No task #{tid} found (or not yours)."

    if act == "clear":
        n = clear_user_tasks(user_id)
        return f"🗑️ Cleared {n} task(s)."

    err = action.get("error") or "I couldn't understand that request."
    return f"🤔 {err}\n\n{HELP}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_KNOWN_CMDS = {
    "help", "?", "add", "set", "create", "new",
    "list", "ls", "show", "listall", "all",
    "enable", "activate", "on", "resume",
    "disable", "deactivate", "off", "pause",
    "delete", "del", "rm", "remove",
    "clear", "purge", "wipe",
}


async def run(context: dict[str, Any]) -> str:
    args: list[str] = list(context.get("args") or [])
    user_id = int(context.get("user_id") or 0)
    chat_id = int(context.get("chat_id") or user_id)

    try:
        admin_id = int(os.getenv("QB_ADMIN_USER_ID", "0") or 0)
        if not admin_id:
            from config import QB_ADMIN_USER_ID as _kvid
            admin_id = int(_kvid or 0)
        is_admin = bool(admin_id) and user_id == admin_id
    except Exception:
        is_admin = False

    if not args:
        return HELP

    cmd = args[0].lower()
    rest = args[1:]

    # Natural-language fallback: if first word isn't a known sub-command,
    # send the entire input to the LLM router.
    username = context.get("username")
    first_name = context.get("first_name")

    if cmd not in _KNOWN_CMDS:
        text = " ".join(args).strip()
        action = await _llm_route(text)
        if action is None:
            return (
                "🤔 I didn't understand and the LLM is unavailable.\n\n" + HELP
            )
        action["username"] = username
        action["first_name"] = first_name
        return _apply_action(action, user_id, chat_id, is_admin)

    if cmd in ("help", "?"):
        return HELP

    if cmd in ("add", "set", "create", "new"):
        sched_result = parse_schedule(rest)
        if sched_result[0] is None:
            # Structured parse failed → try LLM with the full natural request
            text = " ".join(args).strip()
            action = await _llm_route(text)
            if action is None:
                return f"❌ {sched_result[1]}\n\n{HELP}"
            action["username"] = username
            action["first_name"] = first_name
            return _apply_action(action, user_id, chat_id, is_admin)
        schedule, remaining = sched_result
        message = " ".join(remaining).strip()
        if not message:
            return "❌ Missing message text.\nExample: `notify add in 30m Take pills`"
        if not user_id:
            return "❌ Cannot determine your user id."
        task = add_task(user_id, chat_id, schedule, message, username=username, first_name=first_name)
        return f"✅ Notification scheduled\n\n{_format_task(task)}"

    if cmd in ("list", "ls", "show"):
        tasks = list_tasks(user_id, admin=False)
        if not tasks:
            return "📭 You have no scheduled notifications."
        # sort: enabled first, then by next_fire_at
        tasks.sort(key=lambda t: (not t.get("enabled"), t.get("next_fire_at", "")))
        lines = [f"📋 *Your notifications* ({len(tasks)})", ""]
        for t in tasks:
            lines.append(_format_task(t))
        return "\n".join(lines)

    if cmd in ("listall", "all") and is_admin:
        tasks = list_tasks(user_id, admin=True)
        if not tasks:
            return "📭 No scheduled notifications across all users."
        lines = [f"📋 *All notifications* ({len(tasks)})", ""]
        for t in tasks:
            who = t.get("username") and f"@{t['username']}" or t.get("first_name") or f"id={t.get('user_id')}"
            lines.append(f"{_format_task(t)}\n   👤 {who}")
        return "\n".join(lines)

    if cmd in ("enable", "activate", "on", "resume"):
        if not rest:
            return "❌ Usage: `notify enable <id>`"
        try:
            tid = int(rest[0])
        except ValueError:
            return f"❌ Invalid id: `{rest[0]}`"
        t = set_enabled(tid, user_id, True, admin=is_admin)
        if t is None:
            return f"❌ No task #{tid} found (or not yours)."
        return f"🟢 Task #{tid} enabled.\n\n{_format_task(t)}"

    if cmd in ("disable", "deactivate", "off", "pause"):
        if not rest:
            return "❌ Usage: `notify disable <id>`"
        try:
            tid = int(rest[0])
        except ValueError:
            return f"❌ Invalid id: `{rest[0]}`"
        t = set_enabled(tid, user_id, False, admin=is_admin)
        if t is None:
            return f"❌ No task #{tid} found (or not yours)."
        return f"⚪ Task #{tid} disabled.\n\n{_format_task(t)}"

    if cmd in ("delete", "del", "rm", "remove"):
        if not rest:
            return "❌ Usage: `notify delete <id>`"
        try:
            tid = int(rest[0])
        except ValueError:
            return f"❌ Invalid id: `{rest[0]}`"
        t = delete_task(tid, user_id, admin=is_admin)
        if t is None:
            return f"❌ No task #{tid} found (or not yours)."
        return f"🗑️ Task #{tid} deleted."

    if cmd in ("clear", "purge", "wipe"):
        n = clear_user_tasks(user_id)
        return f"🗑️ Cleared {n} task(s)."

    return f"❌ Unknown command: `{cmd}`\n\n{HELP}"
