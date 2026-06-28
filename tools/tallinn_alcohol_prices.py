async def run(context: dict) -> str:
    """Fetch alcohol prices in Tallinn from Rimi.ee."""
    import aiohttp
    import asyncio
    import re
    import json as _json
    from html import escape as esc
    from bs4 import BeautifulSoup

    try:
        args = context.get("args", []) or []
        custom_query = " ".join(args).strip()

        # -- Russian slang → search term mapping --
        SLANG_MAP = {
            "вискарь": "whisky",
            "конь": "cognac",
            "водяра": "vodka",
            "пивас": "beer",
            "джинчик": "gin",
            "шампусик": "champagne",
            "валмиера": "valmiermuiza",
            "валмиермуйжа": "valmiermuiza",
            "тервете": "tervete õlu",
            "терветес": "tervete õlu",
            "ужавас": "uzavas",
            "valmiera": "valmiermuiza",
            "tervete": "tervete õlu",
        }
        # Brand names: search term → display label
        BRAND_MAP = {
            "valmiermuiza": "Valmiermuiža",
            "tervete õlu": "Tērvetes",
            "uzavas": "Užavas",
        }
        BEER_BRANDS = set(BRAND_MAP)
        # Translate slang, drop untranslated Cyrillic
        # Priority: if trigger_word is a known brand/slang, use it directly
        trigger = context.get("trigger_word", "").lower()
        raw_extra = context.get("raw_extra", "").lower()
        # Check raw_extra for brand/slang words (before LLM translation mangles them)
        raw_brand = None
        if raw_extra:
            for w in raw_extra.split():
                if w in SLANG_MAP:
                    raw_brand = SLANG_MAP[w]
                    break
        if trigger in SLANG_MAP:
            custom_query = SLANG_MAP[trigger]
        elif raw_brand:
            custom_query = raw_brand
        elif custom_query:
            words = custom_query.split()
            translated = [SLANG_MAP[w.lower()] for w in words if w.lower() in SLANG_MAP]
            kept = [w for w in words if w.lower() not in SLANG_MAP and w.isascii()]
            # If a brand/slang was found, use only that (drop noise like "how much")
            custom_query = " ".join(translated) if translated else " ".join(kept).strip()
        # Build display label: use brand name if known
        display_label = BRAND_MAP.get(custom_query.lower()) if custom_query else None
        if not display_label and custom_query:
            display_label = custom_query.title()

        DEFAULT_SEARCHES = [
            {"query": "whisky", "label": "Whisky"},
            {"query": "beer", "label": "Beer"},
            {"query": "cognac", "label": "Cognac"},
            {"query": "wine", "label": "Wine"},
        ]

        UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        RIMI_BASE = "https://www.rimi.ee"
        RIMI_SEARCH = RIMI_BASE + "/epood/ee/otsing"

        # -- Rimi.ee product search (HTML scraping with structured GTM data) --
        async def search_rimi(session, query, max_results=5):
            try:
                async with session.get(
                    RIMI_SEARCH,
                    params={"query": query, "pageSize": str(max_results), "category": "SH-1"},
                    headers={"User-Tool": UA},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                products = []
                for card in soup.select(".js-product-container"):
                    if len(products) >= max_results:
                        break
                    gtm_raw = card.get("data-gtm-eec-product", "")
                    if not gtm_raw:
                        continue
                    try:
                        gtm = _json.loads(gtm_raw)
                    except Exception:
                        continue
                    # Filter to alcohol category only (SH-1-*)
                    cat = gtm.get("category", "")
                    if not cat.startswith("SH-1"):
                        continue
                    link_el = card.select_one("a.card__url")
                    href = link_el["href"] if link_el and link_el.get("href") else ""
                    products.append({
                        "name": gtm.get("name", ""),
                        "price": gtm.get("price"),
                        "url": RIMI_BASE + href if href else "",
                    })
                return products
            except Exception:
                return []

        # -- Run all queries in parallel --
        async with aiohttp.ClientSession() as session:
            if custom_query:
                searches = [{"query": custom_query, "label": display_label or custom_query.title()}]
            else:
                searches = DEFAULT_SEARCHES

            tasks = [search_rimi(session, s["query"]) for s in searches]
            rimi_results = await asyncio.gather(*tasks)

            # Beer brand fallback: if brand not found, show other Estonian beers
            if custom_query and custom_query.lower() in BEER_BRANDS:
                if not rimi_results[0]:
                    rimi_results = [await search_rimi(session, "beer")]
                    searches[0]["label"] += " (not found — showing Estonian beer)"

        # -- Format output (HTML for clickable links) --
        lines = ["<b>Alcohol prices in Tallinn (Estonia)</b>\n"]

        for search, products in zip(searches, rimi_results):
            lines.append(f"<b>{esc(search['label'])}</b>")
            if products:
                for p in products:
                    name = esc(p["name"])
                    price = p["price"]
                    price_str = f"€{price:.2f}" if price else "N/A"
                    url = p["url"]
                    if url:
                        lines.append(f'  <a href="{url}">{name}</a> — {price_str}')
                    else:
                        lines.append(f"  {name} — {price_str}")
            else:
                lines.append("  No results on Rimi.ee")
            lines.append("")

        lines.append("Source: Rimi.ee (store prices)")
        if not custom_query:
            lines.append("Tip: <code>tallinn [search term]</code> to search specific products")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to fetch prices: {e}"
