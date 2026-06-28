"""Global output safety wrappers for outbound Telegram messages.

Monkey-patches ``telegram.Bot.send_message`` and ``telegram.Message.reply_text``
(plus ``edit_message_text`` / ``Message.edit_text``) so every outbound text
passes through :func:`sanitize_outbound` before reaching Telegram.

Goals:
  - Strip stray ``*`` chars that LLMs commonly leak in HTML / no-parse-mode
    messages (the most common cause of ``can't parse entities`` errors and
    ugly raw asterisks in output).
  - Preserve content inside ``<pre>`` / ``<code>`` blocks (code, tables).
  - Be a no-op when ``parse_mode='MarkdownV2'`` or ``'Markdown'`` is in
    use, because asterisks are meaningful syntax there.

Install once at bot startup via :func:`install`.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

_installed = False


def sanitize_outbound(text: Any, parse_mode: str | None) -> Any:
    """Strip stray ``*`` chars from *text* unless Markdown mode is requested.

    For HTML / plain text we strip asterisks outside ``<pre>`` / ``<code>``
    blocks. For Markdown / MarkdownV2 we leave the text untouched (asterisks
    are syntax).
    """
    if not isinstance(text, str) or not text:
        return text
    if parse_mode and "markdown" in parse_mode.lower():
        return text  # asterisks are syntax in markdown modes
    # Lazy import to avoid circular dependency at module load.
    from handlers.utils import _strip_stray_md_markers
    return _strip_stray_md_markers(text)


def _wrap(send_fn: Callable, text_arg_name: str = "text") -> Callable:
    async def _wrapped(*args: Any, **kwargs: Any):
        if text_arg_name in kwargs:
            kwargs[text_arg_name] = sanitize_outbound(
                kwargs[text_arg_name], kwargs.get("parse_mode")
            )
        else:
            # text is positional. Bot.send_message(chat_id, text, ...)
            # Message.reply_text(text, ...) — first positional after self.
            # We've already lost ``self`` because we monkey-patch the bound
            # descriptor at class level. So positional layout depends on which
            # method we wrap (handled by the install function passing the
            # correct text_arg_name and knowing the positional index).
            pass
        return await send_fn(*args, **kwargs)
    _wrapped.__wrapped__ = send_fn  # type: ignore[attr-defined]
    return _wrapped


def install() -> None:
    """Install monkey-patches. Safe to call multiple times."""
    global _installed
    if _installed:
        return

    try:
        from telegram import Bot, Message
    except Exception as exc:
        logger.warning("output_safety: cannot install (telegram not importable): %s", exc)
        return

    # Wrap Bot.send_message: signature (self, chat_id, text, ...)
    _orig_send = Bot.send_message
    async def _safe_send_message(self, chat_id, text=None, *args, **kwargs):
        if text is not None:
            text = sanitize_outbound(text, kwargs.get("parse_mode"))
        return await _orig_send(self, chat_id, text, *args, **kwargs)
    Bot.send_message = _safe_send_message  # type: ignore[assignment]

    # Wrap Bot.edit_message_text: signature (self, text, ...)
    _orig_edit = Bot.edit_message_text
    async def _safe_edit_message_text(self, text=None, *args, **kwargs):
        if text is not None:
            text = sanitize_outbound(text, kwargs.get("parse_mode"))
        return await _orig_edit(self, text, *args, **kwargs)
    Bot.edit_message_text = _safe_edit_message_text  # type: ignore[assignment]

    # Message.reply_text and Message.edit_text are convenience wrappers
    # that internally call bot.send_message / bot.edit_message_text, so they
    # benefit automatically. We don't need to wrap them.

    _installed = True
    logger.info("output_safety: installed (sanitize_outbound active on send_message / edit_message_text)")
