"""Huum sauna tool.

Talks to the Huum cloud API (UKU Wi-Fi controller) to read state and
control a Huum electric sauna heater (e.g. the Drop). Uses the same
endpoint surface as the official Home Assistant integration / pyhuum.
"""

from __future__ import annotations

USAGE = (
    "🧖 Huum sauna commands:\n"
    "• sauna — current status\n"
    "• sauna on [T] — turn on (default 80°C, range 40–110)\n"
    "• sauna off — turn off\n"
    "• sauna temp <T> — change target temperature\n"
    "• sauna humidity <0-10> — set steamer humidity\n"
    "• sauna light — toggle sauna light\n"
    "• sauna raw — raw status JSON for diagnostics\n"
    "\n"
    "Safety: turning on is blocked while the door is open.\n"
    "Add 'force' to override, e.g. sauna on 85 force\n"
    "\n"
    "💡 Recipes:\n"
    "• Quick warm-up: sauna on 85\n"
    "• Steam session: sauna on 75 → sauna humidity 6\n"
    "• Done: sauna off\n"
    "• Mood lighting: sauna light"
)

_STATUS_TEXT = {
    230: "offline",
    231: "online, heating",
    232: "online, idle",
    233: "locked (in use)",
    400: "emergency stop",
}

_MIN_TEMP = 40
_MAX_TEMP = 110


async def run(context: dict) -> str:
    import os
    import json as _json

    import aiohttp

    username = os.getenv("HUUM_USERNAME", "").strip()
    password = os.getenv("HUUM_PASSWORD", "").strip()
    if not username or not password:
        return "❌ HUUM_USERNAME / HUUM_PASSWORD not configured in Key Vault."

    args = context.get("args") or []
    args = [a for a in args if isinstance(a, str) and a.strip()]
    # Strip trigger keywords from anywhere (voice queries often put them last)
    _TRIGGERS = {"sauna", "huum", "сауна", "сауны", "сауне",
                 "баня", "бани", "бане", "парная", "парной"}
    args = [a for a in args if a.lower() not in _TRIGGERS]

    # Normalize natural-language phrasings (post Russian→English translation,
    # voice users say things like "turn on the sauna at 85 degrees")
    if args:
        s = " " + " ".join(a.lower() for a in args) + " "

        # Help intent: "tell me commands / how to / which command" → help
        _HELP_PATTERNS = (
            "расскажи", "подскажи", "какие команды", "какая команда",
            "какой команды", "как включить", "как выключить", "как сделать",
            "помощь", "справка",
            "tell me how", "how do i", "how to", "which command", "what command",
            "list commands", "help",
        )
        if any(p in s for p in _HELP_PATTERNS):
            args = ["help"]
        else:
            for filler in (
                " please ", " the ", " to ", " at ", " a ",
                " в ", " на ", " пожалуйста ", " сейчас ", " прямо ", " у ", " мне ",
                " какой ", " какая ", " какое ", " какие ",
                " сколько ", " выдаёт ", " выдает ", " показывает ",
                " now ", " what ", " tell ", " show ", " me ", " us ",
            ):
                s = s.replace(filler, " ")
            for a, b in (
                # English
                ("turn on", "on"), ("switch on", "on"), ("start", "on"),
                ("turn off", "off"), ("switch off", "off"), ("stop", "off"),
                ("set temperature", "temp"), ("set temp", "temp"),
                ("change temperature", "temp"), ("change temp", "temp"),
                ("current temperature", "status"), ("current temp", "status"),
                ("how hot", "status"), ("what's the temperature", "status"),
                ("what is the temperature", "status"),
                ("toggle light", "light"), ("turn light", "light"),
                ("degrees", ""), ("celsius", ""),
                # Russian (in case translation is skipped or partial)
                ("включи", "on"), ("включить", "on"), ("запусти", "on"),
                ("выключи", "off"), ("выключить", "off"), ("останови", "off"),
                ("установи температуру", "temp"), ("задай температуру", "temp"),
                ("текущая температура", "status"), ("какая температура", "status"),
                ("сколько градусов", "status"),
                ("температуру", "temp"), ("температура", "status"),
                ("свет", "light"), ("лампа", "light"),
                ("статус", "status"), ("состояние", "status"),
                ("градусов", ""), ("градуса", ""), ("градус", ""),
            ):
                s = s.replace(a, b)
            args = [t for t in s.split() if t]
            # "temp" with no number → treat as status query
            if args and args[0] == "temp" and not any(t.lstrip("-").isdigit() for t in args[1:]):
                args[0] = "status"

    action = (args[0].lower() if args else "status")
    extra = [a.lower() for a in args[1:]]

    BASE = "https://sauna.huum.eu/action/home"
    auth = aiohttp.BasicAuth(username, password)
    timeout = aiohttp.ClientTimeout(total=20)

    def _fmt(data: dict) -> str:
        code = data.get("statusCode")
        text = _STATUS_TEXT.get(code, f"code {code}")
        door = data.get("door")
        # door field is unreliable on units without the optional door sensor
        door_txt = "open" if door else "closed"
        name = data.get("saunaName") or "Sauna"
        lines = [f"🧖 {name} — {text}"]
        temp = data.get("temperature")
        if temp is not None:
            lines.append(f"• Temperature: {temp}°C")
        target = data.get("targetTemperature")
        if target is not None:
            lines.append(f"• Target: {target}°C")
        hum = data.get("humidity")
        if hum is None:
            hum = data.get("measuredHumidity")
        if hum is not None:
            lines.append(f"• Humidity: {hum}/10")
        thum = data.get("targetHumidity")
        if thum is not None and thum != hum:
            lines.append(f"• Target humidity: {thum}/10")
        light = data.get("light")
        if light is not None:
            lines.append(f"• Light: {'on' if light else 'off'}")
        end = data.get("endDate")
        if end:
            try:
                from datetime import datetime, timezone
                end_dt = datetime.fromtimestamp(int(end), tz=timezone.utc)
                lines.append(f"• Session ends: {end_dt.strftime('%H:%M UTC')}")
            except Exception:
                pass
        steamer_err = data.get("steamerError")
        if steamer_err:
            lines.append(f"⚠ Steamer error code {steamer_err}")
        lines.append(f"• Door: {door_txt}")
        safety = data.get("remoteSafetyState")
        if safety and safety != "safe":
            lines.append(f"⚠ Remote control disabled (safety: {safety})")
        return "\n".join(lines)

    async def _call(session, method, path, json=None):
        url = f"{BASE}/{path}"
        async with session.request(method, url, auth=auth, json=json) as r:
            text = await r.text()
            if r.status == 401:
                raise RuntimeError("Huum auth rejected (check username/password).")
            # Parse JSON first — the API now returns {"error": true, "msg": "..."}
            # error envelopes with HTTP 400 (e.g. "REMOTE_NOT_SAFE", "DOOR_OPEN",
            # "NOT_PRIVATE"). Surface a friendly message instead of raw HTTP text.
            try:
                data = _json.loads(text) if text else {}
            except ValueError:
                if r.status >= 400:
                    if r.status == 403:
                        raise RuntimeError("Huum forbidden (not the active controller of this sauna).")
                    raise RuntimeError(f"Huum {method} {path} HTTP {r.status}: {text[:200]}")
                raise RuntimeError(f"Huum {path}: non-JSON response: {text[:200]}")

            if isinstance(data, dict) and data.get("error"):
                msg_code = (data.get("msg") or "").upper()
                friendly = {
                    "REMOTE_NOT_SAFE":  "Sauna is not in remote-ready state "
                                        "(door open or safety check failed). Open the Huum "
                                        "app, close the door and tap the safety toggle.",
                    "DOOR_OPEN":        "Sauna door is open.",
                    "NOT_PRIVATE":      "Sauna is shared — remote control is disabled.",
                    "PAYMENT_REQUIRED": "Huum subscription has expired.",
                    "LOCKED":           "Sauna is locked by another user.",
                }.get(msg_code, data.get("msg") or f"Huum {path} failed (HTTP {r.status})")
                raise RuntimeError(friendly)

            if r.status == 403:
                raise RuntimeError("Huum forbidden (not the active controller of this sauna).")
            if r.status >= 400:
                raise RuntimeError(f"Huum {method} {path} HTTP {r.status}: {text[:200]}")
            return data

    try:
        if action in ("help", "?"):
            is_voice = bool(context.get("language"))
            is_russian = (context.get("language") or "").lower().startswith("ru")
            if is_voice and is_russian:
                return (
                    "Команды сауны: скажи 'сауна включи' чтобы включить, "
                    "'сауна выключи' чтобы выключить, 'сауна температура' "
                    "для текущей температуры, 'сауна температуру 85' чтобы "
                    "задать температуру, 'сауна свет' чтобы включить свет."
                )
            if is_voice:
                return (
                    "Sauna commands: say 'sauna on' to turn on, 'sauna off' "
                    "to turn off, 'sauna temperature' for current temperature, "
                    "'sauna temp 85' to set temperature, 'sauna light' to toggle light."
                )
            return USAGE

        async with aiohttp.ClientSession(timeout=timeout) as session:
            status = await _call(session, "GET", "status")

            is_voice = bool(context.get("language"))
            is_russian = (context.get("language") or "").lower().startswith("ru")

            if action in ("status", "state", ""):
                if is_voice:
                    temp = status.get("temperature")
                    target = status.get("targetTemperature")
                    code = status.get("statusCode")
                    if is_russian:
                        if code == 231:
                            return f"Сауна греется, сейчас {temp} градусов, цель {target}."
                        return f"Сауна выключена, сейчас {temp} градусов."
                    if code == 231:
                        return f"Sauna is heating, currently {temp} degrees, target {target}."
                    return f"Sauna is off, currently {temp} degrees."
                return _fmt(status)

            if action == "raw":
                pretty = _json.dumps(status, indent=2, ensure_ascii=False)
                return f"```\n{pretty}\n```" if len(pretty) < 3500 else pretty[:3500] + "…"

            if action in ("on", "start", "turnon"):
                temp = 80
                force = "force" in extra
                for tok in extra:
                    if tok.isdigit():
                        temp = int(tok)
                        break
                if not (_MIN_TEMP <= temp <= _MAX_TEMP):
                    return f"❌ Temperature must be {_MIN_TEMP}–{_MAX_TEMP}°C."
                # Pre-check the new remoteSafetyState field — the API now hard-rejects
                # /start with REMOTE_NOT_SAFE when this isn't "safe".
                if not force and status.get("remoteSafetyState") == "notSafe":
                    door_hint = " (door appears open)" if status.get("door") else ""
                    return (
                        f"❌ Sauna is not in remote-ready state{door_hint}. "
                        "Close the door and tap the safety toggle in the Huum app, "
                        "or retry with `sauna on " + str(temp) + " force`."
                    )
                data = {"targetTemperature": temp}
                resp = await _call(session, "POST", "start", json=data)
                return "🔥 Starting sauna at {}°C\n\n{}".format(temp, _fmt(resp or status))

            if action in ("off", "stop", "turnoff"):
                resp = await _call(session, "POST", "stop")
                return "🛑 Sauna off.\n\n" + _fmt(resp or status)

            if action in ("temp", "set", "settemp", "temperature"):
                if not extra or not extra[0].lstrip("-").isdigit():
                    return "Usage: sauna temp <40-110>"
                temp = int(extra[0])
                if not (_MIN_TEMP <= temp <= _MAX_TEMP):
                    return f"❌ Temperature must be {_MIN_TEMP}–{_MAX_TEMP}°C."
                if status.get("statusCode") != 231:
                    return "❌ Sauna is not heating. Start it first: sauna on " + str(temp)
                resp = await _call(session, "POST", "start", json={"targetTemperature": temp})
                return f"🎯 Target set to {temp}°C\n\n" + _fmt(resp or status)

            if action in ("humidity", "hum", "steam", "steamer"):
                if not extra or not extra[0].isdigit():
                    return "Usage: sauna humidity <0-10>"
                hum = int(extra[0])
                if not (0 <= hum <= 10):
                    return "❌ Humidity must be 0–10."
                target = status.get("targetTemperature") or 80
                resp = await _call(session, "POST", "start",
                                   json={"targetTemperature": target, "humidity": hum})
                return f"💧 Humidity set to {hum}/10\n\n" + _fmt(resp or status)

            if action in ("light", "lights", "lamp"):
                resp = await _call(session, "GET", "light")
                return "💡 Toggled light.\n\n" + _fmt(resp or status)

            return f"Unknown action '{action}'.\n\n{USAGE}"

    except RuntimeError as e:
        return f"❌ {e}"
    except Exception as e:
        return f"❌ Huum sauna error: {type(e).__name__}: {e}"
