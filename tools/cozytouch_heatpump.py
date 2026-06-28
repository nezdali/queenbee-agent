"""Atlantic Cozytouch heat pump tool.

Talks to the Atlantic / Cozytouch cloud (Overkiz backend) to read state
and control an air/water heat pump and its domestic hot water tank.
"""

from __future__ import annotations

USAGE = (
    "🔥 Cozytouch heat pump commands:\n"
    "• cozy — current status\n"
    "• cozy devices — list devices\n"
    "• cozy raw — raw states for diagnostics\n"
    "• cozy energy — energy consumption\n"
    "• cozy set 21 — set target °C (derogation on the zone)\n"
    "• cozy mode <stop|heating|cooling> — main heat-pump operating mode\n"
    "• cozy preset <comfort|eco|away|frost|auto|manu> — zone preset\n"
    "• cozy hotwater <on|off|boost> — control DHW tank\n"
    "\n"
    "💡 Recipes:\n"
    "• Warm up now: cozy mode heating → cozy set 22\n"
    "• Back to schedule: cozy preset auto\n"
    "• Leaving for a while: cozy preset away\n"
    "• Summer / long absence: cozy mode stop and cozy hotwater off\n"
    "• Hot shower soon: cozy hotwater boost\n"
    "• Check power use: cozy energy"
)


async def run(context: dict) -> str:
    import os
    import json as _json

    import aiohttp

    username = os.getenv("COZYTOUCH_USERNAME", "").strip()
    password = os.getenv("COZYTOUCH_PASSWORD", "").strip()
    if not username or not password:
        return "❌ COZYTOUCH_USERNAME / COZYTOUCH_PASSWORD not configured in Key Vault."

    args = context.get("args") or []
    args = [a for a in args if isinstance(a, str) and a.strip()]
    # Strip trigger keywords from anywhere (not just position 0) — voice
    # queries often put the keyword at the end ("какая температура у печки").
    _TRIGGERS = {"cozy", "cozytouch", "heatpump", "печка", "печки", "печке",
                 "печку", "печи", "печью", "печь",
                 "тепло", "отопление", "stove", "heat", "heating",
                 "сделай", "сделать", "make",
                 "дом", "дома", "доме", "house", "home",
                 "дело"}  # STT mishears "сделай" as "дело"
    args = [a for a in args if a.lower() not in _TRIGGERS]

    # Normalize natural-language phrasings produced by Russian→English translation
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
            # Drop Russian/English question words and fillers
            for filler in (
                " please ", " the ", " to ", " a ", " is ",
                " в ", " на ", " пожалуйста ", " сейчас ", " прямо ", " там ", " тут ",
                " у ", " мне ", " ты ", " вы ", " он ", " она ",
                " какой ", " какая ", " какое ", " какие ",
                " сколько ", " выдаёт ", " выдает ", " показывает ", " стоит ",
                " now ", " what ", " tell ", " show ", " me ", " us ",
            ):
                s = s.replace(filler, " ")
            # Relative deltas FIRST (before "температуру"→"set" eats the verb context).
            # "warmer/cooler by N", "теплее/холоднее на N", "повысь/снизь на N"
            import re as _re
            _NUM_RU = {
                "один": "1", "одну": "1", "одна": "1", "одного": "1",
                "два": "2", "две": "2", "пару": "2", "пары": "2",
                "три": "3", "трёх": "3", "трех": "3",
                "четыре": "4", "пять": "5",
            }
            for word, digit in _NUM_RU.items():
                s = _re.sub(rf"\b{word}\b", digit, s)
            # Verbs that imply delta — collapse "verb [темп.] [на] N" → "warmer/cooler N"
            s = _re.sub(
                r"(повыс\w*|увелич\w*|теплее|жарче|горячее|warmer|hotter)\s+(?:by\s+)?(?:температур\w*\s+)?(\d+(?:[.,]\d+)?)",
                r"warmer \2", s,
            )
            s = _re.sub(
                r"(снизь?\w*|уменьш\w*|холоднее|прохладнее|cooler|colder)\s+(?:by\s+)?(?:температур\w*\s+)?(\d+(?:[.,]\d+)?)",
                r"cooler \2", s,
            )
            # Reverse word order: "by N degrees cooler"
            s = _re.sub(r"(\d+(?:[.,]\d+)?)\s*(?:degrees?\s+)?(warmer|hotter)", r"warmer \1", s)
            s = _re.sub(r"(\d+(?:[.,]\d+)?)\s*(?:degrees?\s+)?(cooler|colder)", r"cooler \1", s)
            # "на N градус(а/ов) меньше/больше" → cooler/warmer N
            s = _re.sub(r"(\d+(?:[.,]\d+)?)\s*градус\w*\s*(меньше|пониже|холоднее)", r"cooler \1", s)
            s = _re.sub(r"(\d+(?:[.,]\d+)?)\s*градус\w*\s*(больше|повыше|теплее)", r"warmer \1", s)
            # Bare "N меньше/больше" without explicit "градус"
            s = _re.sub(r"\b(\d+(?:[.,]\d+)?)\s+(меньше|пониже)\b", r"cooler \1", s)
            s = _re.sub(r"\b(\d+(?:[.,]\d+)?)\s+(больше|повыше)\b", r"warmer \1", s)

            for a, b in (
                # English
                ("set temperature", "set"), ("set temp", "set"),
                ("change temperature", "set"), ("change temp", "set"),
                ("turn on hot water boost", "hotwater boost"),
                ("hot water boost", "hotwater boost"),
                ("hot water on", "hotwater on"),
                ("hot water off", "hotwater off"),
                ("hot water", "hotwater"),
                ("boost hotwater", "hotwater boost"),
                ("current temperature", "status"), ("current temp", "status"),
                ("what's the temperature", "status"), ("how warm", "status"),
                ("what is the temperature", "status"),
                ("turn on heating", "mode heating"),
                ("turn off heating", "mode stop"),
                ("stop heating", "mode stop"),
                ("start heating", "mode heating"),
                ("cooling", "mode cooling"),
                ("degrees", ""), ("celsius", ""),
                # Russian
                ("горячую воду буст", "hotwater boost"),
                ("горячая вода буст", "hotwater boost"),
                ("буст горячей воды", "hotwater boost"),
                ("бойлер буст", "hotwater boost"),
                ("горячую воду", "hotwater"), ("горячая вода", "hotwater"),
                ("установи температуру", "set"), ("задай температуру", "set"),
                ("измени температуру", "set"),
                ("текущая температура", "status"), ("какая температура", "status"),
                ("сколько градусов", "status"),
                ("включи отопление", "mode heating"), ("запусти отопление", "mode heating"),
                ("выключи отопление", "mode stop"), ("останови отопление", "mode stop"),
                ("охлаждение", "mode cooling"),
                ("температуру", "set"), ("температура", "status"),
                ("статус", "status"), ("состояние", "status"),
                ("градусов", ""), ("градуса", ""), ("градус", ""),
            ):
                s = s.replace(a, b)
            args = [t for t in s.split() if t]
            # Delta intent wins over status: if both present, drop status.
            if "warmer" in args or "cooler" in args:
                action_idx = next((i for i, t in enumerate(args) if t in ("warmer", "cooler")), 0)
                args = args[action_idx:]
            # "set" with no numeric value → treat as status query
            if args and args[0] == "set" and not any(
                t.replace(",", ".").lstrip("-").replace(".", "", 1).isdigit() for t in args[1:]
            ):
                args[0] = "status"

    action = (args[0].lower() if args else "status")
    extra = args[1:]

    ATLANTIC_API = "https://apis.groupe-atlantic.com"
    OVERKIZ_BASE = "https://ha110-1.overkiz.com/enduser-mobile-web/enduserAPI"
    COZYTOUCH_CLIENT_ID = (
        "Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"
    )
    UA = "Mozilla/5.0 cozytouch-bot/1.1"

    async def _login(session: aiohttp.ClientSession) -> None:
        async with session.post(
            f"{ATLANTIC_API}/token",
            data={
                "grant_type": "password",
                "username": f"GA-PRIVATEPERSON/{username}",
                "password": password,
            },
            headers={
                "Authorization": f"Basic {COZYTOUCH_CLIENT_ID}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": UA,
            },
            timeout=20,
        ) as r:
            body = await r.text()
            if r.status != 200:
                raise RuntimeError(f"Atlantic /token HTTP {r.status}: {body[:300]}")
            tok = _json.loads(body)
            if tok.get("error"):
                raise RuntimeError(f"Atlantic token error: {tok}")
            access = tok["access_token"]
        async with session.get(
            f"{ATLANTIC_API}/magellan/accounts/jwt",
            headers={"Authorization": f"Bearer {access}", "User-Agent": UA},
            timeout=20,
        ) as r:
            jwt = (await r.text()).strip().strip('"')
            if r.status != 200 or not jwt:
                raise RuntimeError(f"Magellan JWT HTTP {r.status}: {jwt[:300]}")
        async with session.post(
            f"{OVERKIZ_BASE}/login",
            data={"jwt": jwt},
            headers={"User-Agent": UA},
            timeout=20,
        ) as r:
            text = await r.text()
            if r.status not in (200, 204):
                raise RuntimeError(f"Overkiz /login HTTP {r.status}: {text[:300]}")
            if r.status == 200 and text:
                try:
                    j = _json.loads(text)
                    if isinstance(j, dict) and j.get("success") is False:
                        raise RuntimeError(f"Overkiz /login refused: {text[:300]}")
                except _json.JSONDecodeError:
                    pass

    async def _get(session: aiohttp.ClientSession, path: str):
        async with session.get(
            f"{OVERKIZ_BASE}/{path.lstrip('/')}",
            headers={"User-Agent": UA},
            timeout=30,
        ) as r:
            body = await r.text()
            if r.status != 200:
                raise RuntimeError(f"GET {path} HTTP {r.status}: {body[:300]}")
            return _json.loads(body) if body else {}

    async def _exec(session: aiohttp.ClientSession, label: str, device_url: str,
                    command: str, params: list | None = None) -> str:
        payload = {
            "label": label,
            "actions": [{
                "deviceURL": device_url,
                "commands": [{"name": command, "parameters": params or []}],
            }],
        }
        async with session.post(
            f"{OVERKIZ_BASE}/exec/apply",
            json=payload,
            headers={"User-Agent": UA, "Content-Type": "application/json"},
            timeout=30,
        ) as r:
            text = await r.text()
            if r.status != 200:
                raise RuntimeError(f"exec/apply HTTP {r.status}: {text[:300]}")
            return text

    def _state(dev: dict, name: str):
        for s in dev.get("states") or []:
            if s.get("name") == name:
                return s.get("value")
        return None

    def _classify(dev: dict) -> str:
        ui = (dev.get("uiClass") or "").lower()
        widget = (dev.get("widget") or "").lower()
        if "domestichotwater" in ui or "dhw" in widget or "water" in ui:
            return "dhw"
        if "zonecontrolzone" in widget or "heatingzone" in widget or "zone" in widget:
            return "zone"
        if "maincomponent" in widget or "mainboiler" in widget:
            return "heatpump"
        if any(k in ui for k in ("heatingsystem", "boiler", "heatpump", "heating")):
            return "heatpump"
        if any(k in widget for k in ("heatpump", "boiler")):
            return "heatpump"
        if "gateway" in ui:
            return "gateway"
        return ui or widget or "device"

    try:
        if action in ("help", "?"):
            return USAGE

        is_voice = bool(context.get("language"))
        is_russian = (context.get("language") or "").lower().startswith("ru")

        async with aiohttp.ClientSession() as session:
            await _login(session)
            setup = await _get(session, "setup")
            devs = (setup.get("devices") if isinstance(setup, dict) else None) or []
            if isinstance(setup, dict) and not devs and isinstance(setup.get("setup"), dict):
                devs = setup["setup"].get("devices") or []

            if action == "devices":
                lines = [f"📋 {len(devs)} device(s):"]
                for d in devs:
                    lines.append(
                        f"• {d.get('label') or '—'} — {d.get('uiClass') or '?'} "
                        f"({d.get('widget') or '?'})\n  {d.get('deviceURL')}"
                    )
                return "\n".join(lines) or "No devices."

            if action == "raw":
                lines = []
                for d in devs:
                    lines.append(f"\n{d.get('label')} ({d.get('uiClass')} / {d.get('widget')})")
                    for s in d.get("states") or []:
                        lines.append(f"  {s.get('name')} = {s.get('value')}")
                out = "\n".join(lines) or "No devices."
                return out if len(out) < 3800 else out[:3800] + "…"

            if action in ("status", "state", ""):
                if not devs:
                    return "No devices returned by Cozytouch."

                if is_voice:
                    indoor = None
                    target = None
                    for d in devs:
                        url = d.get("deviceURL") or ""
                        ui = (d.get("uiClass") or "").lower()
                        if ui == "temperaturesensor" and url.endswith("#9"):
                            indoor = _state(d, "core:TemperatureState")
                        if _classify(d) == "zone" and target is None:
                            target = (
                                _state(d, "core:TargetTemperatureState")
                                or _state(d, "core:HeatingTargetTemperatureState")
                            )
                    if is_russian:
                        parts = []
                        if indoor is not None:
                            parts.append(f"сейчас {indoor} градусов")
                        if target is not None:
                            parts.append(f"цель {target}")
                        return ("В доме " + ", ".join(parts) + ".") if parts else "Нет данных."
                    parts = []
                    if indoor is not None:
                        parts.append(f"currently {indoor} degrees")
                    if target is not None:
                        parts.append(f"target {target}")
                    return ("Indoor " + ", ".join(parts) + ".") if parts else "No data."

                # Atlantic Pass APC firmware convention:
                #   sub-device #3 = outdoor temperature sensor
                #   sub-device #9 = indoor (room) temperature sensor
                temp_sensor_role = {"3": "Outdoor", "9": "Indoor"}

                lines = ["🔥 Cozytouch status"]
                for d in devs:
                    kind = _classify(d)
                    url = d.get("deviceURL") or ""
                    ui = (d.get("uiClass") or "").lower()

                    # Hide noisy / useless rows
                    if kind == "gateway" or ui in ("pod",):
                        continue
                    if "wifi" in ui:
                        continue
                    if ui == "electricitysensor":
                        # bare per-circuit sensors — covered by `cozy energy`
                        continue

                    label = d.get("label") or url
                    bits = []

                    if ui == "temperaturesensor":
                        suffix = url.rsplit("#", 1)[-1] if "#" in url else ""
                        role = temp_sensor_role.get(suffix, "Temperature")
                        temp = _state(d, "core:TemperatureState")
                        if temp is None:
                            continue
                        lines.append(f"• {role}: {temp}°C")
                        continue

                    if kind == "zone":
                        target = (
                            _state(d, "core:TargetTemperatureState")
                            or _state(d, "core:HeatingTargetTemperatureState")
                        )
                        temp = _state(d, "core:TemperatureState")
                        preset = _state(d, "io:PassAPCHeatingModeState")
                        if temp is not None:
                            bits.append(f"now {temp}°C")
                        if target is not None:
                            bits.append(f"target {target}°C")
                        if preset is not None:
                            bits.append(f"preset {preset}")

                    elif kind == "dhw":
                        wtemp = _state(d, "core:WaterTemperatureState") or _state(d, "core:DHWTemperatureState")
                        wtarget = (
                            _state(d, "core:WaterTargetTemperatureState")
                            or _state(d, "core:TargetDHWTemperatureState")
                            or _state(d, "core:TargetTemperatureState")
                        )
                        dmode = _state(d, "io:DHWModeState") or _state(d, "core:DHWOnOffState")
                        boost = _state(d, "core:BoostOnOffState") or _state(d, "io:DHWBoostModeState")
                        if wtemp is not None:
                            bits.append(f"water {wtemp}°C")
                        if wtarget is not None:
                            bits.append(f"target {wtarget}°C")
                        if dmode is not None:
                            bits.append(f"mode {dmode}")
                        if boost not in (None, "off"):
                            bits.append(f"boost {boost}")

                    elif kind == "heatpump":
                        mode = (
                            _state(d, "io:PassAPCOperatingModeState")
                            or _state(d, "core:OperatingModeState")
                        )
                        if mode is not None:
                            bits.append(f"mode {mode}")

                    online = _state(d, "core:StatusState") or _state(d, "io:OnOffState")
                    if online is not None and str(online).lower() not in ("available", "on"):
                        bits.append(str(online))

                    lines.append(f"• {label} ({kind}) — {', '.join(bits) or 'no readings'}")
                return "\n".join(lines)

            if action == "energy":
                from datetime import datetime, timezone
                from pathlib import Path

                # HP_Actuator exposes instantaneous power (W) split per circuit;
                # the three CumulativeElectricPowerConsumptionSensors expose
                # lifetime energy counters (Wh). The three sensor positions in
                # Atlantic firmware are conventionally: #1 total / #2 DHW / #3 heating.
                sensor_labels = ["Total", "DHW", "Heating"]
                hp_power_labels = {
                    "core:HeatingPowerConsumptionState": "Heating compressor",
                    "core:HeatingPowerConsumptionExtra1State": "Heating backup 1",
                    "core:HeatingPowerConsumptionExtra2State": "Heating backup 2",
                    "core:HeatingPowerConsumptionExtra3State": "Heating backup 3",
                    "core:ECSPowerConsumptionState": "DHW compressor",
                    "core:ECSPowerConsumptionExtra1State": "DHW backup 1",
                    "core:ECSPowerConsumptionExtra2State": "DHW backup 2",
                    "core:ECSPowerConsumptionExtra3State": "DHW backup 3",
                }
                power_lines: list[str] = []
                current_wh: dict[str, float] = {}
                sensor_idx = 0
                for d in devs:
                    for s in d.get("states") or []:
                        name = s.get("name") or ""
                        val = s.get("value")
                        if name in hp_power_labels and isinstance(val, (int, float)):
                            if val > 0:
                                power_lines.append(f"• {hp_power_labels[name]}: {val} W")
                        elif name == "core:ElectricEnergyConsumptionState" and isinstance(val, (int, float)):
                            label = sensor_labels[sensor_idx] if sensor_idx < len(sensor_labels) else f"Sensor {sensor_idx + 1}"
                            current_wh[label] = float(val)
                            sensor_idx += 1

                if not power_lines and not current_wh:
                    return ("⚠️ No energy/consumption states exposed by your devices.\n"
                            "Run 'cozy raw' to inspect all states.")

                # Persist a daily snapshot and compute month-to-date / year-to-date
                # deltas. Snapshots live in tools/cozytouch_energy_snapshots.json
                # next to this file so they survive bot restarts.
                snap_path = Path(__file__).resolve().parent / "cozytouch_energy_snapshots.json"
                now = datetime.now(timezone.utc)
                today_key = now.strftime("%Y-%m-%d")
                snapshots: list[dict] = []
                if snap_path.exists():
                    try:
                        snapshots = _json.loads(snap_path.read_text("utf-8"))
                        if not isinstance(snapshots, list):
                            snapshots = []
                    except Exception:
                        snapshots = []

                # Only record one entry per day (keep the latest write of the day).
                snapshots = [s for s in snapshots if s.get("date") != today_key]
                snapshots.append({"date": today_key, "ts": now.isoformat(), "wh": current_wh})
                # Trim to last ~400 days to keep file bounded.
                snapshots = sorted(snapshots, key=lambda s: s.get("date", ""))[-400:]
                try:
                    snap_path.write_text(_json.dumps(snapshots, indent=2), "utf-8")
                except Exception:
                    pass

                def _baseline_on_or_before(date_key: str) -> dict | None:
                    """Latest snapshot whose date <= date_key (excluding today)."""
                    candidates = [s for s in snapshots
                                  if s.get("date") and s["date"] <= date_key and s["date"] != today_key]
                    return candidates[-1] if candidates else None

                month_start_key = now.strftime("%Y-%m-01")
                year_start_key = now.strftime("%Y-01-01")
                # Baseline = last snapshot strictly before the period start.
                # That snapshot's reading approximates the counter value at the
                # period boundary. If no snapshot exists from before the period,
                # we can't compute the delta accurately.
                from datetime import date as _date, timedelta as _td
                month_prev_day = (_date.fromisoformat(month_start_key) - _td(days=1)).isoformat()
                year_prev_day = (_date.fromisoformat(year_start_key) - _td(days=1)).isoformat()
                month_base = _baseline_on_or_before(month_prev_day)
                year_base = _baseline_on_or_before(year_prev_day)
                first_snap = snapshots[0] if snapshots else None

                def _delta_lines(base: dict | None, header: str) -> list[str]:
                    if not base or not base.get("wh"):
                        return []
                    out = [header]
                    for k, v in current_wh.items():
                        b = base["wh"].get(k)
                        if isinstance(b, (int, float)):
                            kwh = (v - b) / 1000.0
                            if kwh >= 0:
                                out.append(f"• {k}: {kwh:.1f} kWh")
                    return out if len(out) > 1 else []

                out = ["⚡ Cozytouch energy"]
                if power_lines:
                    out.append("\nCurrent power draw:")
                    out.extend(power_lines)
                else:
                    out.append("\nCompressor and backups are idle.")

                out.append("\nLifetime energy:")
                for k, v in current_wh.items():
                    out.append(f"• {k}: {v / 1000:.1f} kWh")

                mtd = _delta_lines(month_base, f"\nMonth-to-date (since {month_start_key}):")
                ytd = _delta_lines(year_base, f"\nYear-to-date (since {year_start_key}):")
                if mtd:
                    out.extend(mtd)
                if ytd:
                    out.extend(ytd)
                if not mtd and not ytd and first_snap:
                    out.append(
                        f"\n_Building history… first snapshot recorded {first_snap['date']}._"
                        "\nRun 'cozy energy' regularly (or once a day) and per-month / per-year"
                        " breakdowns will appear automatically."
                    )
                elif not mtd and not ytd:
                    out.append(
                        "\n_First snapshot recorded today. Per-month and per-year"
                        " totals will appear from the next call onward._"
                    )
                return "\n".join(out)

            if action in ("set", "setpoint", "temp"):
                if not extra:
                    return "Usage: cozy set <temperature>"
                try:
                    temp = float(extra[0].replace(",", "."))
                except ValueError:
                    return f"❌ Bad temperature: {extra[0]}"
                zones = [d for d in devs if _classify(d) == "zone"]
                if not zones:
                    return "No heating zone device found. Run 'cozy devices'."
                zone = zones[0]
                await _exec(session, "Set derogated temperature", zone["deviceURL"],
                            "setDerogatedTargetTemperature", [temp])
                try:
                    await _exec(session, "Enable derogation", zone["deviceURL"],
                                "setDerogationOnOffState", ["on"])
                except Exception:
                    pass
                return f"✅ Target set to {temp}°C on {zone.get('label')} (derogation)."

            if action in ("warmer", "cooler"):
                try:
                    delta = float((extra[0] if extra else "1").replace(",", "."))
                except ValueError:
                    delta = 1.0
                if action == "cooler":
                    delta = -delta
                zones = [d for d in devs if _classify(d) == "zone"]
                if not zones:
                    return "No heating zone device found. Run 'cozy devices'."
                zone = zones[0]
                current = (
                    _state(zone, "core:TargetTemperatureState")
                    or _state(zone, "core:HeatingTargetTemperatureState")
                )
                if current is None:
                    return "❌ Could not read current target temperature."
                new_temp = round(float(current) + delta, 1)
                await _exec(session, "Set derogated temperature", zone["deviceURL"],
                            "setDerogatedTargetTemperature", [new_temp])
                try:
                    await _exec(session, "Enable derogation", zone["deviceURL"],
                                "setDerogationOnOffState", ["on"])
                except Exception:
                    pass
                if is_voice and is_russian:
                    return f"Цель изменена с {current} на {new_temp} градусов."
                if is_voice:
                    return f"Target changed from {current} to {new_temp} degrees."
                return f"✅ Target {current}°C → {new_temp}°C on {zone.get('label')}."

            if action == "mode":
                if not extra:
                    return "Usage: cozy mode <stop|heating|cooling>"
                m = extra[0].lower()
                if m == "off":
                    m = "stop"
                if m not in ("stop", "heating", "cooling"):
                    return f"❌ Unknown mode '{extra[0]}'. Use stop/heating/cooling."
                hp = next((d for d in devs if _classify(d) == "heatpump"), None)
                if not hp:
                    return "No heat pump main component found."
                await _exec(session, "Set operating mode", hp["deviceURL"],
                            "setPassAPCOperatingMode", [m])
                return f"✅ Heat pump mode set to {m} on {hp.get('label')}."

            if action == "preset":
                if not extra:
                    return "Usage: cozy preset <comfort|eco|away|frost|auto|manu>"
                p = extra[0].lower()
                mapping = {
                    "comfort": "comfort", "eco": "eco",
                    "away": "absence", "absence": "absence",
                    "frost": "frostprotection", "frostprotection": "frostprotection",
                    "auto": "internalScheduling", "schedule": "internalScheduling",
                    "manu": "manu", "manual": "manu",
                    "stop": "stop", "off": "off",
                }
                if p not in mapping:
                    return f"❌ Unknown preset '{extra[0]}'."
                zones = [d for d in devs if _classify(d) == "zone"]
                if not zones:
                    return "No heating zone device found."
                zone = zones[0]
                await _exec(session, "Set heating preset", zone["deviceURL"],
                            "setPassAPCHeatingMode", [mapping[p]])
                return f"✅ Zone preset set to {mapping[p]} on {zone.get('label')}."

            if action in ("hotwater", "dhw"):
                if not extra:
                    return "Usage: cozy hotwater <on|off|boost>"
                sub = extra[0].lower()
                dhw = next((d for d in devs if _classify(d) == "dhw"), None)
                if not dhw:
                    return "No DHW device found."
                if sub == "boost":
                    await _exec(session, "Boost DHW", dhw["deviceURL"],
                                "setBoostMode", ["on"])
                    return f"✅ DHW boost ON on {dhw.get('label')}."
                if sub in ("on", "off"):
                    try:
                        await _exec(session, f"DHW {sub}", dhw["deviceURL"],
                                    "setDHWMode",
                                    ["manualEcoActive" if sub == "on" else "off"])
                    except Exception:
                        await _exec(session, f"DHW {sub}", dhw["deviceURL"],
                                    "setOnOff", [sub])
                    return f"✅ DHW {sub} on {dhw.get('label')}."
                return f"❌ Unknown hotwater action '{sub}'. Use on/off/boost."

            return USAGE
    except Exception as exc:
        return f"❌ Cozytouch error: {type(exc).__name__}: {exc}"
