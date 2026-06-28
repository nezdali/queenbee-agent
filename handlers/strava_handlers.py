"""
Strava OAuth handlers.

Provides /stravaconnect, /stravaauth <code>, /stravahelp.
Per-user tokens are stored by services.strava_service.
"""

from telegram import Update
from telegram.ext import ContextTypes


async def stravaconnect_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stravaconnect — send Strava OAuth authorization URL."""
    if not update.message or not update.effective_user:
        return

    from config import STRAVA_CLIENT_ID
    if not STRAVA_CLIENT_ID:
        await update.message.reply_text(
            "⚠️ Strava is not configured.\n\n"
            "Add `STRAVA_CLIENT_ID` and `STRAVA_CLIENT_SECRET` to your `.env` file.\n"
            "Create an app at https://www.strava.com/settings/api\n"
            "Use `localhost` as the Authorization Callback Domain."
        )
        return

    from services.strava_service import get_auth_url, is_connected
    user_id = update.effective_user.id
    connected = is_connected(user_id)

    url = get_auth_url()
    status = "✅ You are already connected (this will re-authorize).\n\n" if connected else ""
    await update.message.reply_text(
        f"{status}"
        "🏃 *Connect Strava*\n\n"
        "⚠️ Strava blocks Telegram's in-app browser (`disallowed_usertool`).\n"
        "*Long-press the link below → Open in external browser* (Chrome/Safari) "
        "or copy-paste it. Do NOT just tap it.\n\n"
        "1. Authorize the app in your browser\n"
        "2. After authorizing, your browser will show an error page — that's expected.\n"
        "3. Copy the `code=` value from the URL bar and send:\n"
        "`/stravaauth <code>`",
        parse_mode="Markdown",
    )
    await update.message.reply_text(url, disable_web_page_preview=True)


async def stravaauth_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stravaauth <code> — exchange the Strava authorization code for tokens."""
    if not update.message or not update.effective_user:
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: `/stravaauth <code>`\n\nGet the code from the URL after authorizing with /stravaconnect.",
            parse_mode="Markdown",
        )
        return

    from services.strava_service import exchange_code
    user_id = update.effective_user.id
    await update.message.chat.send_action("typing")

    try:
        data = await exchange_code(args[0].strip(), user_id=user_id)
        athlete = data.get("athlete", {})
        name = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip() or "unknown"
        await update.message.reply_text(
            f"✅ *Strava connected!*\n\n"
            f"Athlete: {name}\n"
            f"Token saved for your account. Tools can now access your Strava data.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to connect Strava: {e}")


async def stravahelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stravahelp — show what Strava data the bot can answer questions about."""
    if not update.message or not update.effective_user:
        return

    from services.strava_service import is_connected
    user_id = update.effective_user.id
    connected = is_connected(user_id)
    status_line = (
        "✅ Your Strava account is connected.\n\n"
        if connected
        else "⚠️ Strava is not connected yet — run /stravaconnect first.\n\n"
    )

    text = (
        "🏃 *Strava — what you can ask*\n\n"
        f"{status_line}"
        "Just ask in plain language (any language). The bot can pull these "
        "stats from your Strava account:\n\n"
        "👤 *Profile & totals*\n"
        "• YTD and all-time totals per sport (rides, runs, swims): "
        "distance, time, elevation, activity count\n"
        "• Recent 4-week totals\n\n"
        "📋 *Activity list*\n"
        "• Recent activities with date, type, distance, moving/elapsed time, pace\n"
        "• Heart rate (avg + max), power (avg / weighted / max watts), kJ\n"
        "• Elevation gain, whether power was from a real meter\n\n"
        "🔬 *Single activity (deep dive)*\n"
        "• Full power & HR zone distribution (time + % in each zone)\n"
        "• Per-lap splits: distance, time, speed, HR, watts, elevation\n"
        "• Cadence, calories, suffer score, gear, description, temperature\n\n"
        "❤️ *Kudos & comments*\n"
        "• Who liked (kudosed) a specific activity\n"
        "• Total kudos count and full list of comments\n\n"
        "💡 *Example questions:*\n"
        "• How many km did I ride this month?\n"
        "• What was my average watts yesterday?\n"
        "• How much time did I spend in power zone 5 on my last ride?\n"
        "• What's the elevation gain of my last 5 rides?\n"
        "• Show my HR zones from my Saturday workout\n"
        "• Who liked my last ride?\n"
        "• How many kudos did my Sunday run get, and who commented?\n\n"
        "🤝 *Other athletes:*\n"
        "If another connected user has registered an alias in `KNOWN_USERS` "
        "(see config.py), you can ask:\n"
        "• How was <name>'s last ride?\n"
        "• Compare my weekly distance to <name>'s\n"
        "• Who kudosed <name>'s last activity?\n\n"
        "🔧 *Commands:*\n"
        "/stravaconnect — connect / re-authorize\n"
        "/stravaauth <code> — finish OAuth"
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)
