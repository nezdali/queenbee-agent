async def run(context: dict) -> str:
    try:
        import aiohttp
        import re
        from html import escape as esc

        args = context.get("args", []) or []
        is_voice = bool(context.get("language"))
        raw_extra_lower = (context.get("raw_extra") or "").lower()
        user_msg_lower = (context.get("user_message") or "").lower()
        haystack = " ".join([" ".join(str(a).lower() for a in args),
                             raw_extra_lower, user_msg_lower])

        # Parse country, fuel type filter, and tank size
        COUNTRIES = {
            "estonia": ("Estonia", "🇪🇪"),
            "eesti": ("Estonia", "🇪🇪"),
            "эстония": ("Estonia", "🇪🇪"),
            "эстони": ("Estonia", "🇪🇪"),
            "latvia": ("Latvia", "🇱🇻"),
            "latvija": ("Latvia", "🇱🇻"),
            "латвия": ("Latvia", "🇱🇻"),
            "латви": ("Latvia", "🇱🇻"),
            "рига": ("Latvia", "🇱🇻"),
            "риге": ("Latvia", "🇱🇻"),
            "риг": ("Latvia", "🇱🇻"),
            "riga": ("Latvia", "🇱🇻"),
        }
        country = "Estonia"
        flag = "🇪🇪"
        tank_liters = 70
        fuel_filter = None  # None = show both
        # Voice path: detect country via haystack substring (handles Russian cases)
        if is_voice:
            for stem, (cn, fl) in COUNTRIES.items():
                if stem in haystack:
                    country, flag = cn, fl
                    break
            if any(w in haystack for w in ("дизел", "diesel")):
                fuel_filter = "diesel"
            elif any(w in haystack for w in ("бензин", "95", "petrol", "gasoline")):
                fuel_filter = "e95"
        else:
            for a in args:
                s = str(a).lower()
                if s in COUNTRIES:
                    country, flag = COUNTRIES[s]
                elif s in ("diesel", "дизель"):
                    fuel_filter = "diesel"
                elif s in ("95", "e95", "euro95", "бензин", "gasoline", "petrol"):
                    fuel_filter = "e95"
                else:
                    try:
                        val = float(s.replace("l", "").replace("л", ""))
                        if 5 <= val <= 500:
                            tank_liters = val
                    except Exception:
                        pass

        URL = f"https://www.fuel-prices.eu/{country}/"
        UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

        async with aiohttp.ClientSession() as session:
            async with session.get(URL, headers={"User-Tool": UA},
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return f"Failed to fetch fuel prices: HTTP {resp.status}"
                html = await resp.text()

        # Extract current prices (country-agnostic patterns)
        e95_match = re.search(
            r'Euro\s*95\s*(?:petrol\s*)?(?:in\s*\w+\s*)?(?:is\s*)?€([\d.]+)\s*per\s*lit', html, re.I
        )
        diesel_match = re.search(
            r'diesel\s*(?:is\s*)?€([\d.]+)\s*per\s*lit', html, re.I
        )

        e95_change = re.search(r'petrol\s*prices?\s*(decreased|increased)\s*by\s*([\d.]+)%', html, re.I)
        diesel_change = re.search(r'diesel\s*prices?\s*(decreased|increased)\s*by\s*([\d.]+)%', html, re.I)

        date_match = re.search(r'As of\s+(\w+\s+\d+,\s+\d{4})', html, re.I)
        update_date = date_match.group(1) if date_match else "unknown"

        eu_e95 = re.search(r'EU average of €([\d.]+)/L for petrol', html, re.I)
        eu_diesel = re.search(r'EU average of €([\d.]+)/L for diesel', html, re.I)

        table_rows = re.findall(
            r'(\d{4}-\d{2}-\d{2})\s*\|\s*€([\d.]+)\s*\|\s*€([\d.]+)', html
        )

        e95_price = float(e95_match.group(1)) if e95_match else None
        diesel_price = float(diesel_match.group(1)) if diesel_match else None

        show_e95 = fuel_filter in (None, "e95") and e95_price
        show_diesel = fuel_filter in (None, "diesel") and diesel_price

        if not show_e95 and not show_diesel:
            return "Failed to parse fuel prices from fuel-prices.eu"

        if is_voice:
            language = (context.get("language") or "").lower()
            is_ru = language.startswith("ru")
            def _say(p):
                return f"{p:.2f}".replace(".", ",") if is_ru else f"{p:.2f}"
            v_parts = []
            if show_e95:
                v_parts.append(("девяносто пятый " if is_ru else "Euro 95 ") + _say(e95_price))
            if show_diesel:
                v_parts.append(("дизель " if is_ru else "diesel ") + _say(diesel_price))
            if is_ru:
                country_ru = {"Estonia": "Эстонии", "Latvia": "Латвии"}.get(country, country)
                return f"В {country_ru}: " + ", ".join(v_parts) + "."
            return f"{country}: " + ", ".join(v_parts) + "."

        lines = [f"<b>⛽ {flag} {esc(country)} Fuel Prices</b>", f"<i>Updated: {esc(update_date)}</i>\n"]

        def fmt_fuel(label, price, change_match, eu_match):
            change = ""
            if change_match:
                icon = "📉" if change_match.group(1).lower() == "decreased" else "📈"
                change = f" {icon} {change_match.group(2)}% vs last week"
            eu_diff = ""
            if eu_match:
                diff = price - float(eu_match.group(1))
                eu_diff = f" ({'+' if diff >= 0 else ''}{diff:.3f} vs EU avg €{eu_match.group(1)})"
            return f"<b>{label}:</b> €{price:.3f}/L{change}{eu_diff}"

        if show_e95:
            lines.append(fmt_fuel("Euro 95", e95_price, e95_change, eu_e95))
        if show_diesel:
            lines.append(fmt_fuel("Diesel", diesel_price, diesel_change, eu_diesel))

        # Tank cost
        lines.append("")
        tank_int = int(tank_liters) if tank_liters == int(tank_liters) else tank_liters
        if show_e95:
            lines.append(f"🚗 {tank_int}L tank (E95): <b>€{e95_price * tank_liters:.2f}</b>")
        if show_diesel:
            lines.append(f"🚛 {tank_int}L tank (Diesel): <b>€{diesel_price * tank_liters:.2f}</b>")

        # Recent history
        if table_rows:
            lines.append("\n<b>Recent weeks:</b>")
            for date, e95, diesel in table_rows[:6]:
                if fuel_filter == "e95":
                    lines.append(f"  {date}: E95 €{e95}")
                elif fuel_filter == "diesel":
                    lines.append(f"  {date}: Diesel €{diesel}")
                else:
                    lines.append(f"  {date}: E95 €{e95} | Diesel €{diesel}")

        lines.append(f"\nSource: <a href=\"{URL}\">fuel-prices.eu</a>")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to fetch fuel prices: {str(e)}"