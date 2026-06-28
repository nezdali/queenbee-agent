async def run(context: dict) -> str:
    """Kolga Laht / Estonian coast sea-level observations from Ilmateenistus."""
    try:
        from core.tool_utils import fetch_text
        from bs4 import BeautifulSoup
        import datetime
        import re
        from zoneinfo import ZoneInfo
    except Exception as e:
        return f"Initialization failed: {e}"

    URL = "https://www.ilmateenistus.ee/meri/vaatlusandmed/kogu-rannik/andmed-tabelis/"
    UA = "Mozilla/5.0 (KolgaLahtTool)"
    TZ = ZoneInfo("Europe/Tallinn")

    # Stations near Kolga Bay, in preference order. Loksa sits right on the bay.
    PREFERRED = ["Loksa", "Kunda", "Pirita", "Tallinn", "Heltermaa", "Dirhami"]

    def to_float(s):
        if not s:
            return None
        s = s.replace("\xa0", " ").replace("−", "-").replace(",", ".").strip()
        try:
            return float(s)
        except Exception:
            return None

    def fmt_signed_int(v):
        return None if v is None else f"{int(round(v)):+d}"

    def wind_cardinal(deg):
        if deg is None:
            return ""
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return dirs[int((deg + 22.5) // 45) % 8]

    _WIND_RU = {
        "N": "северный", "NE": "северо-восточный", "E": "восточный",
        "SE": "юго-восточный", "S": "южный", "SW": "юго-западный",
        "W": "западный", "NW": "северо-западный",
    }
    _WIND_EN = {
        "N": "northerly", "NE": "northeasterly", "E": "easterly",
        "SE": "southeasterly", "S": "southerly", "SW": "southwesterly",
        "W": "westerly", "NW": "northwesterly",
    }

    def trend_word(change, voice_ru):
        if change is None:
            return ""
        if change >= 2:
            return "растёт" if voice_ru else "rising"
        if change <= -2:
            return "падает" if voice_ru else "falling"
        return "стабильный" if voice_ru else "steady"

    def parse_table(html: str):
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", class_="table")
        if not table:
            return [], None
        body = table.find("tbody")
        rows = []
        for tr in (body.find_all("tr") if body else []):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if not cells or not cells[0].strip():
                continue
            c = cells + [""] * (9 - len(cells))
            rows.append({
                "station": cells[0],
                "bk77": to_float(c[1]),
                "eh2000": to_float(c[2]),
                "change_24h": to_float(c[3]),
                "water_temp": to_float(c[4]),
                "air_temp": to_float(c[5]),
                "wind_dir": to_float(c[6]),
                "wind_speed": to_float(c[7]),
                "wind_max": to_float(c[8]),
            })
        ts = None
        m = re.search(r"UTC\s+(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})", html)
        if m:
            d, mo, y, hh, mm = map(int, m.groups())
            ts = datetime.datetime(y, mo, d, hh, mm, tzinfo=datetime.timezone.utc).astimezone(TZ)
        return rows, ts

    def has_data(r):
        return any(
            r.get(k) is not None
            for k in ("bk77", "eh2000", "change_24h", "water_temp", "wind_speed")
        )

    def pick_primary(rows, hint):
        if hint:
            hl = hint.lower()
            for r in rows:
                if hl in r["station"].lower():
                    return r
        # Prefer first PREFERRED station that actually has data;
        # only fall back to a data-less row if nothing else is available.
        fallback = None
        for name in PREFERRED:
            nl = name.lower()
            for r in rows:
                if nl in r["station"].lower():
                    if has_data(r):
                        return r
                    if fallback is None:
                        fallback = r
                    break
        if fallback is not None:
            return fallback
        for r in rows:
            if has_data(r):
                return r
        return rows[0] if rows else None

    args = context.get("args") or []
    if isinstance(args, str):
        args = args.split()
    raw_extra = str(context.get("raw_extra") or "")
    user_msg = str(context.get("user_message") or "")
    # Combine all sources so we can match Cyrillic station names that the LLM
    # translation may have mangled (e.g. "пирита" → "pyrite").
    haystack = (" ".join(str(a) for a in args) + " " + raw_extra + " " + user_msg).lower()
    # Russian/transliterated station stems → canonical station name
    _RU_STATIONS = {
        "локс": "Loksa", "пирит": "Pirita", "кунд": "Kunda", "таллин": "Tallinn",
        "хельтерма": "Heltermaa", "хелтерма": "Heltermaa", "дирхам": "Dirhami",
        "роомассаар": "Roomassaare", "кихн": "Kihnu", "вилсанд": "Vilsandi",
        "осмуссаар": "Osmussaare", "хаапсал": "Haapsalu", "пярну": "Pärnu",
        "нарв": "Narva", "лету": "Letipea", "леэту": "Letipea",
    }
    hint = None
    for stem, canonical in _RU_STATIONS.items():
        if stem in haystack:
            hint = canonical
            break
    if not hint:
        # Fall back to args (latin), strip noise/trigger words.
        _NOISE = {
            "kolga", "sea", "tide", "море", "уровень", "уровня", "level", "water",
            "вода", "воды", "what", "is", "the", "in", "at", "на", "в",
            "какой", "сколько", "tell", "me", "show", "узнай", "проверь",
            "seas", "sealevel",
        }
        hint = " ".join(str(a) for a in args if str(a).lower() not in _NOISE).strip() or None
    language = (context.get("language") or "").lower()
    is_voice = bool(language)
    is_ru = language.startswith("ru")

    try:
        html, status = await fetch_text(URL, headers={"User-Tool": UA})
        if status != 200 or not html:
            return f"❌ Ilmateenistus unreachable (HTTP {status})."
        rows, ts = parse_table(html)
        if not rows:
            return "❌ No sea-level rows found on Ilmateenistus page."

        primary = pick_primary(rows, hint)
        if not primary:
            return "❌ No matching station."

        if is_voice:
            # If the chosen station has no level/temp/wind data at this
            # observation time, hop to the next station that does.
            if not has_data(primary):
                for alt in rows:
                    if alt is not primary and has_data(alt):
                        primary = alt
                        break
            bk77 = primary["bk77"]
            wt = primary["water_temp"]
            ws = primary["wind_speed"]
            wd = wind_cardinal(primary["wind_dir"])
            tw = trend_word(primary["change_24h"], is_ru)
            if is_ru:
                parts = [f"Уровень моря в {primary['station']}"]
                if bk77 is not None:
                    parts.append(f"{int(round(bk77)):+d} сантиметров")
                if tw:
                    parts.append(tw)
                if wt is not None:
                    parts.append(f"вода {wt:.1f} градусов")
                if ws is not None:
                    wd_word = _WIND_RU.get(wd, "")
                    wd_suffix = f", {wd_word}" if wd_word else ""
                    parts.append(f"ветер {ws:.0f} метров в секунду{wd_suffix}")
                if len(parts) == 1:
                    return f"Нет данных по морю в {primary['station']} сейчас."
                return ", ".join(parts) + "."
            parts = [f"Sea level at {primary['station']}"]
            if bk77 is not None:
                parts.append(f"{int(round(bk77)):+d} centimetres")
            if tw:
                parts.append(tw)
            if wt is not None:
                parts.append(f"water {wt:.1f} degrees")
            if ws is not None:
                wd_word = _WIND_EN.get(wd, "")
                wd_suffix = f", {wd_word}" if wd_word else ""
                parts.append(f"wind {ws:.0f} metres per second{wd_suffix}")
            if len(parts) == 1:
                return f"No sea data for {primary['station']} right now."
            return ", ".join(parts) + "."

        lines = [f"<b>🌊 Sea level — {primary['station']} (Kolga Bay area)</b>"]
        if ts:
            lines.append(f"<i>Observed: {ts.strftime('%a %d %b %H:%M %Z')}</i>")
        lines.append("")
        if primary["bk77"] is not None or primary["eh2000"] is not None:
            bk = fmt_signed_int(primary["bk77"]) or "—"
            eh = fmt_signed_int(primary["eh2000"]) or "—"
            lines.append(f"• Level: <b>{bk} cm</b> (BK77) · {eh} cm (EH2000)")
        if primary["change_24h"] is not None:
            ch = fmt_signed_int(primary["change_24h"])
            lines.append(f"• 24 h change: {ch} cm ({trend_word(primary['change_24h'], False)})")
        if primary["water_temp"] is not None:
            lines.append(f"• Water: {primary['water_temp']:.1f} °C")
        if primary["air_temp"] is not None:
            lines.append(f"• Air: {primary['air_temp']:.1f} °C")
        if primary["wind_speed"] is not None:
            wd = wind_cardinal(primary["wind_dir"])
            extra = f" (gust {primary['wind_max']:.0f})" if primary["wind_max"] is not None else ""
            dir_str = f" {wd} ({int(primary['wind_dir'])}°)" if primary["wind_dir"] is not None else ""
            lines.append(f"• Wind: {primary['wind_speed']:.1f} m/s{dir_str}{extra}")

        seen = {primary["station"].lower()}
        others = []
        for name in PREFERRED:
            nl = name.lower()
            for r in rows:
                if nl in r["station"].lower() and r["station"].lower() not in seen:
                    others.append(r)
                    seen.add(r["station"].lower())
                    break
        if others:
            lines.append("")
            lines.append("<b>Nearby gauges</b>")
            for r in others[:4]:
                bk = fmt_signed_int(r["bk77"]) or "—"
                ch = fmt_signed_int(r["change_24h"])
                ch_str = f" ({ch} cm/24h)" if ch else ""
                wt_str = f" · water {r['water_temp']:.1f}°C" if r["water_temp"] is not None else ""
                lines.append(f"• {r['station']}: {bk} cm{ch_str}{wt_str}")

        lines.append("")
        lines.append('<a href="https://www.ilmateenistus.ee/meri/vaatlusandmed/kogu-rannik/andmed-tabelis/">Source: Estonian Weather Service</a>')
        return "\n".join(lines)
    except Exception as e:
        return f"Kolga sea level tool error: {e}"
