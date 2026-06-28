"""
Group restriction handler.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from .utils import _QB_ADMIN_USER_ID

logger = logging.getLogger(__name__)


async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Leave any group/supergroup the bot is added to, unless added by admin."""
    if not update.my_chat_member:
        return
    member = update.my_chat_member
    new_status = member.new_chat_member.status
    chat = member.chat
    added_by = member.from_user.id if member.from_user else None

    if new_status not in ("member", "administrator") or chat.type == "private":
        return

    if added_by == _QB_ADMIN_USER_ID:
        logger.info("Bot added to %s (id=%s) by admin — staying.", chat.title, chat.id)
        return

    logger.warning("Bot added to %s (id=%s) by user %s — leaving.", chat.title, chat.id, added_by)
    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text="🚫 This bot is private and cannot be added to group chats.",
        )
    except Exception:
        pass
    try:
        await context.bot.leave_chat(chat.id)
    except Exception as e:
        logger.error("Failed to leave chat %s: %s", chat.id, e)
