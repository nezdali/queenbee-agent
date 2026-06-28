"""
Core handlers: /start, /help, /clear, /model, /setmodel, plus the main
text-message and photo handlers.

The text handler routes messages through:
  1. Queen Bee trigger (`>>` prefix)        — generate a new tool
  2. Saved-tool keyword dispatch           — first-word match on trigger_keywords
  3. LLM orchestrator (with tool calling)   — fallback
"""

import base64
import html as _html
import logging
import os as _os
import random as _random
import time as _time

from telegram import Update
from telegram.ext import ContextTypes

from config import ALLOWED_USERS, ALLOWED_USERNAMES
from services.llm_service import get_llm_response_stream, list_models, check_model_capabilities
from services.model_router import ModelRouter
from core.agent_tools import dispatch_tool, build_run_tool_schema
from core import user_roles as _user_roles_store
import core.tool_registry as tool_registry

from .utils import (
    conversation_manager, _QB_ADMIN_USER_ID, logger,
    _send_collapsed, _send_tool_result,
    _stream_tool_to_msg,
    _track_tool_message, _COLLAPSIBLE_THRESHOLD,
    _md_to_telegram_html,
)
from .tool_dispatch import _THINKING_PHRASES, VIBE_PHRASES, DEFAULT_VIBE, _translate_query


model_router = ModelRouter()


# ---------------------------------------------------------------------------
# /start, /help, /clear
# ---------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.message:
        await update.message.reply_text(
            f"Hello {update.effective_user.first_name}! 👋\n\n"
            "I'm an AI-powered chatbot. Just send me a message and I'll respond "
            "using a large language model.\n\n"
            "Commands:\n"
            "/start - Show this welcome message\n"
            "/clear - Clear conversation history\n"
            "/help - Show help information"
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    text = (
        "💡 *How to use this bot:*\n\n"
        "Just send a message and I'll respond using AI. I remember the context "
        "of our conversation.\n\n"
        "*Commands:*\n"
        "/start - Welcome message\n"
        "/clear - Reset conversation history\n"
        "/model - List available AI models\n"
        "/setmodel <id> - Switch AI model\n"
        "/help - This help message\n\n"
        "🐝 *Queen Bee Tools:*\n"
        "`>> <description>` - Generate a new tool with AI\n"
        "/newtool - How to create a new tool (walkthrough)\n"
        "/tools - List saved tools\n"
        "/toolhelp - Show tools with examples\n"
        "/runtool <name> - Run a saved tool\n"
        "/deltool <name> - Delete a saved tool\n"
        "/edittool <name> <instruction> - Edit a tool with AI"
    )

    if update.effective_user and update.effective_user.id == _QB_ADMIN_USER_ID:
        text += (
            "\n\n"
            "👥 *User Roles (admin only):*\n"
            "/listusers - Show all users with their roles\n"
            "/listroles - Show available roles and their permissions\n"
            "/adduser <id> <role1,role2> [@username] - Grant roles to a user\n"
            "/deluser <id> - Remove a user from the role store\n"
            "/setrole <tool> <role> - Change which role can run a tool"
        )

    await update.message.reply_text(text, parse_mode="Markdown")


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.message:
        conversation_manager.clear_history(update.effective_user.id)
        await update.message.reply_text(
            "🗑️ Conversation history cleared! Start fresh by sending a new message."
        )


# ---------------------------------------------------------------------------
# /model, /setmodel
# ---------------------------------------------------------------------------
async def setmodel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    args = context.args or []
    if not args:
        current = conversation_manager.get_model(update.effective_user.id) or model_router.chat_model()
        await update.message.reply_text(
            f"Current model: `{current}`\nUsage: `/setmodel <model_id>`",
            parse_mode="Markdown",
        )
        return
    model_id = args[0]
    conversation_manager.set_model(update.effective_user.id, model_id)
    logger.info("User %s set model to %s", update.effective_user.id, model_id)
    await update.message.reply_text(f"Model set to `{model_id}`", parse_mode="Markdown")


async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    args = context.args or []

    if not args:
        models = await list_models()
        if not models:
            await update.message.reply_text("Could not retrieve model list.")
            return
        lines = ["*Available models:*"]
        for m in models[:30]:
            lines.append(f"• `{m}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    model_id = args[0]
    caps = await check_model_capabilities(model_id)
    await update.message.reply_text(
        f"*Model:* `{model_id}`\n"
        f"Tools: {'✅' if caps.get('tools') else '❌'}\n"
        f"Vision: {'✅' if caps.get('vision') else '❌'}",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Authorization helper
# ---------------------------------------------------------------------------
def _is_authorized(user_id: int, username: str) -> bool:
    """Empty allow-lists mean no restriction. Persistent role-store users also pass."""
    id_allowed = not ALLOWED_USERS or user_id in ALLOWED_USERS
    name_allowed = not ALLOWED_USERNAMES or username in ALLOWED_USERNAMES
    roles_allowed = user_id in _user_roles_store.list_all()
    return id_allowed or name_allowed or roles_allowed


# ---------------------------------------------------------------------------
# /handle_message — main text router
# ---------------------------------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not update.effective_user:
        return

    user_id = update.effective_user.id
    chat_type = update.message.chat.type  # "private" | "group" | "supergroup"

    # In groups: only respond when @mentioned or replied-to
    if chat_type in ("group", "supergroup"):
        bot_username = context.bot.username or ""
        is_mentioned = f"@{bot_username}" in update.message.text
        is_reply_to_bot = (
            update.message.reply_to_message is not None
            and update.message.reply_to_message.from_user is not None
            and update.message.reply_to_message.from_user.id == context.bot.id
        )
        if not is_mentioned and not is_reply_to_bot:
            return

    username = (update.effective_user.username or "").lower()
    if not _is_authorized(user_id, username):
        await update.message.reply_text("⛔ Sorry, you are not authorized to use this bot.")
        logger.warning("Unauthorized access attempt by user %s (@%s)", user_id, username)
        return

    user_message = update.message.text
    if chat_type in ("group", "supergroup") and context.bot.username:
        user_message = user_message.replace(f"@{context.bot.username}", "").strip()

    logger.info("Message from user %s: %s", user_id, user_message[:100])

    # Re-run last tool on reply-to-bot
    reply_msg = update.message.reply_to_message
    if (
        reply_msg and reply_msg.from_user and reply_msg.from_user.id == context.bot.id
    ):
        tool_history = context.user_data.get("_tool_messages", {})
        prev = tool_history.get(reply_msg.message_id)
        if prev:
            from core.tool_factory import run_tool
            tool_name = prev["tool_name"]
            extra_text = await _translate_query(user_message)
            _eu = update.effective_user
            run_context = {
                "user_id": user_id,
                "chat_id": update.message.chat_id,
                "username": (_eu.username if _eu else None),
                "first_name": (_eu.first_name if _eu else None),
                "args": extra_text.split() if extra_text else [],
                "raw_extra": user_message,
                "trigger_word": prev["run_context"].get("trigger_word", ""),
            }
            thinking_msg = await update.message.reply_text(f"🔍 Re-running {tool_name}...")
            await update.message.chat.send_action("typing")
            result = await run_tool(tool_name, run_context)
            sent = await _stream_tool_to_msg(thinking_msg, tool_name, result, chat=update.message.chat)
            _track_tool_message(context, sent, tool_name, run_context)
            return

    # Queen Bee trigger: `>>` prefix → generate a tool
    stripped = user_message.lstrip()
    _QB_PREFIXES = (">>", "›>", "\u00bb")
    is_qb = any(stripped.startswith(p) for p in _QB_PREFIXES)
    if is_qb:
        from .queen_bee import _handle_qb_request
        description = stripped.lstrip(">›\u00bb").strip()
        if description:
            await _handle_qb_request(update, context, description)
        else:
            await update.message.reply_text(
                "🐝 *Queen Bee Tool*\n\n"
                "Create a new tool by describing what it should do:\n"
                "`>> describe the tool task here`\n\n"
                "Example: `>> fetch current Bitcoin price from CoinGecko`\n\n"
                "Manage tools: /tools · /runtool · /deltool",
                parse_mode="Markdown",
            )
        return

    # Thinking phrase
    vibe = conversation_manager.get_vibe(user_id) or DEFAULT_VIBE
    phrases = VIBE_PHRASES.get(vibe, _THINKING_PHRASES)
    thinking_phrase = _random.choice(phrases)
    thinking_msg = await update.message.reply_text(f"🔍 {thinking_phrase}")

    # Keyword dispatch: first word matches a tool's trigger_keywords
    first_word = user_message.split()[0].lower().lstrip("/") if user_message.split() else ""
    if first_word:
        from core.tool_factory import list_tools, run_tool
        for tool_meta in list_tools():
            if first_word in {kw.lower() for kw in tool_meta.trigger_keywords}:
                raw_extra = " ".join(user_message.split()[1:])
                extra_text = await _translate_query(raw_extra)
                _eu = update.effective_user
                run_context = {
                    "user_id": user_id,
                    "chat_id": update.message.chat_id,
                    "username": (_eu.username if _eu else None),
                    "first_name": (_eu.first_name if _eu else None),
                    "args": extra_text.split() if extra_text else [],
                    "raw_extra": raw_extra,
                    "trigger_word": first_word,
                    "user_message": user_message,
                }
                try:
                    await thinking_msg.edit_text(
                        f"🔍 {thinking_phrase}\n"
                        f"🤖 Detected tool: `{tool_meta.name}`\n"
                        f"📋 {tool_meta.description}\n\n"
                        f"⏳ Running...",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
                await update.message.chat.send_action("typing")
                result = await run_tool(tool_meta.name, run_context)
                sent = await _stream_tool_to_msg(thinking_msg, tool_meta.name, result, chat=update.message.chat)
                _track_tool_message(context, sent, tool_meta.name, run_context)
                return

    # --- LLM orchestrator path -------------------------------------------
    await update.message.chat.send_action("typing")
    conversation_manager.add_message(user_id, "user", user_message)
    history = conversation_manager.get_history(user_id)

    active_tools = tool_registry.get_schemas_for_user(user_id)
    tool_schema = build_run_tool_schema(user_id=user_id)
    if tool_schema:
        active_tools.append(tool_schema)

    _user_ctx = {
        "user_id": user_id,
        "chat_id": update.message.chat_id,
        "username": (update.effective_user.username if update.effective_user else None),
        "first_name": (update.effective_user.first_name if update.effective_user else None),
        "user_message": user_message,
    }
    _step_lines: list[str] = []

    async def _orchestrator_executor(name: str, arguments: str) -> str:
        if name == "run_tool":
            import json as _j
            try:
                _args = _j.loads(arguments) if arguments else {}
            except Exception:
                _args = {}
            tool_name = _args.get("tool_name", "?")
            tool_args = _args.get("args", "")
            step = f"🤖 Tool <b>{_html.escape(tool_name)}</b>"
            if tool_args:
                step += f" — <i>{_html.escape(tool_args)}</i>"
            _step_lines.append(step)
            try:
                await thinking_msg.edit_text(
                    "\n".join(_step_lines) + "\n⏳ Running...", parse_mode="HTML",
                )
            except Exception:
                pass
            await update.message.chat.send_action("typing")
        elif name == "fetch_url":
            _step_lines.append("🌐 Fetching URL...")
            try:
                await thinking_msg.edit_text("\n".join(_step_lines), parse_mode="HTML")
            except Exception:
                pass

        result = await dispatch_tool(name, arguments, _user_context=_user_ctx)

        if name == "run_tool" and _step_lines:
            _step_lines[-1] = _step_lines[-1].split("\n")[0] + " ✅"
            try:
                await thinking_msg.edit_text("\n".join(_step_lines), parse_mode="HTML")
            except Exception:
                pass
        return result

    full_reply = ""
    last_edit_time = 0.0
    _STREAM_EDIT_INTERVAL = 1.0
    _STREAM_MIN_CHARS = 80

    async for chunk in get_llm_response_stream(
        history,
        model=conversation_manager.get_model(user_id),
        tools=active_tools,
        tool_executor=_orchestrator_executor,
    ):
        full_reply += chunk
        now = _time.monotonic()
        if now - last_edit_time >= _STREAM_EDIT_INTERVAL and len(full_reply) >= _STREAM_MIN_CHARS:
            prefix = "\n".join(_step_lines) + "\n\n" if _step_lines else ""
            display = prefix + _md_to_telegram_html(full_reply) + " ▍"
            try:
                await thinking_msg.edit_text(
                    display, parse_mode="HTML", disable_web_page_preview=True,
                )
            except Exception:
                pass
            last_edit_time = now

    reply = full_reply or "I received an empty response. Please try again."

    if _step_lines:
        summary = "\n".join(_step_lines)
        html_reply = _md_to_telegram_html(reply)
        if len(reply) > _COLLAPSIBLE_THRESHOLD:
            final_text = f"{summary}\n\n<blockquote expandable>{html_reply}</blockquote>"
        else:
            final_text = f"{summary}\n\n{html_reply}"
        try:
            await thinking_msg.edit_text(final_text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            try:
                await thinking_msg.delete()
            except Exception:
                pass
            await _send_collapsed(update.message.reply_text, reply)
    else:
        html_reply = _md_to_telegram_html(reply)
        if len(reply) > _COLLAPSIBLE_THRESHOLD:
            final_text = f"<blockquote expandable>{html_reply}</blockquote>"
        else:
            final_text = html_reply
        try:
            await thinking_msg.edit_text(final_text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            try:
                await thinking_msg.delete()
            except Exception:
                pass
            await _send_collapsed(update.message.reply_text, reply)

    conversation_manager.add_message(user_id, "assistant", reply)


# ---------------------------------------------------------------------------
# Photo handling — pass to a vision-capable model
# ---------------------------------------------------------------------------
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the photo to a vision-capable model and stream the answer."""
    if not update.message or not update.effective_user or not update.message.photo:
        return

    user_id = update.effective_user.id
    username = (update.effective_user.username or "").lower()
    if not _is_authorized(user_id, username):
        await update.message.reply_text("⛔ Sorry, you are not authorized to use this bot.")
        return

    caption = (update.message.caption or "").strip() or "Describe this image."

    photo = update.message.photo[-1]  # highest resolution
    file = await context.bot.get_file(photo.file_id)
    buf = await file.download_as_bytearray()
    b64 = base64.b64encode(bytes(buf)).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"

    history = [
        {"role": "user", "content": [
            {"type": "text", "text": caption},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]},
    ]

    thinking_msg = await update.message.reply_text("🖼️ Looking at the image...")
    await update.message.chat.send_action("typing")

    full_reply = ""
    last_edit = 0.0
    async for chunk in get_llm_response_stream(
        history,
        model=conversation_manager.get_model(user_id) or model_router.chat_model(),
    ):
        full_reply += chunk
        now = _time.monotonic()
        if now - last_edit >= 1.0 and len(full_reply) >= 80:
            try:
                await thinking_msg.edit_text(
                    _md_to_telegram_html(full_reply) + " ▍",
                    parse_mode="HTML", disable_web_page_preview=True,
                )
            except Exception:
                pass
            last_edit = now

    reply = full_reply or "I couldn't read the image."
    try:
        await thinking_msg.edit_text(
            _md_to_telegram_html(reply),
            parse_mode="HTML", disable_web_page_preview=True,
        )
    except Exception:
        await update.message.reply_text(reply)
