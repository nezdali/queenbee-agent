async def run(context: dict) -> str:
    import aiohttp
    import html
    import json as _json
    import hashlib
    import random
    import re
    from pathlib import Path as _Path
    from bs4 import BeautifulSoup
    
    try:
        args = context.get("args", []) or []
        # Prefer untranslated raw_extra: anekdot.ru search requires Russian text
        raw_extra = context.get("raw_extra", "") or ""
        query = raw_extra.strip() if raw_extra else " ".join(args).strip()

        # Clean up the query to extract the actual topic
        # First try to extract topic directly from structured patterns:
        # "анекдот про <topic>" or "anekdot about <topic>"
        topic_query = query
        topic_extracted = False
        # Match "анекдот(ы) про/о/об <topic>" or "anekdot/joke(s)/anecdote about <topic>"
        m = re.search(r"(?:анекдот[ыа]?\s+(?:про|о|об)\s+|(?:anekdot|jokes?|anecdotes?)\s+about\s+)(.+)",
                       query, re.IGNORECASE)
        if m:
            topic_query = m.group(1).strip(" ,.")
            topic_extracted = True

        if not topic_extracted:
            # Fallback: strip noise words one by one
            for noise in ["давай", "расскажи", "покажи", "найди", "хочу", "ещё", "еще",
                           "let's", "lets", "let", "have", "talk", "about", "tell", "me", "give",
                           "show", "find", "want", "more", "another", "one", "please", "some"]:
                topic_query = re.sub(rf"\b{noise}\b", "", topic_query, flags=re.IGNORECASE)
            topic_query = re.sub(r"\b(анекдот[ыа]?|шутк[уа]|anekdot|jokes?|anecdotes?)\b", "", topic_query, flags=re.IGNORECASE)
            topic_query = re.sub(r"\b(про|на тему|о|об)\b", "", topic_query, flags=re.IGNORECASE)
            topic_query = topic_query.strip(" ,.")

        # Build a nice display title
        if topic_query:
            topic_title = f"Анекдот про {topic_query.capitalize()}"
        else:
            topic_title = "Анекдот"

        # Use topic_query for search (if non-empty), else use original query
        search_query = topic_query if topic_query else query

        # --- Seen-jokes cache (per-topic, keeps last 50 hashes) ---
        _CACHE_FILE = _Path(__file__).resolve().parent / "anekdot_seen.json"
        _MAX_SEEN = 50

        def _load_seen() -> dict:
            try:
                return _json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                return {}

        def _save_seen(data: dict) -> None:
            try:
                _CACHE_FILE.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

        def _joke_hash(text: str) -> str:
            return hashlib.md5(text.strip().lower().encode()).hexdigest()[:12]

        def _pick_unseen(jokes: list[str], topic_key: str) -> str | None:
            """Pick a random unseen joke for this topic. If all seen, pick least-recently-seen."""
            if not jokes:
                return None
            seen_data = _load_seen()
            seen_list = seen_data.get(topic_key, [])
            seen_hashes = set(seen_list)
            unseen = [j for j in jokes if _joke_hash(j) not in seen_hashes]
            if unseen:
                pick = random.choice(unseen)
            else:
                # All seen — pick the one whose hash appears earliest (least recently seen)
                hash_order = {h: i for i, h in enumerate(seen_list)}
                jokes_sorted = sorted(jokes, key=lambda j: hash_order.get(_joke_hash(j), 0))
                pick = jokes_sorted[0]
                # Remove it from seen so it rotates
                seen_list = [h for h in seen_list if h != _joke_hash(pick)]
            # Mark as seen
            seen_list.append(_joke_hash(pick))
            seen_data[topic_key] = seen_list[-_MAX_SEEN:]
            _save_seen(seen_data)
            return pick
        headers = {
            "User-Tool": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        }

        def _bq(text: str) -> str:
            """Wrap in expandable blockquote only if text is long."""
            tag = "blockquote expandable" if len(text) > 600 else "blockquote"
            return f"<{tag}>{html.escape(text)}</{tag.split()[0]}>"

        def clean_text(text: str) -> str:
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            text = re.sub(r"\n{3,}", "\n\n", text)       # 3+ newlines → paragraph break
            text = re.sub(r"(?<!\n)\n(?!\n)", " ", text) # single newline → space
            text = re.sub(r"[ \t]{2,}", " ", text)       # multiple spaces → one
            text = re.sub(r"``([^`]*)``", r"«\1»", text)  # ``word`` → «word»
            return text.strip()

        def extract_jokes_from_html(html: str):
            soup = BeautifulSoup(html, "html.parser")
            jokes = []

            selectors = [
                ".topicbox .text",
                ".topicbox .topicbox-content",
                ".content .text",
                ".text",
                ".topicbox"
            ]

            seen = set()
            for sel in selectors:
                for node in soup.select(sel):
                    text = node.get_text("\n", strip=True)
                    text = clean_text(text)
                    if len(text) < 40 or len(text) > 500:
                        continue
                    low = text.lower()
                    if "анекдоты" in low and len(text) < 120:
                        continue
                    if text not in seen:
                        seen.add(text)
                        jokes.append(text)
                if jokes:
                    break
            return jokes

        async with aiohttp.ClientSession(headers=headers) as session:
            if search_query:
                search_urls = [
                    f"https://www.anekdot.ru/search/?query={aiohttp.helpers.quote(search_query, safe='')}",
                    f"https://www.anekdot.ru/search/?query={aiohttp.helpers.quote(search_query, safe='')}&page=2",
                    f"https://www.anekdot.ru/search/all/?query={aiohttp.helpers.quote(search_query, safe='')}",
                ]

                collected = []
                for url in search_urls:
                    try:
                        async with session.get(url, timeout=20) as resp:
                            if resp.status != 200:
                                continue
                            page_html = await resp.text()
                            jokes = extract_jokes_from_html(page_html)
                            for j in jokes:
                                if search_query.lower() in j.lower() or not collected:
                                    collected.append(j)
                    except Exception:
                        continue

                unique = []
                seen = set()
                for j in collected:
                    if j not in seen:
                        seen.add(j)
                        unique.append(j)

                if unique:
                    topic_key = search_query.lower().strip() or "_random"
                    pick = _pick_unseen(unique, topic_key)
                    if pick:
                        return (
                            f"🎭 <b>{html.escape(topic_title)}</b>\n\n"
                            f"{_bq(pick)}"
                        )

                try:
                    async with session.get("https://www.anekdot.ru/random/anekdot/", timeout=20) as resp:
                        if resp.status == 200:
                            page_html = await resp.text()
                            jokes = extract_jokes_from_html(page_html)
                            filtered = [j for j in jokes if search_query.lower() in j.lower()]
                            if filtered:
                                fallback_key = search_query.lower().strip() or "_random"
                                fb_pick = _pick_unseen(filtered, fallback_key)
                                if fb_pick:
                                    return (
                                        f"🎭 <b>{html.escape(topic_title)}</b>\n\n"
                                        f"{_bq(fb_pick)}"
                                )
                except Exception:
                    pass

                return (
                    f"😕 Не нашёл анекдот по теме <b>{html.escape(topic_title)}</b>.\n"
                    f"Попробуй: <i>чапаев</i>, <i>вовочка</i>, <i>программисты</i>"
                )

            random_urls = [
                "https://www.anekdot.ru/random/anekdot/",
                "https://www.anekdot.ru/last/anekdot/"
            ]
            all_jokes = []
            for url in random_urls:
                try:
                    async with session.get(url, timeout=20) as resp:
                        if resp.status != 200:
                            continue
                        page_html = await resp.text()
                        all_jokes.extend(extract_jokes_from_html(page_html))
                except Exception:
                    continue

            unique = []
            seen = set()
            for j in all_jokes:
                if j not in seen:
                    seen.add(j)
                    unique.append(j)

            if unique:
                rand_pick = _pick_unseen(unique, "_random")
                if rand_pick:
                    return (
                        f"🎲 <b>Случайный анекдот</b>\n\n"
                        f"{_bq(rand_pick)}"
                    )

            return "😔 Не удалось стянуть анекдот с anekdot.ru. Попробуй ещё раз чуть позже."
    except Exception as e:
        return f"Ошибка при получении анекдота: {str(e)}"