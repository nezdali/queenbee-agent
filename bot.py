"""
Telegram Chatbot with LLM Backend — entry point.

Initializes the bot, registers handlers, and starts polling.
"""

import logging

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import TELEGRAM_BOT_TOKEN, validate_config
from handlers import (
    # Core chat
    start_command, help_command, clear_command,
    handle_message, handle_photo,
    model_command, setmodel_command,
    # Group restriction
    handle_my_chat_member,
    # Queen Bee tool factory
    tools_command, runtool_command, deltool_command,
    qb_callback,
    qbtest_command, qbsave_command, qbdiscard_command,
    qbfix_command, qbdebug_command,
    edittool_command, edittool_callback,
    tool_smart_callback, toolhelp_command, toolhelp_callback,
    newtool_command,
    # Admin / RBAC
    adduser_command, deluser_command, listusers_command,
    listroles_command, setrole_command,
    # Strava (optional)
    stravaconnect_command, stravaauth_command, stravahelp_command,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Initialize and start the Telegram bot."""
    validate_config()

    # Defense-in-depth: strip stray `*` from outbound text outside <pre>/<code>.
    from core.output_safety import install as _install_output_safety
    _install_output_safety()

    logger.info("Starting Telegram bot...")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Group restriction: leave non-admin groups
    app.add_handler(
        ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    # --- Core chat commands ------------------------------------------------
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler(("model", "models"), model_command))
    app.add_handler(CommandHandler("setmodel", setmodel_command))

    # --- Queen Bee tool factory ------------------------------------------
    app.add_handler(CommandHandler("tools", tools_command))
    app.add_handler(CommandHandler("runtool", runtool_command))
    app.add_handler(CommandHandler("deltool", deltool_command))
    app.add_handler(CommandHandler("edittool", edittool_command))
    app.add_handler(CommandHandler("qbtest", qbtest_command))
    app.add_handler(CommandHandler("qbsave", qbsave_command))
    app.add_handler(CommandHandler("qbdiscard", qbdiscard_command))
    app.add_handler(CommandHandler("qbfix", qbfix_command))
    app.add_handler(CommandHandler("qbdebug", qbdebug_command))
    app.add_handler(CallbackQueryHandler(edittool_callback, pattern="^qb_edit_"))
    app.add_handler(CallbackQueryHandler(qb_callback, pattern="^qb_"))
    app.add_handler(
        CallbackQueryHandler(tool_smart_callback, pattern="^tool_(confirm|decline)$")
    )
    app.add_handler(CommandHandler("toolhelp", toolhelp_command))
    app.add_handler(CallbackQueryHandler(toolhelp_callback, pattern="^toolhelp:"))
    app.add_handler(CommandHandler("newtool", newtool_command))

    # --- Admin / RBAC ------------------------------------------------------
    app.add_handler(CommandHandler("adduser", adduser_command))
    app.add_handler(CommandHandler("deluser", deluser_command))
    app.add_handler(CommandHandler("listusers", listusers_command))
    app.add_handler(CommandHandler("listroles", listroles_command))
    app.add_handler(CommandHandler("setrole", setrole_command))

    # --- Strava (optional; no-op if STRAVA_CLIENT_ID is unset) -------------
    app.add_handler(CommandHandler("stravaconnect", stravaconnect_command))
    app.add_handler(CommandHandler("stravaauth", stravaauth_command))
    app.add_handler(CommandHandler("stravahelp", stravahelp_command))

    # --- Messages ----------------------------------------------------------
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
