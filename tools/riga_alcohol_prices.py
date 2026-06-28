async def run(context: dict) -> str:
    """Fetch alcohol prices in Riga from Rimi.lv + Barbora.lv."""
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
            "вискарь": "viskijs",
            "конь": "konjaks",
            "водяра": "degvins",
            "пивас": "alus",
            "джинчик": "gin",
            "шампусик": "šampanietis",
            "валмиера": "valmiermuiza",
            "валмиермуйжа": "valmiermuiza",
            "тервете": "tervete alus",
            "терветес": "tervete alus",
            "ужавас": "uzavas",
            "valmiera": "valmiermuiza",
            "tervete": "tervete alus",
        }
        # Brand names: search term → display label
        BRAND_MAP = {
            "valmiermuiza": "Valmiermuiža",
            "tervete alus": "Tērvetes",
            "uzavas": "Užavas",
        }
        BEER_BRANDS = set(BRAND_MAP)
        # English → Latvian search term mapping for Rimi.lv
        EN_TO_LV = {
            "whisky": "viskijs",
            "whiskey": "viskijs",
            "cognac": "konjaks",
            "vodka": "degvins",
            "beer": "alus",
            "wine": "vīns",
            "champagne": "šampanietis",
            "gin": "džins",
            "rum": "rums",
            "tequila": "tekila",
        }
        # Translate slang, drop untranslated Cyrillic, then map English → Latvian
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
        # Build display label: use brand name if it's a known brand, else reverse-map Latvian→English
        LV_TO_EN = {v: k.title() for k, v in EN_TO_LV.items()}
        display_label = BRAND_MAP.get(custom_query.lower()) if custom_query else None
        if not display_label and custom_query:
            parts = custom_query.split()
            display_label = " ".join(LV_TO_EN.get(p.lower(), p.title()) for p in parts)
        # Map remaining English terms to Latvian for better Rimi.lv results
        if custom_query:
            words = custom_query.split()
            custom_query = " ".join(EN_TO_LV.get(w.lower(), w) for w in words)

        DEFAULT_SEARCHES = [
            {"query": "viskijs", "label": "Whisky"},
            {"query": "alus", "label": "Beer"},
            {"query": "konjaks", "label": "Cognac"},
            {"query": "vīns", "label": "Wine"},
        ]

        UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        RIMI_BASE = "https://www.rimi.lv"
        RIMI_SEARCH = RIMI_BASE + "/e-veikals/lv/meklesana"

        # -- Rimi.lv product search (HTML scraping with structured GTM data) --
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
                    if not cat.startswith("SH-1-"):
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

        # -- Barbora.lv product search (HTML with window.b_productList JSON) --
        BARBORA_SEARCH = "https://barbora.lv/meklet"
        BARBORA_PRODUCT = "https://barbora.lv/produkti/"

        async def search_barbora(session, query, max_results=5):
            try:
                async with session.get(
                    BARBORA_SEARCH,
                    params={"q": query},
                    headers={"User-Tool": UA},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text()
                m = re.search(r'window\.b_productList\s*=\s*(\[.*?\]);\s*\n', html, re.DOTALL)
                if not m:
                    return []
                all_products = _json.loads(m.group(1))
                products = []
                for p in all_products:
                    if len(products) >= max_results:
                        break
                    # Filter to alcohol (Dzērieni/Stiprie or Dzērieni/Alus etc.)
                    cat = (p.get("category_name_full_path") or "").lower()
                    if not cat.startswith("dzērieni/"):
                        continue
                    units = p.get("units") or []
                    price = units[0]["price"] if units else p.get("price")
                    slug = p.get("Url") or ""
                    products.append({
                        "name": p.get("title", ""),
                        "price": price,
                        "url": BARBORA_PRODUCT + slug if slug else "",
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

            tasks = []
            for s in searches:
                tasks.append(search_rimi(session, s["query"]))
                tasks.append(search_barbora(session, s["query"]))
            results = await asyncio.gather(*tasks)

            # Beer brand fallback: if brand not found, show other Latvian beers
            if custom_query and custom_query.lower() in BEER_BRANDS:
                if not results[0] and not results[1]:
                    fallback = await asyncio.gather(
                        search_rimi(session, "alus"),
                        search_barbora(session, "alus"),
                    )
                    results = list(fallback)
                    searches[0]["label"] += " (not found — showing Latvian beer)"

            store_results = results

        # -- Brand filter: if a known brand was searched, keep only matching products --
        brand_filter = None
        if custom_query and custom_query.lower() in BRAND_MAP:
            brand_filter = BRAND_MAP[custom_query.lower()].lower()
        if brand_filter:
            def _matches_brand(p: dict) -> bool:
                n = (p.get("name") or "").lower()
                # Match on first stem (handles diacritics: Valmiermuiža → valmierm)
                stem = brand_filter[:6]
                return stem in n.replace("ž", "z").replace("ē", "e").replace("š", "s").replace("ū", "u").replace("ā", "a").replace("ī", "i")
            store_results = [
                [p for p in (lst or []) if _matches_brand(p)]
                for lst in store_results
            ]

        # -- Voice mode: return a single product + price line (no markup) --
        if context.get("language"):
            best = None
            for i, _s in enumerate(searches):
                for p in (store_results[i * 2] or []) + (store_results[i * 2 + 1] or []):
                    if p.get("price") is None:
                        continue
                    if best is None or p["price"] < best["price"]:
                        best = p
            if best is None:
                return "Ничего не найдено."
            lang_short = context["language"].split("-", 1)[0].lower()
            if lang_short == "ru":
                return f"{best['name']} — {best['price']:.2f} евро."
            return f"{best['name']} — €{best['price']:.2f}."

        # -- Format output (HTML for clickable links) --
        lines = ["<b>Alcohol prices in Riga (Latvia)</b>\n"]

        for i, search in enumerate(searches):
            rimi_products = store_results[i * 2]
            barbora_products = store_results[i * 2 + 1]

            lines.append(f"<b>{esc(search['label'])}</b>")

            if rimi_products:
                lines.append("  <i>Rimi.lv:</i>")
                for p in rimi_products:
                    name = esc(p["name"])
                    price = p["price"]
                    price_str = f"€{price:.2f}" if price else "N/A"
                    url = p["url"]
                    if url:
                        lines.append(f'    <a href="{url}">{name}</a> — {price_str}')
                    else:
                        lines.append(f"    {name} — {price_str}")
            else:
                lines.append("  <i>Rimi.lv:</i> no results")

            if barbora_products:
                lines.append("  <i>Barbora.lv:</i>")
                for p in barbora_products:
                    name = esc(p["name"])
                    price = p["price"]
                    price_str = f"€{price:.2f}" if price else "N/A"
                    url = p["url"]
                    if url:
                        lines.append(f'    <a href="{url}">{name}</a> — {price_str}')
                    else:
                        lines.append(f"    {name} — {price_str}")
            else:
                lines.append("  <i>Barbora.lv:</i> no results")

            lines.append("")

        lines.append("Source: Rimi.lv + Barbora.lv (store prices)")
        if not custom_query:
            lines.append("Tip: <code>riga [search term]</code> to search specific products")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to fetch prices: {e}"
