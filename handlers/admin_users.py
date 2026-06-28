"""
Admin commands for user role management.

All commands here require the caller to be the QB admin (QB_ADMIN_USER_ID).
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import QB_ADMIN_USER_ID, ROLE_PERMISSIONS
from core import user_roles as _user_roles_store
from .utils import format_table, safe_reply

logger = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return bool(QB_ADMIN_USER_ID) and user_id == QB_ADMIN_USER_ID


async def _deny(update: Update) -> None:
    await update.message.reply_text("⛔ Admin only.")


# ---------------------------------------------------------------------------
# /adduser <user_id> <role1,role2,...> [@username]
# ---------------------------------------------------------------------------

async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        await _deny(update); return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: `/adduser <user_id> <role1,role2,...> [@username]`\n\n"
            "Known roles: " + ", ".join(sorted(ROLE_PERMISSIONS.keys())) + "\n"
            "Example: `/adduser 1234567 finance,email @alice`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        uid = int(args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number"); return

    roles = [r.strip().lower() for r in args[1].split(",") if r.strip()]
    unknown = [r for r in roles if r not in ROLE_PERMISSIONS]
    if unknown:
        await update.message.reply_text(
            f"Unknown role(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(ROLE_PERMISSIONS.keys()))}"
        )
        return

    username = args[2] if len(args) >= 3 else ""
    record = _user_roles_store.set_roles(uid, roles, username=username)
    await update.message.reply_text(
        f"✅ User `{uid}` ({record['username'] or '—'}) set to roles: "
        f"{', '.join(record['roles'])}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------------------------------------------------------------------------
# /deluser <user_id>
# ---------------------------------------------------------------------------

async def deluser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        await _deny(update); return

    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: `/deluser <user_id>`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number"); return

    if uid == QB_ADMIN_USER_ID:
        await update.message.reply_text("⛔ Cannot remove the admin"); return

    removed = _user_roles_store.remove_user(uid)
    if removed:
        await update.message.reply_text(f"✅ Removed user `{uid}`",
                                        parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"User `{uid}` not in persistent store",
                                        parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# /listusers
# ---------------------------------------------------------------------------

async def listusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        await _deny(update); return

    from html import escape as _esc

    mapping = _user_roles_store.list_all()
    if not mapping:
        await safe_reply(
            update.message,
            "<i>No users in persistent store.</i>\n"
            "(Admin + env-var users still work via ALLOWED_USERS.)",
        )
        return

    rows = []
    for uid in sorted(mapping.keys()):
        info = mapping[uid]
        roles = ", ".join(info.get("roles", []))
        name = info.get("username") or "—"
        rows.append([str(uid), name, roles])
    table = format_table(rows, headers=["User ID", "Name", "Roles"])
    await safe_reply(
        update.message,
        f"<b>Persistent user roles:</b>\n<pre>{_esc(table)}</pre>",
    )


# ---------------------------------------------------------------------------
# /listroles
# ---------------------------------------------------------------------------

async def listroles_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        await _deny(update); return

    from html import escape as _esc

    rows = []
    for role in sorted(ROLE_PERMISSIONS.keys()):
        patterns = ", ".join(ROLE_PERMISSIONS[role])
        rows.append([role, patterns])
    table = format_table(rows, headers=["Role", "Permission patterns"])
    await safe_reply(
        update.message,
        f"<b>Available roles:</b>\n<pre>{_esc(table)}</pre>",
    )


# ---------------------------------------------------------------------------
# /setrole <tool_name> <role>
# ---------------------------------------------------------------------------

async def setrole_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        await _deny(update); return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: `/setrole <tool_name> <role>`\n"
            "Example: `/setrole my_tool admin`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    tool_name = args[0]
    role = args[1].strip().lower()
    if role not in ROLE_PERMISSIONS:
        await update.message.reply_text(
            f"Unknown role: {role}. Known: {', '.join(sorted(ROLE_PERMISSIONS.keys()))}"
        )
        return

    # Look up the tool and update its manifest.
    from core import tool_factory
    tools = tool_factory.list_tools()
    target = next((a for a in tools if a.name == tool_name), None)
    if not target:
        await update.message.reply_text(f"Tool not found: `{tool_name}`",
                                        parse_mode=ParseMode.MARKDOWN)
        return

    target.permission = role
    try:
        tool_factory.save_tool_meta(target)
    except Exception as e:
        await update.message.reply_text(f"Failed to save manifest: {e}")
        return

    await update.message.reply_text(
        f"✅ Tool `{tool_name}` now requires role: *{role}*",
        parse_mode=ParseMode.MARKDOWN,
    )
