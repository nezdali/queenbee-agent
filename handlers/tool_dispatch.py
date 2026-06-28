"""
Tool dispatch: intent detection, keyword matching, query translation.
"""

import json as _json
import logging
import random as _random
import re as _re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thinking phrases shown while processing
# ---------------------------------------------------------------------------

_THINKING_PHRASES = [
    "Пойду подумаю...",
    "Интересно, чтобы это могло быть...",
    "Ну и запросики у вас...",
    "Бот чешет репу...",
    "Пойду пораскину мозгами...",
    "Любопытно, что бы это значило...",
    "Ну и задачки вы подкидываете...",
    "Бот завис в раздумьях...",
    "Сейчас шестерёнки заскрипят...",
    "Хм, это уже интересно...",
    "Надо это хорошенько обмозговать...",
    "Ого, вот это запрос...",
    "Так, дайте секунду на подумать...",
    "Бот ушёл в мыслительный процесс...",
    "Секунду, поймаю мысль...",
    "Пойду сделаю вид, что сразу понял...",
    "Ну да, конечно, элементарней некуда...",
    "Вот это вы, конечно, завернули...",
    "Сейчас быстренько прикинусь умным...",
    "Запрос без подвоха, ага...",
    "Секунду, включу режим «я всё знаю»...",
    "Ну и фантазия у вас...",
    "Так, а теперь — серьёзно попытаюсь понять...",
    "Бот, конечно, не в шоке, но близко...",
    "О, опять что-то подозрительно интересное...",
    "Сейчас разложу это по полочкам, если полочки выдержат...",
    "Ничего себе, вот это поворот мысли...",
    "Ладно, убедили, придётся подумать...",
    "Секундочку, мой сарказмометр зашкалил...",
    "Да-да, именно этого мне сегодня не хватало...",
    "Вот это вы завернули...",
    "Мозговой модуль в работе...",
    "Интригующий поворот...",
    "Сейчас разберёмся, что к чему...",
]

_THINKING_PHRASES_SMART = [
    "Анализирую запрос...",
    "Обрабатываю данные...",
    "Формирую ответ...",
    "Провожу анализ...",
    "Систематизирую информацию...",
    "Выстраиваю логическую цепочку...",
    "Структурирую данные...",
    "Оцениваю варианты...",
    "Подбираю оптимальное решение...",
    "Верифицирую данные...",
    "Работаю над запросом...",
    "Обрабатываю...",
    "Генерирую ответ...",
    "Выполняю запрос...",
    "Ищу решение...",
]

VIBE_PHRASES = {
    "funny": _THINKING_PHRASES,
    "smart": _THINKING_PHRASES_SMART,
}

DEFAULT_VIBE = "funny"

# ---------------------------------------------------------------------------
# LLM-based tool routing
# ---------------------------------------------------------------------------

_TOOL_ROUTER_PROMPT = """\
You route user messages to the right tool. Available tools:

{tool_list}

If the user message clearly asks for something a tool can do, respond with JSON:
{{"tool": "<tool_name>", "query": "<extracted search query>"}}

If no tool matches, respond with:
{{"tool": null}}

Rules:
- Extract only the product/search query, not the instruction (e.g. "show me prices of samsung galaxy s26 on amazon" → query: "samsung galaxy s26")
- Be generous with matching — if the user mentions shopping/prices/buying, route to the shopping or amazon tool
- Only return JSON, no other text
"""


async def _detect_tool_intent(message: str, tools) -> tuple[str, str] | None:
    """Use LLM to detect if a message should be routed to a tool.
    Returns (tool_name, query_string) or None."""
    from services.llm_service import client
    from config import LLM_MODEL

    tool_lines = []
    for a in tools:
        if a.status != "approved":
            continue
        tool_lines.append(f"- {a.name}: {a.description} (keywords: {', '.join(a.trigger_keywords)})")

    if not tool_lines:
        return None

    prompt = _TOOL_ROUTER_PROMPT.format(tool_list="\n".join(tool_lines))

    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": message},
            ],
            response_format={"type": "json_object"},
        )
        result = _json.loads(response.choices[0].message.content or "{}")
    except Exception as e:
        logger.debug("Tool intent detection failed: %s", e)
        return None

    tool_name = result.get("tool")
    if not tool_name:
        return None

    query = result.get("query", "").strip()
    return (tool_name, query)


# ---------------------------------------------------------------------------
# Smart tool keyword matching (no LLM, fast)
# ---------------------------------------------------------------------------

def _detect_tool_keyword_match(message: str, tools):
    """Scan all words in the message against tool trigger_keywords.
    Returns (tool_meta, matched_keyword, remaining_query) or None.
    Skips first-word matches (those are handled by direct dispatch).
    When multiple tools match, prefers the one whose name contains the matched keyword."""
    words = set(message.lower().split())
    first_word = message.split()[0].lower() if message.split() else ""

    candidates = []  # list of (tool, matched_kw)
    for tool in tools:
        if tool.status != "approved":
            continue
        for kw in tool.trigger_keywords:
            kw_lower = kw.lower()
            if kw_lower in words and kw_lower != first_word:
                candidates.append((tool, kw_lower))
                break  # one match per tool is enough

    if not candidates:
        return None

    # Prefer tool whose name contains the matched keyword (city match)
    best = candidates[0]
    for tool, kw in candidates:
        if kw in tool.name.lower():
            best = (tool, kw)
            break

    tool_meta, best_kw = best
    remaining = " ".join(w for w in message.split() if w.lower() != best_kw).strip()
    return (tool_meta, best_kw, remaining)


# ---------------------------------------------------------------------------
# Query translation: translate non-Latin tool queries to English
# ---------------------------------------------------------------------------

_NON_LATIN_RE = _re.compile(r"[^\x00-\x7F]")


async def _translate_query(text: str) -> str:
    """If text contains non-Latin characters, translate to English via LLM.
    Returns the translated text, or original on failure."""
    if not text or not _NON_LATIN_RE.search(text):
        return text
    from services.llm_service import client
    from config import LLM_MODEL
    try:
        resp = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": (
                    "Translate the user's search query to English. "
                    "Return ONLY the translated query, nothing else. "
                    "Keep brand names and product names as-is."
                )},
                {"role": "user", "content": text},
            ],
            max_completion_tokens=200,
        )
        translated = (resp.choices[0].message.content or "").strip()
        if translated:
            logger.info("Translated query: %r → %r", text, translated)
            return translated
    except Exception as exc:
        logger.warning("Query translation failed: %s", exc)
    return text
