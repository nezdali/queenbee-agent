async def run(context: dict) -> str:
    """Fetch alcohol prices in Stavanger from Vinmonopolet JSON API + Numbeo."""
    import aiohttp
    import asyncio
    import re
    import json as _json
    from html import escape as esc
    from urllib.parse import quote
    from bs4 import BeautifulSoup

    try:
        args = context.get("args", []) or []
        custom_query = " ".join(args).strip()

        # -- Russian slang → search term mapping --
        SLANG_MAP = {
            "вискарь": "whisky",
            "конь": "cognac",
            "водяра": "vodka",
            "пивас": "pilsner",
            "джинчик": "gin",
            "шампусик": "champagne",
            "валмиера": "valmiermuiza",
            "терветес": "tervetes",
            "ужавас": "uzavas",
        }
        # Translate any slang words found in the query, drop untranslated Cyrillic
        if custom_query:
            words = custom_query.split()
            translated = [SLANG_MAP[w.lower()] for w in words if w.lower() in SLANG_MAP]
            kept = [w for w in words if w.lower() not in SLANG_MAP and w.isascii()]
            custom_query = " ".join(translated + kept).strip()
        # If no args but the trigger word is slang, use it as the query
        if not custom_query:
            trigger = context.get("trigger_word", "").lower()
            if trigger in SLANG_MAP:
                custom_query = SLANG_MAP[trigger]

        # -- Default product searches (can be overridden by user query) --
        DEFAULT_SEARCHES = [
            {"query": "glenmorangie", "label": "Glenmorangie (whisky)"},
            {"query": "pilsner", "label": "Beer / Pilsner"},
            {"query": "cognac", "label": "Cognac"},
            {"query": "wine red", "label": "Red wine"},
        ]

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
        }
        VMP_SEARCH = "https://www.vinmonopolet.no/vmpws/v2/vmp/products/search"
        VMP_BASE = "https://www.vinmonopolet.no"
        NUMBEO_URL = "https://www.numbeo.com/cost-of-living/in/Stavanger"
        ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

        # -- ECB NOK/EUR exchange rate --
        async def fetch_nok_eur_rate(session):
            try:
                async with session.get(ECB_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    xml = await resp.text()
                    m = re.search(r'currency=["\']NOK["\'] rate=["\']([0-9.]+)["\']', xml)
                    return float(m.group(1)) if m else None
            except Exception:
                return None

        # -- Vinmonopolet product search (JSON API, no key needed) --
        async def search_vinmonopolet(session, query, max_results=5):
            try:
                async with session.get(
                    VMP_SEARCH,
                    params={"q": query, "pageSize": str(max_results)},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = _json.loads(await resp.text())
                    products = []
                    for p in data.get("products", [])[:max_results]:
                        products.append({
                            "name": p.get("name", ""),
                            "price": p.get("price", {}).get("value"),
                            "price_fmt": p.get("price", {}).get("formattedValue", ""),
                            "volume": p.get("volume", {}).get("formattedValue", ""),
                            "category": p.get("main_category", {}).get("name", ""),
                            "url": VMP_BASE + p.get("url", ""),
                        })
                    return products
            except Exception:
                return []

        # -- Numbeo cost-of-living table --
        async def fetch_numbeo(session):
            result = {}
            try:
                async with session.get(
                    NUMBEO_URL,
                    headers={"User-Agent": headers["User-Agent"]},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return result
                    html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                for tr in soup.find_all("tr"):
                    cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                    if len(cells) < 2:
                        continue
                    label = cells[0].lower()
                    nums = re.findall(r"[\d,]+\.\d+", cells[1].replace("\xa0", ""))
                    val = float(nums[0].replace(",", "")) if nums else None
                    if not val:
                        continue
                    if "domestic" in label and "draft" in label and "beer" in label:
                        result["beer_restaurant"] = val
                    elif "domestic beer" in label and "0.5" in label:
                        result["beer_store"] = val
                    elif "imported beer" in label and "0.33" in label and "restaurant" not in label:
                        result.setdefault("beer_imported_store", val)
                    elif "imported beer" in label and "0.33" in label:
                        result.setdefault("beer_imported_restaurant", val)
                    elif "bottle of wine" in label and "mid" in label:
                        result["wine_mid"] = val
            except Exception:
                pass
            return result

        # -- Run all queries in parallel --
        async with aiohttp.ClientSession() as session:
            if custom_query:
                searches = [{"query": custom_query, "label": custom_query.title()}]
            else:
                searches = DEFAULT_SEARCHES

            tasks = [search_vinmonopolet(session, s["query"]) for s in searches]
            tasks.append(fetch_numbeo(session))
            tasks.append(fetch_nok_eur_rate(session))
            results = await asyncio.gather(*tasks)

            vmp_results = results[:-2]
            numbeo = results[-2]
            nok_eur = results[-1]  # NOK per 1 EUR

        def to_eur(nok_val):
            if nok_eur and nok_val:
                return f" (~€{nok_val / nok_eur:.2f})"
            return ""

        # -- Format output (HTML for clickable links) --
        rate_info = f" | 1 EUR = {nok_eur:.2f} NOK" if nok_eur else ""
        lines = [f"<b>Alcohol prices in Stavanger (Norway)</b>{rate_info}\n"]

        for search, products in zip(searches, vmp_results):
            lines.append(f"<b>{esc(search['label'])}</b>")
            if products:
                for p in products:
                    price_str = p["price_fmt"] or f"{p['price']:.2f} kr"
                    name = esc(p["name"])
                    vol = f" ({esc(p['volume'])})" if p["volume"] else ""
                    url = p["url"]
                    eur = to_eur(p["price"])
                    lines.append(f"  <a href=\"{url}\">{name}</a>{vol} — {esc(price_str)}{eur}")
            else:
                lines.append("  No results on Vinmonopolet")
            lines.append("")

        # Numbeo reference prices
        if numbeo:
            lines.append("<b>Numbeo reference (Stavanger avg)</b>")
            if numbeo.get("beer_restaurant"):
                lines.append(f"  Draft beer 0.5L (restaurant): {numbeo['beer_restaurant']:.0f} kr{to_eur(numbeo['beer_restaurant'])}")
            if numbeo.get("beer_store"):
                lines.append(f"  Domestic beer 0.5L (store): {numbeo['beer_store']:.0f} kr{to_eur(numbeo['beer_store'])}")
            if numbeo.get("beer_imported_store"):
                lines.append(f"  Imported beer 0.33L (store): {numbeo['beer_imported_store']:.0f} kr{to_eur(numbeo['beer_imported_store'])}")
            if numbeo.get("wine_mid"):
                lines.append(f"  Mid-range wine bottle (store): {numbeo['wine_mid']:.0f} kr{to_eur(numbeo['wine_mid'])}")
            lines.append("")

        lines.append("Source: Vinmonopolet (exact) + Numbeo (averages)")
        if not custom_query:
            lines.append("Tip: <code>stavanger [search term]</code> to search specific products")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to fetch prices: {e}"
