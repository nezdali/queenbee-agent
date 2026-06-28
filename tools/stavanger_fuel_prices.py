async def run(context: dict) -> str:
    try:
        import aiohttp
        from bs4 import BeautifulSoup

        async def fetch_text(session, url):
            try:
                headers = {"User-Tool": "Mozilla/5.0"}
                async with session.get(url, headers=headers, timeout=20) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.text()
            except Exception:
                return None

        def parse_price_table(html):
            try:
                if not html:
                    return None
                soup = BeautifulSoup(html, "html.parser")
                tables = soup.find_all("table")
                for table in tables:
                    rows = table.find_all("tr")
                    price = None
                    updated = None
                    for tr in rows:
                        cells = tr.find_all(["th", "td"])
                        texts = [c.get_text(" ", strip=True) for c in cells]
                        if len(texts) >= 2:
                            key = texts[0].strip().lower()
                            val = texts[1].strip()
                            if key == "current price":
                                price = val
                            elif key == "last update":
                                updated = val
                    if price:
                        return {"price": price, "updated": updated}
                return None
            except Exception:
                return None

        async def get_price(session, fuel_type):
            try:
                if fuel_type == "diesel":
                    candidates = [
                        "https://www.globalpetrolprices.com/Norway/stavanger/diesel_prices/",
                        "https://www.globalpetrolprices.com/Norway/diesel_prices/"
                    ]
                    label = "Diesel"
                else:
                    candidates = [
                        "https://www.globalpetrolprices.com/Norway/stavanger/gasoline_prices/",
                        "https://www.globalpetrolprices.com/Norway/gasoline_prices/"
                    ]
                    label = "95 petrol"

                fallback_result = None
                for url in candidates:
                    html = await fetch_text(session, url)
                    parsed = parse_price_table(html)
                    if parsed:
                        if "/stavanger/" in url:
                            return {"scope": "Stavanger", "label": label, "price": parsed.get("price"), "updated": parsed.get("updated"), "url": url}
                        if not fallback_result:
                            fallback_result = {"scope": "Norway", "label": label, "price": parsed.get("price"), "updated": parsed.get("updated"), "url": url}
                return fallback_result
            except Exception:
                return None

        async with aiohttp.ClientSession() as session:
            diesel_result = await get_price(session, "diesel")
            petrol_result = await get_price(session, "petrol95")

        is_voice = bool(context.get("language"))
        if is_voice:
            import re as _re
            language = (context.get("language") or "").lower()
            is_ru = language.startswith("ru")

            def _short(price_str):
                if not price_str:
                    return None
                m = _re.search(r"([\d.,]+)", price_str)
                if not m:
                    return None
                num = m.group(1).replace(",", ".")
                try:
                    val = float(num)
                except ValueError:
                    return None
                return f"{val:.2f}".replace(".", ",") if is_ru else f"{val:.2f}"

            v_parts = []
            d_short = _short((diesel_result or {}).get("price"))
            p_short = _short((petrol_result or {}).get("price"))
            if p_short:
                v_parts.append(("девяносто пятый " if is_ru else "Euro 95 ") + p_short)
            if d_short:
                v_parts.append(("дизель " if is_ru else "diesel ") + d_short)
            if not v_parts:
                return "Не удалось получить цены." if is_ru else "Could not fetch fuel prices."
            prefix = "В Ставангере: " if is_ru else "Stavanger: "
            return prefix + ", ".join(v_parts) + "."

        lines = []

        if diesel_result:
            scope = diesel_result.get("scope", "Norway")
            price = diesel_result.get("price") or "unknown"
            updated = diesel_result.get("updated") or "unknown date"
            if scope == "Stavanger":
                lines.append(f"Diesel price in Stavanger: {price} (Last update: {updated})")
            else:
                lines.append(f"Diesel Stavanger-specific price not found, Norway average: {price} (Last update: {updated})")
        else:
            lines.append("Could not fetch diesel price right now.")

        if petrol_result:
            scope = petrol_result.get("scope", "Norway")
            price = petrol_result.get("price") or "unknown"
            updated = petrol_result.get("updated") or "unknown date"
            if scope == "Stavanger":
                lines.append(f"95 petrol price in Stavanger: {price} (Last update: {updated})")
            else:
                lines.append(f"95 petrol Stavanger-specific price not found, Norway average: {price} (Last update: {updated})")
        else:
            lines.append("Could not fetch 95 petrol price right now.")

        return "\n".join(lines)
    except Exception as e:
        return f"Sorry, something went wrong while fetching fuel prices: {str(e)}"