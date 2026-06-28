"""
Shared utilities for handler modules.
"""

import asyncio as _asyncio
import html as _html
import json as _json_mod
import logging
import re as _re
import time as _time
from datetime import datetime, timezone
from pathlib import Path as _Path

from config import QB_ADMIN_USER_ID
from core.conversation import ConversationManager

logger = logging.getLogger(__name__)

conversation_manager = ConversationManager()

# ---------------------------------------------------------------------------
# Admin user ID
# ---------------------------------------------------------------------------
# Set QB_ADMIN_USER_ID in .env (Telegram user ID of the admin). If unset, no
# user has admin privileges and admin-gated commands are denied.
_QB_ADMIN_USER_ID = QB_ADMIN_USER_ID or 0

# ---------------------------------------------------------------------------
# QB rate limiting
# ---------------------------------------------------------------------------
_QB_MAX_PER_DAY = 5
_QB_COOLDOWN_SECONDS = 15 * 60  # 15 minutes
# Per-user tracking: {user_id: {"last_ts": float, "day": str, "count": int}}
_qb_rate: dict = {}


def _check_qb_rate(user_id: int) -> str | None:
    """Return an error message if rate-limited, else None."""
    if user_id == _QB_ADMIN_USER_ID:
        return None
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    entry = _qb_rate.get(user_id, {"last_ts": 0.0, "day": "", "count": 0})
    # Reset daily counter if new day
    if entry["day"] != today:
        entry = {"last_ts": entry["last_ts"], "day": today, "count": 0}
    # Cooldown check
    elapsed = now.timestamp() - entry["last_ts"]
    if elapsed < _QB_COOLDOWN_SECONDS:
        remaining = int((_QB_COOLDOWN_SECONDS - elapsed) / 60) + 1
        return f"⏳ Please wait {remaining} more minute(s) before creating another tool."
    # Daily limit check
    if entry["count"] >= _QB_MAX_PER_DAY:
        return f"🚫 You've reached the daily limit of {_QB_MAX_PER_DAY} tools. Try again tomorrow."
    return None


def _record_qb_usage(user_id: int) -> None:
    """Record a successful QB tool creation for rate limiting."""
    if user_id == _QB_ADMIN_USER_ID:
        return
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    entry = _qb_rate.get(user_id, {"last_ts": 0.0, "day": today, "count": 0})
    if entry["day"] != today:
        entry = {"last_ts": 0.0, "day": today, "count": 0}
    entry["last_ts"] = now.timestamp()
    entry["count"] += 1
    _qb_rate[user_id] = entry


# ---------------------------------------------------------------------------
# Prompt logging — record every user message for daily report
# ---------------------------------------------------------------------------

_PROMPT_LOG_FILE = _Path(__file__).resolve().parent.parent / "prompt_log.jsonl"


def _log_prompt(user_id: int, username: str, first_name: str, message: str) -> None:
    """Append a prompt entry to the daily log file."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "message": message[:500],
    }
    try:
        with _PROMPT_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(_json_mod.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.error("Failed to log prompt: %s", exc)


# ---------------------------------------------------------------------------
# Collapsible output helpers
# ---------------------------------------------------------------------------

_COLLAPSIBLE_THRESHOLD = 600  # characters — replies longer than this get a collapsible blockquote


def _is_html(text: str) -> bool:
    """Return True if *text* already contains Telegram-supported HTML tags."""
    return bool(_re.search(r"<(?:b|i|s|u|a |code|blockquote|pre|em|strong|tg-spoiler)\b", text))


def _md_to_telegram_html(text: str) -> str:
    """Convert common Markdown from LLM replies to Telegram-safe HTML.

    If the text already looks like HTML it is returned almost untouched
    (only bare ``&``, ``<``, ``>`` outside existing tags get escaped).
    """
    if _is_html(text):
        # Already HTML — just make sure stray & < > outside tags are safe.
        # Replace & that are NOT part of &amp; &lt; &gt; &quot; &#…;
        text = _re.sub(r"&(?!amp;|lt;|gt;|quot;|#\d+;|#x[\da-fA-F]+;)", "&amp;", text)
        return text

    # Plain / Markdown text — escape HTML-special chars first
    text = _html.escape(text)

    # Code blocks  ```…```  →  <pre>…</pre>
    text = _re.sub(r"```(?:\w*\n)?(.*?)```", r"<pre>\1</pre>", text, flags=_re.S)
    # Inline code  `…`  →  <code>…</code>
    text = _re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Bold  **…**  →  <b>…</b>
    text = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Italic  *…*  →  <i>…</i>  (but not inside <b>)
    text = _re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    # Strikethrough  ~~…~~  →  <s>…</s>
    text = _re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    # Links  [text](url)  →  <a href="url">text</a>
    text = _re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    # Headers  ### … / ## … / # …  →  <b>…</b>
    text = _re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=_re.M)
    # Bullet points  - item  →  • item
    text = _re.sub(r"^[-*]\s+", "• ", text, flags=_re.M)
    # Numbered lists: keep as-is
    # Horizontal rules  ---  →  ———
    text = _re.sub(r"^-{3,}$", "———", text, flags=_re.M)

    # Strip stray unpaired Markdown markers that survived the conversion above.
    # These often arrive from LLMs that emit a lone "*" or "_" mid-sentence and
    # cause Telegram parse errors / ugly rendering. We only touch chars that are
    # NOT inside an HTML tag, code span, or pre block.
    text = _strip_stray_md_markers(text)

    return text


_HTML_TAG_OR_ENTITY = _re.compile(r"<[^>]+>|&[#\w]+;|<pre>.*?</pre>|<code>.*?</code>", _re.S)


def _strip_stray_md_markers(html_text: str) -> str:
    """Remove unpaired ``*`` / ``_`` from text segments outside HTML tags/code.

    Asterisks and underscores that survive markdown conversion are almost
    always parse-mode noise that broke pairing rules. Removing them prevents
    Telegram "can't parse entities" errors and ugly raw markers in output.
    """
    # Split into tag / entity / non-tag segments
    out: list[str] = []
    pos = 0
    # Match <pre>…</pre> and <code>…</code> as whole blocks (don't touch their contents)
    block_re = _re.compile(r"<(pre|code)\b[^>]*>.*?</\1>", _re.S)
    # First pull out pre/code blocks
    parts: list[tuple[str, str]] = []  # list of (kind, text)
    last = 0
    for m in block_re.finditer(html_text):
        if m.start() > last:
            parts.append(("text", html_text[last:m.start()]))
        parts.append(("block", m.group(0)))
        last = m.end()
    if last < len(html_text):
        parts.append(("text", html_text[last:]))

    cleaned: list[str] = []
    for kind, segment in parts:
        if kind == "block":
            cleaned.append(segment)
            continue
        # Within text segments, walk tag-by-tag and only sanitize bare text
        chunks: list[str] = []
        i = 0
        for tm in _re.finditer(r"<[^>]+>|&[#\w]+;", segment):
            if tm.start() > i:
                chunks.append(_strip_unpaired(segment[i:tm.start()]))
            chunks.append(tm.group(0))
            i = tm.end()
        if i < len(segment):
            chunks.append(_strip_unpaired(segment[i:]))
        cleaned.append("".join(chunks))
    return "".join(cleaned)


def _strip_unpaired(s: str) -> str:
    """Remove any remaining ``*`` characters from plain text.

    Asterisks that survive markdown conversion are noise (LLMs love to
    emit a lone ``*`` mid-sentence). Underscores are left alone because
    they are common in usernames and identifiers and are not special in
    Telegram HTML mode.
    """
    if not s:
        return s
    return s.replace("*", "")


async def _send_collapsed(send_fn, text: str, **kwargs) -> None:
    """Send text via send_fn, collapsing into expandable blockquote if long."""
    if len(text) > _COLLAPSIBLE_THRESHOLD:
        if _is_html(text):
            collapsed = text.replace("<blockquote>", "<blockquote expandable>")
        else:
            collapsed = (
                "<blockquote expandable>"
                + _md_to_telegram_html(text)
                + "</blockquote>"
            )
        try:
            await send_fn(collapsed, parse_mode="HTML", disable_web_page_preview=True, **kwargs)
            return
        except Exception:
            pass
    # Short text or fallback — still try HTML conversion
    try:
        html_text = _md_to_telegram_html(text) if not _is_html(text) else text
        await send_fn(html_text, parse_mode="HTML", disable_web_page_preview=True, **kwargs)
    except Exception:
        try:
            await send_fn(text, disable_web_page_preview=True, **kwargs)
        except Exception:
            await send_fn(text, **kwargs)


def _format_tool_reply(tool_name: str, result: str) -> str:
    """Wrap a tool result in a nice HTML format."""
    is_html = bool(_re.search(r"<(?:b|i|s|a |code|blockquote|pre|u|em|strong)\b", result))
    long = len(result) > _COLLAPSIBLE_THRESHOLD

    if is_html:
        if long:
            return result.replace("<blockquote>", "<blockquote expandable>")
        return result.replace("<blockquote expandable>", "<blockquote>")

    name_display = tool_name.replace("_", " ").title()
    escaped = _html.escape(result)
    if long:
        return (
            f"🤖 <b>{_html.escape(name_display)}</b>\n\n"
            f"<blockquote expandable>{escaped}</blockquote>"
        )
    return (
        f"🤖 <b>{_html.escape(name_display)}</b>\n\n"
        f"{escaped}"
    )


async def _send_tool_result(send_fn, tool_name: str, result: str, chat=None):
    """Send tool result with nice formatting, falling back to plain text.
    Returns the sent Message object (or None).
    If chat is provided and result is an audio/image/video file marker, sends media instead."""
    # Handle audio file responses (e.g. Spotify preview)
    if result.startswith("__AUDIO_FILE__:") and chat:
        parts = result.split(":", 2)
        if len(parts) >= 3:
            _, file_path, title = parts
            import os as _audio_os
            try:
                with open(file_path, "rb") as af:
                    sent = await chat.send_audio(audio=af, title=title, caption=f"🎵 {title}", parse_mode="HTML")
                try:
                    _audio_os.unlink(file_path)
                except Exception:
                    pass
                return sent
            except Exception as exc:
                logger.error("Failed to send audio file: %s", exc)
                try:
                    _audio_os.unlink(file_path)
                except Exception:
                    pass

    # Handle image file responses (e.g. media_creator)
    if result.startswith("__IMAGE_FILE__:") and chat:
        parts = result.split(":", 2)
        if len(parts) >= 3:
            _, file_path, caption = parts
            import os as _img_os
            try:
                with open(file_path, "rb") as imgf:
                    sent = await chat.send_photo(photo=imgf, caption=f"🎨 {caption}")
                try:
                    _img_os.unlink(file_path)
                except Exception:
                    pass
                return sent
            except Exception as exc:
                logger.error("Failed to send image file: %s", exc)
                try:
                    _img_os.unlink(file_path)
                except Exception:
                    pass

    # Handle video file responses (e.g. media_creator sora)
    if result.startswith("__VIDEO_FILE__:") and chat:
        parts = result.split(":", 2)
        if len(parts) >= 3:
            _, file_path, caption = parts
            import os as _vid_os
            try:
                with open(file_path, "rb") as vidf:
                    sent = await chat.send_video(video=vidf, caption=f"🎬 {caption}")
                try:
                    _vid_os.unlink(file_path)
                except Exception:
                    pass
                return sent
            except Exception as exc:
                logger.error("Failed to send video file: %s", exc)
                try:
                    _vid_os.unlink(file_path)
                except Exception:
                    pass

    formatted = _format_tool_reply(tool_name, result)
    try:
        return await send_fn(formatted, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        import re as _re2
        plain = _re2.sub(r"<[^>]+>", "", formatted)
        try:
            return await send_fn(plain, disable_web_page_preview=True)
        except Exception:
            return await send_fn(result)


# ---------------------------------------------------------------------------
# Output safety + table formatting
# ---------------------------------------------------------------------------

# Characters that frequently break Telegram Markdown parsing when they appear
# unescaped inside user-controlled content (usernames, IDs in code spans, etc.).
_MD_SPECIAL_CHARS = "*_~`"


def sanitize_md(text: str) -> str:
    """Remove Markdown-special chars (``*_~```) from a string.

    Use this on any string going into Telegram with ``parse_mode=None``
    when you want to guarantee no stray formatting markers appear.
    """
    if not text:
        return ""
    return text.translate(str.maketrans("", "", _MD_SPECIAL_CHARS))


def contains_md_chars(text: str) -> bool:
    """Return True if *text* contains Markdown-special chars."""
    return any(c in text for c in _MD_SPECIAL_CHARS)


def format_table(
    rows: list[list],
    headers: list | None = None,
    max_col_width: int = 40,
) -> str:
    """Format ``rows`` as a fixed-width plain-text table.

    Suitable for wrapping in ``<pre>...</pre>`` for monospace rendering in
    Telegram HTML mode. Does not include any HTML wrapping itself.
    """
    if not rows and not headers:
        return ""
    body = [[str(c) if c is not None else "" for c in r] for r in rows]
    head = [str(h) for h in headers] if headers else None
    # Truncate over-wide cells
    def _trunc(s: str) -> str:
        return s if len(s) <= max_col_width else s[: max_col_width - 1] + "…"
    body = [[_trunc(c) for c in r] for r in body]
    if head:
        head = [_trunc(h) for h in head]
    n_cols = len(head) if head else len(body[0])
    widths = [0] * n_cols
    for r in ([head] if head else []) + body:
        for i, c in enumerate(r):
            if len(c) > widths[i]:
                widths[i] = len(c)
    def _fmt(row):
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(row))
    out: list[str] = []
    if head:
        out.append(_fmt(head))
        out.append("  ".join("-" * w for w in widths))
    for r in body:
        out.append(_fmt(r))
    return "\n".join(out)


async def safe_reply(message, text: str, *, parse_mode: str | None = "HTML", **kwargs):
    """Reply to *message* with robust fallback.

    Strategy:
      1. Try with the requested ``parse_mode`` (default HTML).
      2. On any error (typically ``BadRequest: can't parse entities``)
         strip HTML tags + Markdown-special chars and send as plain text.

    Always returns the sent ``Message`` (or ``None`` if both attempts fail).
    """
    try:
        return await message.reply_text(text, parse_mode=parse_mode, **kwargs)
    except Exception as exc:
        logger.warning(
            "safe_reply: parse_mode=%s failed (%s); retrying as plain text",
            parse_mode, exc,
        )
        plain = _re.sub(r"<[^>]+>", "", text)
        plain = sanitize_md(plain)
        try:
            kwargs.pop("parse_mode", None)
            return await message.reply_text(plain, **kwargs)
        except Exception as exc2:
            logger.error("safe_reply: plain-text fallback also failed: %s", exc2)
            return None


async def _stream_tool_to_msg(thinking_msg, tool_name: str, result: str, chat=None):
    """Stream tool result into existing thinking_msg by progressive edits.
    Returns the thinking_msg (now containing the final text) or a new Message."""
    # Media files — can't stream into a message, send separately
    if result.startswith(("__AUDIO_FILE__:", "__IMAGE_FILE__:", "__VIDEO_FILE__:")) and chat:
        try:
            await thinking_msg.delete()
        except Exception:
            pass
        return await _send_tool_result(chat.send_message, tool_name, result, chat=chat)

    formatted = _format_tool_reply(tool_name, result)

    # For short results, just edit once
    if len(formatted) < 400:
        try:
            await thinking_msg.edit_text(formatted, parse_mode="HTML", disable_web_page_preview=True)
            return thinking_msg
        except Exception:
            pass
        # Fallback: delete and send fresh
        try:
            await thinking_msg.delete()
        except Exception:
            pass
        return await _send_tool_result(chat.send_message if chat else thinking_msg.reply_text, tool_name, result, chat=chat)

    # Progressive reveal: show text in 3-4 increments
    # Work with the plain text for chunking, then format at the end
    is_result_html = _is_html(result)
    if is_result_html:
        # For HTML results, we can't easily chunk mid-tag
        # Show 25%, 50%, 75%, 100%
        steps = [0.25, 0.5, 0.75, 1.0]
        for frac in steps:
            end = int(len(formatted) * frac)
            # Avoid cutting inside HTML tags — find a safe cut point
            chunk = formatted[:end]
            # Close any open tags crudely
            open_tags = _re.findall(r"<(b|i|u|s|a|code|pre|blockquote|em|strong|tg-spoiler)\b", chunk)
            close_tags = _re.findall(r"</(b|i|u|s|a|code|pre|blockquote|em|strong|tg-spoiler)>", chunk)
            # Count mismatches
            from collections import Counter
            opened = Counter(open_tags)
            closed = Counter(close_tags)
            suffix = ""
            for tag in reversed(open_tags):
                if opened[tag] > closed.get(tag, 0):
                    suffix += f"</{tag}>"
                    opened[tag] -= 1
            display = chunk + suffix
            if frac < 1.0:
                display += " ▍"
            try:
                await thinking_msg.edit_text(display, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                pass
            if frac < 1.0:
                await _asyncio.sleep(0.6)
        return thinking_msg
    else:
        # Plain text result — simpler chunking
        steps = [0.3, 0.6, 1.0]
        for frac in steps:
            end = int(len(formatted) * frac)
            display = formatted[:end]
            if frac < 1.0:
                display += " ▍"
            try:
                await thinking_msg.edit_text(display, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                try:
                    await thinking_msg.edit_text(display, disable_web_page_preview=True)
                except Exception:
                    pass
            if frac < 1.0:
                await _asyncio.sleep(0.6)
        return thinking_msg


def _track_tool_message(context, sent_msg, tool_name: str, run_context: dict) -> None:
    """Store which tool produced a message so replies can re-dispatch."""
    if not sent_msg or not hasattr(sent_msg, "message_id"):
        return
    history = context.user_data.setdefault("_tool_messages", {})
    # Keep last 20 to avoid unbounded growth
    if len(history) > 20:
        oldest = sorted(history)[:len(history) - 20]
        for k in oldest:
            history.pop(k, None)
    history[sent_msg.message_id] = {
        "tool_name": tool_name,
        "run_context": run_context,
    }
