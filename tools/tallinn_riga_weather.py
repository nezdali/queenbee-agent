async def run(context: dict) -> str:
    import aiohttp
    import asyncio

    args = context.get("args") or []
    args_lower = [a.lower() for a in args if isinstance(a, str)]
    is_voice = bool(context.get("language"))
    raw_extra_lower = (context.get("raw_extra") or "").lower()
    user_msg_lower = (context.get("user_message") or "").lower()
    haystack = " ".join([" ".join(args_lower), raw_extra_lower, user_msg_lower])

    # Substring stems → canonical city (handles "таллине", "таллинна", "tallinna" etc.)
    # Includes common Vosk small-model misrecognitions ("сталине" → Таллин).
    CITY_STEMS = [
        ("таллин", "Таллин"),
        ("таллинн", "Таллин"),
        ("сталин", "Таллин"),    # Vosk often hears "таллине" as "сталине"
        ("талин", "Таллин"),
        ("tallin", "Таллин"),
        ("рига", "Рига"),
        ("риге", "Рига"),
        ("ригу", "Рига"),
        ("риги", "Рига"),
        ("riga", "Рига"),
    ]
    requested_cities: set[str] = set()
    for stem, canon in CITY_STEMS:
        if stem in haystack:
            requested_cities.add(canon)
    trigger = (context.get("trigger_word") or "").lower()
    for stem, canon in CITY_STEMS:
        if trigger.startswith(stem):
            requested_cities.add(canon)

    import logging as _lg
    _lg.getLogger(__name__).info(
        "weather tool: haystack=%r trigger=%r requested=%s",
        haystack, trigger, requested_cities,
    )

    DETAIL_ANY = {"all", "full", "полностью", "подробно", "полная",
                  "wind", "ветер", "humidity", "влажность", "влажн",
                  "pressure", "давление", "feels", "ощущается"}
    want_extras = any(a in DETAIL_ANY for a in args_lower)
    full = any(a in ("all", "full", "полностью", "подробно", "полная") for a in args_lower)
    want_wind = full or any(a in ("wind", "ветер") for a in args_lower)
    want_humidity = full or any(a in ("humidity", "влажность", "влажн") for a in args_lower)
    want_pressure = full or any(a in ("pressure", "давление") for a in args_lower)
    want_feels = full or any(a in ("feels", "ощущается") for a in args_lower)

    try:
        cities = [
            ("Таллин", 59.4370, 24.7536),
            ("Рига", 56.9496, 24.1052),
        ]
        if requested_cities:
            cities = [c for c in cities if c[0] in requested_cities]

        async def fetch_openmeteo(session, lat, lon):
            url = (
                "https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                "&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code"
                "&timezone=auto"
            )
            try:
                async with session.get(url, timeout=20) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            except Exception:
                return None
            c = data.get("current") or {}
            if c.get("temperature_2m") is None:
                return None
            return {
                "source": "Open-Meteo",
                "temp": c.get("temperature_2m"),
                "feels": c.get("apparent_temperature"),
                "humidity": c.get("relative_humidity_2m"),
                "wind": c.get("wind_speed_10m"),
                "code": c.get("weather_code"),
            }

        async def fetch_met(session, lat, lon):
            url = (
                "https://api.met.no/weatherapi/locationforecast/2.0/compact"
                f"?lat={lat}&lon={lon}"
            )
            headers = {"User-Agent": "telegram-chatbot/1.0 (contact@example.com)"}
            try:
                async with session.get(url, headers=headers, timeout=20) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            except Exception:
                return None
            try:
                ts = data["properties"]["timeseries"][0]
                inst = ts["data"]["instant"]["details"]
                symbol = (
                    ts["data"].get("next_1_hours", {}).get("summary", {}).get("symbol_code")
                    or ts["data"].get("next_6_hours", {}).get("summary", {}).get("symbol_code")
                )
                wind_ms = inst.get("wind_speed")
                wind_kmh = round(wind_ms * 3.6, 1) if wind_ms is not None else None
                return {
                    "source": "MET",
                    "temp": inst.get("air_temperature"),
                    "humidity": inst.get("relative_humidity"),
                    "wind": wind_kmh,
                    "pressure": inst.get("air_pressure_at_sea_level"),
                    "symbol": symbol,
                }
            except (KeyError, IndexError, TypeError):
                return None

        wmo_map = {
            0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
            45: "туман", 48: "изморозь, туман",
            51: "слабая морось", 53: "морось", 55: "сильная морось",
            61: "слабый дождь", 63: "дождь", 65: "сильный дождь",
            71: "слабый снег", 73: "снег", 75: "сильный снег",
            80: "слабый ливень", 81: "ливень", 82: "сильный ливень",
            95: "гроза", 96: "гроза с градом", 99: "гроза с сильным градом",
        }
        met_symbol_map = {
            "clearsky": "ясно", "fair": "преимущественно ясно",
            "partlycloudy": "переменная облачность", "cloudy": "пасмурно",
            "fog": "туман",
            "lightrainshowers": "слабый ливень", "rainshowers": "ливень", "heavyrainshowers": "сильный ливень",
            "lightrain": "слабый дождь", "rain": "дождь", "heavyrain": "сильный дождь",
            "lightsnow": "слабый снег", "snow": "снег", "heavysnow": "сильный снег",
            "thunder": "гроза",
        }

        def desc_of(src) -> str:
            if not src:
                return "—"
            if src["source"] == "MET":
                sym = src.get("symbol") or ""
                base = sym.split("_", 1)[0]
                return met_symbol_map.get(base, base.replace("_", " ") or "—")
            return wmo_map.get(src.get("code"), "—")

        async def fetch_city(session, name, lat, lon):
            met = await fetch_met(session, lat, lon)
            src = met if met and met.get("temp") is not None else None
            if src is None:
                om = await fetch_openmeteo(session, lat, lon)
                src = om if om and om.get("temp") is not None else None
            if src is None:
                return f"{name}: нет данных"

            temp = src["temp"]
            desc = desc_of(src)

            if is_voice and not want_extras:
                return f"{name}: {temp} градусов, {desc}."

            if not want_extras:
                return f"*{name}*: {temp}°C, {desc}"

            parts = [f"*{name}*: {temp}°C, {desc}"]
            if want_feels and src.get("feels") is not None:
                parts.append(f"ощущ. {src['feels']}°C")
            if want_humidity and src.get("humidity") is not None:
                parts.append(f"влажн. {src['humidity']}%")
            if want_wind and src.get("wind") is not None:
                parts.append(f"ветер {src['wind']} км/ч")
            if want_pressure and src.get("pressure") is not None:
                parts.append(f"давл. {round(src['pressure'])} гПа")
            return ", ".join(parts)

        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(
                *[fetch_city(session, name, lat, lon) for name, lat, lon in cities],
                return_exceptions=True,
            )

        lines = [r if not isinstance(r, Exception) else "Ошибка получения данных." for r in results]
        if is_voice:
            return " ".join(lines)
        return "\n".join(lines) if lines else "Нет данных."
    except Exception as e:
        return f"Ошибка при получении погоды: {str(e)}"
