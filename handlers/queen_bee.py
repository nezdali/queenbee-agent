"""
Queen Bee tool handlers: create, save, run, edit, delete, list, help.
"""

import asyncio
import json as _json
import logging
import os

from pathlib import Path as _Path
from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import QB_ADMIN_USER_ID
from services.llm_service import get_llm_response

from .utils import (
    _QB_ADMIN_USER_ID as _ADMIN_ID,
    _check_qb_rate, _record_qb_usage,
    _send_collapsed, _send_tool_result, _track_tool_message,
    conversation_manager, logger,
)
from .tool_dispatch import _translate_query


def _build_toolhelp_detail(a) -> str:
    """Build the detail help text for a single tool."""
    example = a.help_example or (f"{a.trigger_keywords[0]} ..." if a.trigger_keywords else "N/A")
    if "\n" in example:
        example_lines = "\n".join(f"  `{line}`" for line in example.split("\n") if line.strip())
        example_block = f"💡 Usage:\n{example_lines}"
    else:
        example_block = f"💡 Example: `{example}`"
    return (
        f"🤖 *{a.name}*\n\n"
        f"📋 {a.description}\n\n"
        f"🏷️ Keywords: {', '.join(a.trigger_keywords)}\n"
        f"{example_block}"
    )


async def _handle_qb_request(update: Update, context: ContextTypes.DEFAULT_TYPE, description: str) -> None:
    """Generate a new tool, test-run it, show result, and ask user to accept or discard."""
    from core.tool_factory import generate_tool, check_qb_request_intent, test_tool_code

    msg = update.message
    if not msg:
        return

    user_id = update.effective_user.id if update.effective_user else 0

    intent_error = check_qb_request_intent(description, user_id)
    if intent_error:
        await msg.reply_text(intent_error)
        return

    rate_error = _check_qb_rate(user_id)
    if rate_error:
        await msg.reply_text(rate_error)
        return

    await msg.chat.send_action("typing")
    status_msg = await msg.reply_text(
        f"🐝 Queen Bee is generating a tool...\n\n{description}",
    )

    try:
        meta, result = await generate_tool(description, user_id=user_id)
    except Exception as e:
        logger.exception("generate_tool crashed")
        await msg.reply_text(f"❌ Tool generation crashed: {type(e).__name__}: {e}")
        return
    if meta is None:
        await msg.reply_text(f"❌ Failed to generate tool:\n{result}")
        return

    _record_qb_usage(user_id)

    # -- Test-run the generated code without saving --
    try:
        await status_msg.edit_text(
            f"🐝 Tool `{meta.name}` generated. Testing...",
            parse_mode="Markdown",
        )
    except Exception:
        pass
    await msg.chat.send_action("typing")

    import os
    test_context = {
        "user_id": user_id,
        "chat_id": msg.chat_id,
        "args": [],
        "google_auth": os.path.exists(os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")),
    }
    test_result = await test_tool_code(result, test_context, timeout_sec=30)
    test_ok = not test_result.startswith("❌")

    # -- Store pending tool in user_data --
    context.user_data["pending_tool"] = {
        "name": meta.name,
        "description": meta.description,
        "requires_google_auth": meta.requires_google_auth,
        "trigger_keywords": meta.trigger_keywords,
        "created_at": meta.created_at,
        "version": meta.version,
        "code": result,
    }

    # -- Build the preview message --
    auth_icon = "🔑" if meta.requires_google_auth else "🔓"
    keywords = ", ".join(meta.trigger_keywords) or "—"
    safe_desc = meta.description.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")

    test_icon = "✅" if test_ok else "⚠️"
    # Truncate test result for display
    test_display = test_result[:600] + ("…" if len(test_result) > 600 else "")
    # Escape markdown special chars in test output
    for ch in ("_", "*", "`", "["):
        test_display = test_display.replace(ch, f"\\{ch}")

    first_kw = meta.trigger_keywords[0] if meta.trigger_keywords else meta.name
    text = (
        f"🤖 *Tool generated:* `{meta.name}`\n"
        f"📝 {safe_desc}\n"
        f"{auth_icon} Google auth required: {'yes' if meta.requires_google_auth else 'no'}\n"
        f"🏷️ Keywords: {keywords}\n\n"
        f"{test_icon} *Initial test (no args):*\n{test_display}\n\n"
        f"🧪 *Now test it before saving:*\n"
        f"`/qbtest {first_kw} <args>`\n"
        f"e.g. `/qbtest {first_kw}` or `/qbtest <your input>`\n\n"
        f"Found a bug? `/qbfix <what's wrong>` — Queen Bee will rewrite it.\n"
        f"Inspect a URL: `/qbdebug <url>` (HTTP status, table preview).\n"
        f"Happy with it? `/qbsave`   |   Throw away: `/qbdiscard`"
    )

    await msg.reply_text(text, parse_mode="Markdown")


async def qbtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the currently-pending Queen Bee tool against ad-hoc args without saving."""
    from core.tool_factory import test_tool_code

    msg = update.message
    if not msg:
        return

    pending = context.user_data.get("pending_tool")
    if not pending:
        await msg.reply_text(
            "⚠️ No pending tool. Generate one first with `>> <description>`.",
            parse_mode="Markdown",
        )
        return

    raw_args: list[str] = list(context.args or [])
    keywords_lower = {kw.lower() for kw in pending.get("trigger_keywords") or []}
    trigger_word = ""
    if raw_args and raw_args[0].lower() in keywords_lower:
        trigger_word = raw_args[0].lower()
        tool_args = raw_args[1:]
    else:
        trigger_word = next(iter(keywords_lower), "")
        tool_args = raw_args

    raw_extra = " ".join(tool_args)
    _eu = update.effective_user
    user_id = _eu.id if _eu else 0
    test_ctx = {
        "user_id": user_id,
        "chat_id": msg.chat_id,
        "username": (_eu.username if _eu else None),
        "first_name": (_eu.first_name if _eu else None),
        "args": tool_args,
        "raw_extra": raw_extra,
        "trigger_word": trigger_word,
        "google_auth": os.path.exists(os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")),
    }

    await msg.chat.send_action("typing")
    result = await test_tool_code(pending["code"], test_ctx, timeout_sec=60)

    name = pending.get("name", "tool")
    header = f"🧪 *Test of* `{name}`"
    if trigger_word or raw_extra:
        invoked = " ".join(x for x in [trigger_word, raw_extra] if x)
        header += f" — `{invoked}`"
    if not result or not result.strip():
        body = "_(tool returned empty string)_"
    elif len(result) >= 3800:
        body = result[:3800] + "…"
    else:
        body = result
    footer = "\n\n`/qbfix <bug>`   |   `/qbdebug <url>`   |   `/qbsave`   |   `/qbdiscard`"
    await msg.reply_text(f"{header}\n\n{body}{footer}", parse_mode="Markdown")


async def qbsave_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save the currently-pending Queen Bee tool (command form of the Save button)."""
    from core.tool_factory import ToolMeta, save_tool, review_tool_security

    msg = update.message
    if not msg:
        return

    pending = context.user_data.get("pending_tool")
    if not pending:
        await msg.reply_text(
            "⚠️ No pending tool. Generate one first with `>> <description>`.",
            parse_mode="Markdown",
        )
        return

    pending_copy = dict(pending)
    code = pending_copy.pop("code")
    meta = ToolMeta(**pending_copy)
    user_id = update.effective_user.id if update.effective_user else 0

    try:
        save_tool(meta, code)
        context.user_data.pop("pending_tool", None)
    except Exception as e:
        await msg.reply_text(f"❌ Failed to save tool: {e}")
        return

    if not (QB_ADMIN_USER_ID and user_id == QB_ADMIN_USER_ID):
        asyncio.create_task(
            review_tool_security(meta.name, code, user_id, msg.get_bot())
        )
        await msg.reply_text(
            f"✅ Tool `{meta.name}` saved!\n"
            f"🔍 Running security review... You'll be notified when it's approved.",
            parse_mode="Markdown",
        )
        return

    await msg.reply_text(
        f"✅ Tool `{meta.name}` saved and ready!\n"
        f"Run it with: `/runtool {meta.name}`\n"
        f"Or use a trigger keyword: {', '.join(meta.trigger_keywords) or 'N/A'}",
        parse_mode="Markdown",
    )


async def qbdiscard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Discard the currently-pending Queen Bee tool (command form of the Discard button)."""
    msg = update.message
    if not msg:
        return
    if context.user_data.pop("pending_tool", None):
        await msg.reply_text("❌ Pending tool discarded.")
    else:
        await msg.reply_text("⚠️ No pending tool to discard.")


async def qbdebug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch a URL and dump diagnostic info (status, size, table preview).

    Used to investigate why a Queen Bee scrape tool is failing.
    Usage: /qbdebug <url>
    """
    msg = update.message
    if not msg:
        return

    raw = " ".join(context.args or []).strip()
    if not raw or not raw.lower().startswith(("http://", "https://")):
        await msg.reply_text(
            "Usage: `/qbdebug <https://...>`\n\n"
            "Fetches the URL and shows HTTP status, byte length, table count and "
            "the first rows of each table so you know what the scraper actually sees.",
            parse_mode="Markdown",
        )
        return

    url = raw.split()[0]
    await msg.chat.send_action("typing")
    status_msg = await msg.reply_text(f"🔎 Fetching {url} ...")

    try:
        from tool_utils import fetch_text
    except Exception as e:
        await status_msg.edit_text(f"❌ tool_utils import failed: {e}")
        return

    try:
        text, status = await fetch_text(url, timeout=20)
    except Exception as e:
        await status_msg.edit_text(f"❌ fetch_text crashed: {type(e).__name__}: {e}")
        return

    text = text or ""
    lines: list[str] = [
        f"🔎 *qbdebug* `{url}`",
        f"HTTP `{status or 0}`  •  length `{len(text)}` bytes",
    ]

    if text:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(text, "html.parser")
        except Exception as e:
            soup = None
            lines.append(f"BeautifulSoup parse failed: {e}")

        if soup is not None:
            tables = soup.find_all("table")
            lines.append(f"`<table>` tags found: *{len(tables)}*")
            for ti, table in enumerate(tables[:2]):
                rows = table.find_all("tr")
                lines.append(f"\n*table\\[{ti}\\]* rows=`{len(rows)}`")
                for ri, row in enumerate(rows[:5]):
                    cells = row.find_all(["td", "th"])
                    summary = ", ".join(
                        f"{c.name}={c.get_text(' ', strip=True)[:30]!r}"
                        for c in cells[:6]
                    )
                    if len(cells) > 6:
                        summary += f", ... (+{len(cells)-6} more)"
                    lines.append(f"  row\\[{ri}\\]: {summary}")
            if not tables:
                snippet = text.strip()[:600]
                snippet_safe = snippet.replace("`", "\\`")
                lines.append("\nFirst 600 chars:\n```\n" + snippet_safe + "\n```")

    out = "\n".join(lines)
    if len(out) > 3800:
        out = out[:3800] + "\n…(truncated)"
    try:
        await status_msg.edit_text(out, parse_mode="Markdown")
    except Exception:
        # Markdown can choke on bracket escapes; fall back to plain text.
        await status_msg.edit_text(out.replace("\\[", "[").replace("\\]", "]"))


async def qbfix_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask Queen Bee to fix the currently-pending tool based on a bug report."""
    from core.tool_factory import (
        fix_pending_code, test_tool_code, gather_fix_context,
        _format_fix_context, _looks_like_failure,
    )

    msg = update.message
    if not msg:
        return

    pending = context.user_data.get("pending_tool")
    if not pending:
        await msg.reply_text(
            "⚠️ No pending tool. Generate one first with `>> <description>`.",
            parse_mode="Markdown",
        )
        return

    instruction = " ".join(context.args or []).strip()
    if not instruction:
        await msg.reply_text(
            "Usage: `/qbfix <what's broken / what to change>`",
            parse_mode="Markdown",
        )
        return

    user_id = update.effective_user.id if update.effective_user else 0
    name = pending.get("name", "tool")
    await msg.chat.send_action("typing")
    status = await msg.reply_text(
        f"🛠️ Gathering ground truth for `{name}`...", parse_mode="Markdown"
    )

    test_ctx = {
        "user_id": user_id,
        "chat_id": msg.chat_id,
        "args": [],
        "raw_extra": "",
        "trigger_word": ((pending.get("trigger_keywords") or [""])[0]),
        "google_auth": os.path.exists(os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")),
    }

    truth = await gather_fix_context(pending["code"], instruction, test_ctx)
    extra_context = _format_fix_context(truth)

    try:
        await status.edit_text(
            f"🛠️ Asking Queen Bee to fix `{name}`...", parse_mode="Markdown"
        )
    except Exception:
        pass

    new_code, new_keywords, new_desc = await fix_pending_code(
        name=name,
        description=pending.get("description", ""),
        trigger_keywords=pending.get("trigger_keywords") or [],
        current_code=pending["code"],
        instruction=instruction,
        user_id=user_id,
        extra_context=extra_context,
    )
    if new_code is None:
        await status.edit_text(f"❌ Fix failed: {new_desc}")
        return

    test_result = await test_tool_code(new_code, dict(test_ctx), timeout_sec=30)
    attempts = 1

    # Auto-retry once if the result still looks like a failure
    if _looks_like_failure(test_result):
        try:
            await status.edit_text(
                f"🛠️ First attempt still failing, auto-retrying with new observation...",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        retry_truth = await gather_fix_context(new_code, instruction, test_ctx)
        retry_extra = _format_fix_context(retry_truth)
        retry_instruction = (
            f"{instruction}\n\nPREVIOUS FIX ATTEMPT STILL FAILED. "
            f"The tool now returns the OBSERVED OUTPUT below. "
            f"Look at the OBSERVED PAGE / TABLE PREVIEW carefully and fix the actual root cause."
        )
        new_code2, new_keywords2, new_desc2 = await fix_pending_code(
            name=name,
            description=new_desc,
            trigger_keywords=new_keywords,
            current_code=new_code,
            instruction=retry_instruction,
            user_id=user_id,
            extra_context=retry_extra,
        )
        if new_code2 is not None:
            new_code, new_keywords, new_desc = new_code2, new_keywords2, new_desc2
            test_result = await test_tool_code(new_code, dict(test_ctx), timeout_sec=30)
            attempts = 2

    pending["code"] = new_code
    pending["trigger_keywords"] = new_keywords
    pending["description"] = new_desc
    pending["version"] = int(pending.get("version", 1)) + 1
    context.user_data["pending_tool"] = pending

    test_ok = not _looks_like_failure(test_result)
    test_icon = "✅" if test_ok else "⚠️"
    test_display = test_result[:600] + ("…" if len(test_result) > 600 else "")
    for ch in ("_", "*", "`", "["):
        test_display = test_display.replace(ch, f"\\{ch}")

    first_kw = new_keywords[0] if new_keywords else name
    attempts_note = f" (after {attempts} attempts)" if attempts > 1 else ""
    await status.edit_text(
        f"🛠️ `{name}` updated (v{pending['version']}){attempts_note}.\n"
        f"🏷️ Keywords: {', '.join(new_keywords) or '—'}\n\n"
        f"{test_icon} *Re-test (no args):*\n{test_display}\n\n"
        f"Try again: `/qbtest {first_kw} <args>`\n"
        f"Still broken? `/qbfix <more details>`\n"
        f"Happy? `/qbsave`  or  `/qbdiscard`",
        parse_mode="Markdown",
    )


async def qb_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Queen Bee save/discard confirmation buttons."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""

    if data == "qb_discard":
        context.user_data.pop("pending_tool", None)
        await query.edit_message_text("❌ Tool discarded.")
        return

    if data.startswith("qb_save:"):
        pending = context.user_data.get("pending_tool")
        if not pending:
            await query.edit_message_text("⚠️ No pending tool found. Please create one again.")
            return

        from core.tool_factory import ToolMeta, save_tool, review_tool_security

        code = pending.pop("code")
        meta = ToolMeta(**pending)
        user_id = query.from_user.id if query.from_user else 0

        try:
            save_tool(meta, code)
            context.user_data.pop("pending_tool", None)
        except Exception as e:
            await query.edit_message_text(f"❌ Failed to save tool: {e}")
            return

        if not (QB_ADMIN_USER_ID and user_id == QB_ADMIN_USER_ID):
            asyncio.create_task(
                review_tool_security(meta.name, code, user_id, query.get_bot())
            )
            await query.edit_message_text(
                f"✅ Tool `{meta.name}` saved!\n"
                f"🔍 Running security review... You'll be notified when it's approved.",
                parse_mode="Markdown",
            )
            return

        await query.edit_message_text(
            f"✅ Tool `{meta.name}` saved and ready!\n"
            f"Run it with: `/runtool {meta.name}`\n"
            f"Or use a trigger keyword: {', '.join(meta.trigger_keywords) or 'N/A'}",
            parse_mode="Markdown",
        )

    if data.startswith("qb_approve:") or data.startswith("qb_reject:"):
        reviewer_id = query.from_user.id if query.from_user else 0
        if not (QB_ADMIN_USER_ID and reviewer_id == QB_ADMIN_USER_ID):
            await query.answer("Only the admin can approve or reject tools.", show_alert=True)
            return

        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.edit_message_text("⚠️ Malformed callback data.")
            return
        action, tool_name, created_by_str = parts
        created_by = int(created_by_str) if created_by_str.isdigit() else 0

        manifest_path = _Path(__file__).parent.parent / "tools" / f"{tool_name}.json"

        if not manifest_path.exists():
            await query.edit_message_text(f"⚠️ Tool `{tool_name}` no longer exists.")
            return

        if action == "qb_approve":
            data_json = _json.loads(manifest_path.read_text(encoding="utf-8"))
            data_json["status"] = "approved"
            manifest_path.write_text(_json.dumps(data_json, indent=2, ensure_ascii=False), encoding="utf-8")
            await query.edit_message_text(f"✅ Tool `{tool_name}` approved.")
            if created_by:
                try:
                    await query.get_bot().send_message(
                        chat_id=created_by,
                        text=f"✅ Your tool `{tool_name}` was approved by admin and is ready.\nRun it with: `/runtool {tool_name}`",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
        else:  # qb_reject
            data_json = _json.loads(manifest_path.read_text(encoding="utf-8"))
            data_json["status"] = "rejected"
            manifest_path.write_text(_json.dumps(data_json, indent=2, ensure_ascii=False), encoding="utf-8")
            await query.edit_message_text(f"🚫 Tool `{tool_name}` rejected.")
            if created_by:
                try:
                    await query.get_bot().send_message(
                        chat_id=created_by,
                        text=f"🚫 Your tool `{tool_name}` was rejected by admin for security reasons.",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass


async def tools_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /tools — list all saved tools."""
    if not update.message:
        return

    from core.tool_factory import list_tools

    tools = list_tools()
    if not tools:
        await update.message.reply_text(
            "No saved tools yet.\n\nCreate one:\n`>> describe what the tool should do`",
            parse_mode="Markdown",
        )
        return

    lines = ["🤖 *Saved tools:*\n"]
    for a in tools:
        icon = "🔑" if a.requires_google_auth else "🤖"
        lines.append(f"{icon} `{a.name}` — {a.description}")
    lines.append("\nRun: `/runtool <name>`\nDelete: `/deltool <name>`\n\nTap a tool below for details + a copy-name button.")

    buttons = [
        InlineKeyboardButton(a.name, callback_data=f"toolhelp:{a.name}")
        for a in tools
    ]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    keyboard = InlineKeyboardMarkup(rows)

    # Telegram caps messages at 4096 chars. Split the list into safe chunks
    # so larger tool collections still render. Buttons attach to the LAST
    # message so they remain visible at the bottom of the list.
    full_text = "\n".join(lines)
    MAX = 3800  # leave headroom for Markdown escaping
    if len(full_text) <= MAX:
        await update.message.reply_text(
            full_text, parse_mode="Markdown", reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return

    # Build chunks line-by-line so we never split inside a Markdown construct
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for line in lines:
        ln = len(line) + 1  # +1 for newline
        if cur_len + ln > MAX and cur:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += ln
    if cur:
        chunks.append("\n".join(cur))

    for i, chunk in enumerate(chunks):
        kw = {"parse_mode": "Markdown", "disable_web_page_preview": True}
        if i == len(chunks) - 1:
            kw["reply_markup"] = keyboard
        try:
            await update.message.reply_text(chunk, **kw)
        except Exception:
            # Fallback: send as plain text without Markdown if escaping breaks
            kw.pop("parse_mode", None)
            await update.message.reply_text(chunk, **kw)


async def runtool_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /runtool <name> [args...] — execute a saved tool."""
    if not update.message or not update.effective_user:
        return

    if update.effective_user.id != _ADMIN_ID:
        await update.message.reply_text("🚫 Only admin can run tools directly.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: `/runtool <name> [args...]`", parse_mode="Markdown"
        )
        return

    name = args[0]
    extra_text = " ".join(args[1:])
    extra_text = await _translate_query(extra_text)
    extra_args = extra_text.split() if extra_text else []
    google_auth = os.path.exists(os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json"))

    _eu = update.effective_user
    run_context = {
        "user_id": _eu.id if _eu else 0,
        "chat_id": update.message.chat_id,
        "username": (_eu.username if _eu else None),
        "first_name": (_eu.first_name if _eu else None),
        "args": extra_args,
        "google_auth": google_auth,
    }

    await update.message.chat.send_action("typing")

    from core.tool_factory import run_tool

    result = await run_tool(name, run_context)
    await _send_tool_result(update.message.reply_text, name, result, chat=update.message.chat)


async def deltool_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /deltool <name> — delete a saved tool."""
    if not update.message or not update.effective_user:
        return

    if update.effective_user.id != _ADMIN_ID:
        await update.message.reply_text("🚫 Only admin can delete tools.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: `/deltool <name>`", parse_mode="Markdown"
        )
        return

    from core.tool_factory import delete_tool

    name = args[0]
    if delete_tool(name):
        await update.message.reply_text(f"🗑️ Tool `{name}` deleted.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Tool `{name}` not found.", parse_mode="Markdown")


async def edittool_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /edittool <name> <instruction> — edit an existing tool with an LLM prompt.

    Uses the same ground-truth + auto-retry pipeline as /qbfix: runs the current
    saved tool, fetches any URL mentioned in the instruction or code, includes
    that as OBSERVED OUTPUT / OBSERVED PAGE in the LLM prompt, then auto-retries
    once if the first edit still returns a failure-shaped result.
    """
    if not update.message or not update.effective_user:
        return

    if update.effective_user.id != _ADMIN_ID:
        await update.message.reply_text("🚫 Only admin can edit tools.")
        return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: `/edittool <name> <instruction>`\n\n"
            "Example: `/edittool amazon_search add --sort price flag`",
            parse_mode="Markdown",
        )
        return

    name = args[0]
    instruction = " ".join(args[1:])
    user_id = update.effective_user.id

    from core.tool_factory import (
        edit_tool, test_tool_code, get_tool_code,
        gather_fix_context, _format_fix_context, _looks_like_failure,
    )

    current_code = get_tool_code(name)
    if current_code is None:
        await update.message.reply_text(f"❌ Tool `{name}` not found.", parse_mode="Markdown")
        return

    await update.message.chat.send_action("typing")
    status_msg = await update.message.reply_text(
        f"✏️ Gathering ground truth for `{name}`...\n\n_{instruction}_",
        parse_mode="Markdown",
    )

    test_ctx = {
        "user_id": user_id,
        "chat_id": update.message.chat_id,
        "args": [],
        "raw_extra": "",
        "trigger_word": "",
        "google_auth": os.path.exists(os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")),
    }

    truth = await gather_fix_context(current_code, instruction, test_ctx)
    extra_context = _format_fix_context(truth)

    try:
        await status_msg.edit_text(
            f"✏️ Asking Queen Bee to edit `{name}`...", parse_mode="Markdown"
        )
    except Exception:
        pass

    meta, result = await edit_tool(name, instruction, user_id=user_id, extra_context=extra_context)
    if meta is None:
        await status_msg.edit_text(f"❌ {result}")
        return

    test_result = await test_tool_code(result, dict(test_ctx), timeout_sec=30)
    attempts = 1

    if _looks_like_failure(test_result):
        try:
            await status_msg.edit_text(
                f"✏️ First attempt still failing, auto-retrying with new observation...",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        retry_truth = await gather_fix_context(result, instruction, test_ctx)
        retry_extra = _format_fix_context(retry_truth)
        retry_instruction = (
            f"{instruction}\n\nPREVIOUS EDIT ATTEMPT STILL FAILED. "
            f"The tool now returns the OBSERVED OUTPUT below. "
            f"Look at OBSERVED PAGE / TABLE PREVIEW carefully and fix the actual root cause."
        )
        meta2, result2 = await edit_tool(
            name,
            retry_instruction,
            user_id=user_id,
            extra_context=retry_extra,
            override_code=result,
        )
        if meta2 is not None and isinstance(result2, str):
            meta, result = meta2, result2
            test_result = await test_tool_code(result, dict(test_ctx), timeout_sec=30)
            attempts = 2

    context.user_data["pending_tool_edit"] = {
        "meta": meta,
        "code": result,
    }

    test_ok = not _looks_like_failure(test_result)
    test_icon = "✅" if test_ok else "⚠️"
    test_display = test_result[:500] + ("…" if len(test_result) > 500 else "")
    for ch in ("_", "*", "`", "["):
        test_display = test_display.replace(ch, f"\\{ch}")

    code_preview = result[:600] + ("\n…(truncated)" if len(result) > 600 else "")
    attempts_note = f" (after {attempts} attempts)" if attempts > 1 else ""
    text = (
        f"✏️ *Tool updated:* `{meta.name}` (v{meta.version}){attempts_note}\n"
        f"📝 {meta.description}\n"
        f"🏷️ Keywords: {', '.join(meta.trigger_keywords)}\n\n"
        f"{test_icon} *Re-test (no args):*\n{test_display}\n\n"
        f"```python\n{code_preview}\n```\n\n"
        "Save this edit?"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Save", callback_data="qb_edit_save"),
            InlineKeyboardButton("❌ Discard", callback_data="qb_edit_discard"),
        ]
    ])
    try:
        await status_msg.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def edittool_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle confirm/discard for /edittool."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if query.data == "qb_edit_discard":
        context.user_data.pop("pending_tool_edit", None)
        await query.edit_message_text("❌ Edit discarded.")
        return

    if query.data == "qb_edit_save":
        pending = context.user_data.pop("pending_tool_edit", None)
        if not pending:
            await query.edit_message_text("⚠️ No pending edit found.")
            return

        from core.tool_factory import save_tool

        meta = pending["meta"]
        code = pending["code"]
        try:
            save_tool(meta, code)
            await query.edit_message_text(
                f"✅ Tool `{meta.name}` updated to v{meta.version}.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Failed to save: {e}")


_NEWTOOL_HELP = (
    "🐝 *How to create a new Queen Bee tool*\n"
    "\n"
    "*Quick way — let the LLM build it for you:*\n"
    "Send a message that starts with `>>` followed by a plain‑English description.\n"
    "Examples:\n"
    "`>> show the BTC price in EUR from CoinGecko`\n"
    "`>> fetch tomorrow's weather for Tallinn`\n"
    "`>> summarise my last 5 Strava activities`\n"
    "\n"
    "What happens next:\n"
    "1. The bot asks the LLM to generate a small Python module.\n"
    "2. The generated code is screened for forbidden patterns (subprocess, file\n"
    "   listing/deletion, eval, etc.). Admin bypasses this.\n"
    "3. You get the proposed code and can `/qbtest`, `/qbfix <instruction>`,\n"
    "   `/qbsave`, or `/qbdiscard`.\n"
    "4. Non‑admin tools are saved as *pending\\_review* and unlocked once an\n"
    "   async LLM security review (or admin Approve) clears them.\n"
    "\n"
    "*Run / list / edit / delete:*\n"
    "`/tools` – list saved tools\n"
    "`/toolhelp [name]` – show examples for one or all tools\n"
    "`/runtool <name> [args...]` – execute a tool\n"
    "`/edittool <name> <instruction>` – ask the LLM to modify a tool\n"
    "`/deltool <name>` – delete a saved tool\n"
    "\n"
    "*Tool contract:*\n"
    "Every tool is one file `tools/<name>.py` exposing exactly:\n"
    "```\n"
    "async def run(context: dict) -> str:\n"
    "    # context['user_id'], context['args'] (list[str]),\n"
    "    # context['google_auth'] (bool)\n"
    "    return 'human-readable result string'\n"
    "```\n"
    "Plus a manifest `tools/<name>.json` with: name, description,\n"
    "requires\\_google\\_auth, trigger\\_keywords, permission, status.\n"
    "\n"
    "Inside `run()` you may import: `tool_utils` (fetch\\_json / fetch\\_text /\n"
    "fetch\\_html / fetch\\_rendered / parse\\_args), `aiohttp`, `httpx`, `bs4`,\n"
    "`services.llm_service.get_llm_response`, `services.strava_service`,\n"
    "`gmail_service`, `bill_parser`, plus the stdlib.\n"
    "\n"
    "*Rules:* handle all exceptions, never raise, keep imports inside `run()`,\n"
    "never touch the host filesystem or run shell commands.\n"
    "\n"
    "See the [`tools/`](tools) folder for working examples."
)


async def newtool_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /newtool — explain how to create a new Queen Bee tool."""
    if not update.message:
        return
    await update.message.reply_text(_NEWTOOL_HELP, parse_mode="Markdown", disable_web_page_preview=True)


_TOOLHELP_OVERVIEW = (
    "🤖 <b>How to use tools</b>\n\n"
    "An <b>tool</b> is a small Python module that performs one specific task "
    "(price lookup, weather, Strava stats, smart-home control, …). "
    "There are four ways to run one:\n\n"
    "<b>1. Trigger keyword</b> (easiest)\n"
    "Send a message whose <i>first word</i> matches one of a tool's "
    "<code>trigger_keywords</code>. The rest of the message becomes the args.\n"
    "Example: <code>btc</code> · <code>weather tomorrow</code> · "
    "<code>jysk chair</code>\n\n"
    "<b>2. Explicit call</b>\n"
    "<code>/runtool &lt;name&gt; [args...]</code>\n"
    "Example: <code>/runtool btc_price_coingecko</code>\n\n"
    "<b>3. Smart dispatch</b>\n"
    "If your message doesn't match any keyword but looks like a tool request, "
    "the bot will suggest one with confirm/decline buttons.\n\n"
    "<b>4. Scheduled / monitor</b>\n"
    "Some tools run on a schedule and push results to you automatically.\n\n"
    "<b>Managing tools</b>\n"
    "<code>/tools</code> – short list of saved tools\n"
    "<code>/toolhelp &lt;name&gt;</code> – full detail for one tool\n"
    "<code>/newtool</code> – walkthrough on creating a new tool\n"
    "<code>&gt;&gt; &lt;description&gt;</code> – generate a new tool with the LLM\n"
    "<code>/edittool &lt;name&gt; &lt;instruction&gt;</code> – LLM-edit a tool\n"
    "<code>/deltool &lt;name&gt;</code> – delete a saved tool\n\n"
    "<b>Access</b>\n"
    "Every tool has a <code>permission</code> label (public, finance, email, admin, …). "
    "You only see and can run tools your role allows. Admin sees everything.\n\n"
    "<b>Below: descriptions of every available tool.</b>"
)


def _format_tool_block(a) -> str:
    """Return an HTML block describing one tool for the full toolhelp listing."""
    from html import escape as _esc

    keywords = ", ".join(a.trigger_keywords) if a.trigger_keywords else "—"
    example = a.help_example or (
        f"{a.trigger_keywords[0]}" if a.trigger_keywords else f"/runtool {a.name}"
    )
    example_first = example.split("\n", 1)[0].strip()

    auth_flag = " 🔒google-auth" if a.requires_google_auth else ""
    perm_flag = f" · 🛡️ {a.permission}" if a.permission and a.permission != "public" else ""

    return (
        f"🤖 <b>{_esc(a.name)}</b>{perm_flag}{auth_flag}\n"
        f"{_esc(a.description or '(no description)')}\n"
        f"🏷️ <i>{_esc(keywords)}</i>\n"
        f"💡 <code>{_esc(example_first)}</code>"
    )


async def toolhelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /toolhelp [name] — comprehensive how-to + descriptions of all tools."""
    if not update.message:
        return

    from core.tool_factory import list_tools

    tools = list_tools()
    args = context.args or []

    # Single-tool detail view
    if args:
        name = args[0].lower()
        for a in tools:
            if a.name.lower() == name or name in [k.lower() for k in (a.trigger_keywords or [])]:
                await update.message.reply_text(_build_toolhelp_detail(a), parse_mode="Markdown")
                return
        await update.message.reply_text(f"❌ Tool `{name}` not found.", parse_mode="Markdown")
        return

    approved = sorted(
        (a for a in tools if a.status == "approved"),
        key=lambda a: a.name.lower(),
    )
    if not approved:
        await update.message.reply_text("No tools available yet.")
        return

    from handlers.utils import safe_reply

    # 1. Send the comprehensive how-to overview first.
    await safe_reply(update.message, _TOOLHELP_OVERVIEW)

    # 2. Send the full tool descriptions, chunked to stay under Telegram's 4096-char limit.
    blocks = [_format_tool_block(a) for a in approved]

    chunks: list[str] = []
    current = ""
    for block in blocks:
        candidate = (current + "\n\n" + block) if current else block
        if len(candidate) > 3500:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)

    buttons = [
        InlineKeyboardButton(a.name, callback_data=f"toolhelp:{a.name}")
        for a in approved
    ]
    keyboard_rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    keyboard = InlineKeyboardMarkup(keyboard_rows)

    for i, chunk in enumerate(chunks):
        header = f"📚 <b>Tools ({i+1}/{len(chunks)})</b>\n\n" if len(chunks) > 1 else ""
        is_last = i == len(chunks) - 1
        footer = (
            "\n\n<i>Tap a tool below for full detail "
            "(or use </i><code>/toolhelp &lt;name&gt;</code><i>).</i>"
            if is_last else ""
        )
        await safe_reply(
            update.message,
            header + chunk + footer,
            reply_markup=keyboard if is_last else None,
        )


async def toolhelp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle toolhelp:<name> inline button presses."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    name = data.removeprefix("toolhelp:")

    from core.tool_factory import list_tools
    for a in list_tools():
        if a.name == name:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"📋 Copy `{a.name}`", copy_text=CopyTextButton(text=a.name)),
            ]])
            await query.message.reply_text(
                _build_toolhelp_detail(a), parse_mode="Markdown", reply_markup=keyboard,
            )
            return
    await query.message.reply_text(f"❌ Tool `{name}` not found.", parse_mode="Markdown")


async def tool_smart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle confirm/decline buttons for smart tool detection."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if query.data == "tool_decline":
        context.user_data.pop("pending_smart_tool", None)
        await query.edit_message_text("👌 OK, let me answer as a regular chat instead.")
        user_msg = context.user_data.get("_declined_message", "")
        if not user_msg:
            return
        context.user_data.pop("_declined_message", None)
        user_id = query.from_user.id if query.from_user else 0
        conversation_manager.add_message(user_id, "user", user_msg)
        history = conversation_manager.get_history(user_id)
        reply = await get_llm_response(
            history,
            model=conversation_manager.get_model(user_id),
        )
        conversation_manager.add_message(user_id, "assistant", reply)
        await _send_collapsed(query.message.reply_text, reply)
        return

    if query.data != "tool_confirm":
        return

    pending = context.user_data.pop("pending_smart_tool", None)
    if not pending:
        await query.edit_message_text("⚠️ No pending tool. Please send your message again.")
        return

    from core.tool_factory import run_tool

    tool_name = pending["tool_name"]
    query_args = pending["query_args"]
    user_id = query.from_user.id if query.from_user else 0

    await query.edit_message_text(f"⏳ Running `{tool_name}`...", parse_mode="Markdown")
    await query.message.chat.send_action("typing")

    raw_extra = pending.get("raw_extra", query_args)
    _fu = query.from_user
    run_context = {
        "user_id": user_id,
        "chat_id": query.message.chat_id,
        "username": (_fu.username if _fu else None),
        "first_name": (_fu.first_name if _fu else None),
        "args": query_args.split() if query_args else [],
        "raw_extra": raw_extra,
        "trigger_word": pending.get("trigger_word", ""),
        "google_auth": os.path.exists(os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")),
    }
    result = await run_tool(tool_name, run_context)
    sent = await _send_tool_result(query.message.reply_text, tool_name, result, chat=query.message.chat)
    _track_tool_message(context, sent, tool_name, run_context)
