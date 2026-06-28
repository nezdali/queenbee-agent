import json as _json


def _is_hub(d: dict) -> bool:
    """Hubs/bridges (e.g. ZBBridge-P) have no switch/switches params and
    cannot be turned on/off. Exclude them from control commands."""
    name = (d.get("name") or "").lower()
    if "zbbridge" in name or "bridge" in name or "hub" in name:
        return True
    p = d.get("params", {}) or {}
    return "switch" not in p and "switches" not in p


async def _llm_dispatch(text: str, devices: list) -> list:
    """Convert natural language into [(action, deviceid, channels), ...] using the LLM.
    Returns [] when LLM unavailable or no devices match."""
    if not text or not devices:
        return []
    try:
        from services.llm_service import client
        from config import LLM_MODEL
    except Exception:
        return []

    device_lines = []
    for d in devices:
        name = d.get("name", "?")
        did = d.get("deviceid", "?")
        online = "online" if d.get("online") else "offline"
        p = d.get("params") or {}
        if "switches" in p:
            n_ch = len(p["switches"])
            chs = ",".join(f"ch{i+1}={s.get('switch','?')}" for i, s in enumerate(p["switches"]))
            state = f"multi[{chs}] (channels 1..{n_ch})"
        else:
            state = p.get("switch", "?")
        device_lines.append(f'- id="{did}" name="{name}" state={state} {online}')

    system = (
        "You control eWeLink smart-home devices. The user may speak in ANY language "
        "(English, Russian, Estonian, etc.). Match user intent to one or more devices "
        "from the list and emit a JSON action plan.\n\n"
        "DEVICES:\n" + "\n".join(device_lines) + "\n\n"
        "Output STRICT JSON ONLY (no commentary, no markdown):\n"
        '{ "actions": [ {"deviceid": "<id>", "action": "on" | "off" | "toggle" | "status", "channels": [<1-based ints>] } ] }\n\n'
        "Channel rules:\n"
        '- For multi-channel devices (state shows "multi[...]"), if the user names specific '
        'channels (e.g. "channel 1", "ch2", "первый канал", "второй"), include them in "channels" '
        '(1-based: channel 1 -> [1], channel 2 -> [2], etc.).\n'
        '- If the user wants different actions on different channels of the same device '
        '(e.g. "in the living room turn off channel 1 and turn on channel 2"), emit MULTIPLE '
        'action entries for the same deviceid, each with its own "channels" list.\n'
        '- Omit "channels" (or use []) to act on ALL channels of a multi-channel device.\n'
        '- For single-channel devices, omit "channels".\n\n'
        "Matching rules:\n"
        '- "all", "everywhere", "everywhere in the house", "везде", "все", "всё", "kõik" -> include EVERY device.\n'
        '- "in the X room", "в комнате X", "у X-а/у X-и", "у бабушки", "у Маши", "у Марка", '
        '"living room", "kitchen", "bedroom", "hallway" -> match device whose name (case-insensitive) '
        'contains the room/person. Account for Russian declensions: Маша/Маши/Маше/Машу -> "Masha"; '
        'Марк/Марка/Марку -> "Mark"; бабушка/бабушки/бабушке -> "Granny"/"Babushka"/"Nonna"; '
        'гостиная/гостиной -> "Living"; кухня -> "Kitchen"; спальня -> "Bedroom"; '
        'коридор/прихожая -> "Hallway".\n'
        '- Action verbs: "turn on", "switch on", "включи", "врубай", "зажги", "lülita sisse" -> "on".\n'
        '- "turn off", "switch off", "выключи", "погаси", "вырубай", "lülita välja" -> "off".\n'
        '- "toggle", "переключи", "lülita ümber" -> "toggle".\n'
        '- "status", "состояние", "что со светом" -> "status".\n'
        '- The word "light"/"свет"/"tuli" implies the lighting devices; if the device list contains '
        '  non-light items (sockets, fans), prefer obvious light fixtures by name.\n'
        '- If no devices match, return { "actions": [] }.\n'
    )

    try:
        resp = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        try:
            import logging as _logging
            _logging.getLogger(__name__).info(
                "ewelink LLM dispatch input=%r raw=%r", text, raw
            )
        except Exception:
            pass
        data = _json.loads(raw)
    except Exception as exc:
        try:
            import logging as _logging
            _logging.getLogger(__name__).warning("ewelink LLM dispatch failed: %s", exc)
        except Exception:
            pass
        return []

    valid_ids = {d.get("deviceid") for d in devices}
    out = []
    for a in (data.get("actions") or []):
        did = a.get("deviceid")
        action = (a.get("action") or "").lower()
        if did not in valid_ids or action not in ("on", "off", "toggle", "status"):
            continue
        raw_chs = a.get("channels")
        chs: list | None = None
        if isinstance(raw_chs, list) and raw_chs:
            # Convert 1-based -> 0-based, drop invalid entries
            chs = []
            for c in raw_chs:
                try:
                    idx = int(c) - 1
                except (TypeError, ValueError):
                    continue
                if idx >= 0:
                    chs.append(idx)
            chs = chs or None
        out.append((action, did, chs))
    return out


async def run(context: dict) -> str:
    """Control eWeLink smart home devices (Zigbee/WiFi) via CoolKit API."""
    import os
    import aiohttp
    import base64
    import hashlib
    import hmac
    import json
    import time
    import uuid

    APP_ID = "R8Oq3y0eSZSYdKccHlrQzT1ACCOUT9Gv"
    APP_SECRET = b"1ve5Qk9GXfUhKAn1svnKwpAlxXkMarru"

    email = os.getenv("EWELINK_EMAIL", "").strip()
    password = os.getenv("EWELINK_PASSWORD", "").strip()
    phone = os.getenv("EWELINK_PHONE", "").strip()
    region = os.getenv("EWELINK_REGION", "eu").strip().lower()

    # Detect Russian input so we can localize status / action messages.
    raw_for_lang = " ".join([
        str(context.get("raw_extra") or ""),
        str(context.get("trigger_word") or ""),
        str(context.get("raw_text") or ""),
        " ".join(str(a) for a in (context.get("args") or [])),
    ])
    ru = any("\u0400" <= c <= "\u04FF" for c in raw_for_lang)

    # Action verb -> Russian past-tense participle for replies
    _RU_ACTION = {"on": "включено", "off": "выключено", "toggle": "переключено"}

    def _action_label(action: str) -> str:
        if ru:
            return _RU_ACTION.get(action, action)
        return action

    if not password or (not email and not phone):
        return "eWeLink not configured. Set EWELINK_EMAIL (or EWELINK_PHONE), EWELINK_PASSWORD, and optionally EWELINK_REGION env vars."

    raw = (context.get("raw_text") or "").strip()
    args = context.get("args") or []
    if not args and raw:
        args = raw.split()
    if not args:
        return (
            "💡 *Smart-home control*\n\n"
            "Speak naturally (any language):\n"
            "• `включи свет везде`\n"
            "• `выключи свет в комнате Маши`\n"
            "• `turn on light in living room`\n"
            "• `toggle Mark's room`\n\n"
            "Or structured:\n"
            "• `list` — list all devices\n"
            "• `on <name>` / `off <name>` / `toggle <name>` / `status <name>`\n"
            "• Multi-channel: `on <name> ch=1,3`"
        )

    command = args[0].lower()
    rest = args[1:]

    # Parse ch=X,Y from the end
    channels = None
    remaining = []
    for part in rest:
        if part.lower().startswith("ch="):
            try:
                channels = [int(c.strip()) for c in part[3:].split(",") if c.strip().isdigit()]
            except ValueError:
                pass
        else:
            remaining.append(part)
    device_query = " ".join(remaining).strip().lower()

    REGIONS = {"cn": "cn", "as": "as", "us": "us", "eu": "eu"}
    api_region = REGIONS.get(region, "eu")
    BASE = f"https://{api_region}-apia.coolkit.cc"

    def make_sign(body_bytes: bytes) -> str:
        return base64.b64encode(
            hmac.new(APP_SECRET, msg=body_bytes, digestmod=hashlib.sha256).digest()
        ).decode()

    def make_nonce() -> str:
        import random
        import string
        return "".join(random.choices(string.ascii_letters + string.digits, k=8))

    timeout = aiohttp.ClientTimeout(total=20)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # --- Login ---
            login_body = {"password": password, "countryCode": "+372"}
            if phone:
                login_body["phoneNumber"] = phone
            else:
                login_body["email"] = email

            body_bytes = json.dumps(login_body, separators=(",", ":")).encode()
            sign = make_sign(body_bytes)

            login_headers = {
                "Authorization": f"Sign {sign}",
                "Content-Type": "application/json",
                "X-CK-Appid": APP_ID,
                "X-CK-Nonce": make_nonce(),
            }

            async with session.post(f"{BASE}/v2/user/login", headers=login_headers, data=body_bytes) as resp:
                login_data = await resp.json()

            if login_data.get("error", 0) == 10004:
                # Redirect to correct region
                new_region = login_data.get("data", {}).get("region", api_region)
                BASE = f"https://{new_region}-apia.coolkit.cc"
                async with session.post(f"{BASE}/v2/user/login", headers=login_headers, data=body_bytes) as resp:
                    login_data = await resp.json()

            if login_data.get("error", 0) != 0:
                return f"eWeLink login failed: {login_data.get('msg', 'unknown error')} (code {login_data.get('error')})"

            at = login_data.get("data", {}).get("at")
            if not at:
                return "eWeLink login succeeded but no access token returned."

            auth_headers = {
                "Authorization": f"Bearer {at}",
                "Content-Type": "application/json",
                "X-CK-Appid": APP_ID,
                "X-CK-Nonce": make_nonce(),
            }

            # --- Get devices ---
            async with session.get(
                f"{BASE}/v2/device/thing",
                headers=auth_headers,
                params={"num": 0},
            ) as resp:
                things_data = await resp.json()

            if things_data.get("error", 0) != 0:
                return f"Failed to get devices: {things_data.get('msg', 'unknown')} (code {things_data.get('error')})"

            thing_list = things_data.get("data", {}).get("thingList", [])
            devices = []
            for thing in thing_list:
                item_type = thing.get("itemType")
                item_data = thing.get("itemData", {})
                if item_type in (1, 2):  # own device or shared
                    devices.append(item_data)

            controllable = [d for d in devices if not _is_hub(d)]

            if command == "list":
                if not devices:
                    return ("Устройства в аккаунте eWeLink не найдены." if ru
                            else "No devices found in your eWeLink account.")
                header = "*Ваши устройства eWeLink:*\n" if ru else "*Your eWeLink devices:*\n"
                lines = [header]
                for d in devices:
                    name = d.get("name", "?")
                    did = d.get("deviceid", "?")
                    online = "🟢" if d.get("online") else "🔴"
                    params = d.get("params", {})
                    sw = params.get("switch", None)
                    switches = params.get("switches", None)
                    if switches:
                        if ru:
                            ch_states = []
                            for i, s in enumerate(switches):
                                v = "вкл" if s.get('switch') == 'on' else "выкл"
                                ch_states.append(f"ch{i+1}={v}")
                        else:
                            ch_states = []
                            for i, s in enumerate(switches):
                                ch_states.append(f"ch{i+1}={'on' if s.get('switch')=='on' else 'off'}")
                        state_str = " ".join(ch_states)
                    elif sw:
                        state_str = ("вкл" if sw == "on" else "выкл") if ru else sw
                    else:
                        state_str = "хаб (не управляется)" if ru else "hub (not controllable)"
                    lines.append(f"{online} *{name}* — {state_str}  `{did}`")
                return "\n".join(lines)

            # ---------- Per-device control helper ----------
            async def _control_device(d: dict, action: str, chs: list | None) -> str:
                did = d["deviceid"]
                dname = d.get("name", did)
                p = d.get("params", {}) or {}
                multi = "switches" in p

                if action == "status":
                    if ru:
                        online = "🟢 онлайн" if d.get("online") else "🔴 оффлайн"
                        sw_label = {"on": "включено", "off": "выключено"}
                    else:
                        online = "🟢 online" if d.get("online") else "🔴 offline"
                        sw_label = {}
                    if multi:
                        ch_lines = "\n".join(
                            f"  ch{i+1}: {sw_label.get(s.get('switch'), s.get('switch','?'))}"
                            for i, s in enumerate(p.get("switches", []))
                        )
                        return f"*{dname}* — {online}\n{ch_lines}"
                    sw_val = p.get('switch', '?')
                    sw_show = sw_label.get(sw_val, sw_val)
                    label = "состояние" if ru else "switch"
                    return f"*{dname}* — {online}, {label}: {sw_show}"

                if not d.get("online"):
                    return (f"⚠️ *{dname}* оффлайн — пропущено" if ru
                            else f"⚠️ *{dname}* offline — skipped")

                if action == "toggle":
                    if multi:
                        switches = p.get("switches", [])
                        target = chs if chs else list(range(len(switches)))
                        new_switches = []
                        for i, s in enumerate(switches):
                            if i in target:
                                new_sw = "off" if s.get("switch") == "on" else "on"
                                new_switches.append({"switch": new_sw, "outlet": i})
                            else:
                                new_switches.append({"switch": s.get("switch", "off"), "outlet": i})
                        ctrl = {"switches": new_switches}
                    else:
                        cur = p.get("switch", "off")
                        ctrl = {"switch": "off" if cur == "on" else "on"}
                elif action in ("on", "off"):
                    if multi:
                        switches = p.get("switches", [])
                        target = chs if chs else list(range(len(switches)))
                        new_switches = []
                        for i, s in enumerate(switches):
                            if i in target:
                                new_switches.append({"switch": action, "outlet": i})
                            else:
                                new_switches.append({"switch": s.get("switch", "off"), "outlet": i})
                        ctrl = {"switches": new_switches}
                    else:
                        ctrl = {"switch": action}
                else:
                    return f"❌ unknown action `{action}` for *{dname}*"

                body = json.dumps(
                    {"type": 1, "id": did, "params": ctrl},
                    separators=(",", ":"),
                )
                async with session.post(
                    f"{BASE}/v2/device/thing/status",
                    headers=auth_headers,
                    data=body,
                ) as r:
                    cd = await r.json()
                if cd.get("error", 0) != 0:
                    return f"❌ *{dname}* {action}: {cd.get('msg','err')} ({cd.get('error')})"
                _act = _action_label(action)
                if multi and chs:
                    ch_str = ",".join(str(c + 1) for c in chs)
                    return f"✅ *{dname}* ch{ch_str} → {_act}"
                return f"✅ *{dname}* → {_act}"

            # ---------- Decide actions ----------
            STRUCTURED = {"on", "off", "toggle", "status"}
            actions: list = []

            # 1) Direct structured match: command + clear device-name substring
            if command in STRUCTURED and device_query:
                m = None
                for d in controllable:
                    if device_query == (d.get("name") or "").lower() or device_query == (d.get("deviceid") or "").lower():
                        m = d
                        break
                if not m:
                    for d in controllable:
                        if device_query in (d.get("name") or "").lower():
                            m = d
                            break
                if m:
                    actions = [(command, m["deviceid"], channels)]

            # 2) LLM fallback for natural language or no structured match
            if not actions:
                # Use the ORIGINAL untranslated message (trigger_word + raw_extra)
                # so the LLM sees the verb (включи/выключи) and original device names
                # (Маша, Марк, бабушка). The dispatcher strips the trigger word and
                # translates the rest, which loses both the verb and the Russian/Estonian
                # forms the LLM matches against device names.
                trigger_word = (context.get("trigger_word") or "").strip()
                raw_extra = (context.get("raw_extra") or "").strip()
                full_text = (trigger_word + " " + raw_extra).strip()
                if not full_text:
                    full_text = " ".join(args).strip()
                actions = await _llm_dispatch(full_text, controllable)

            if not actions:
                names = ", ".join(d.get("name", d.get("deviceid", "?")) for d in controllable)
                if ru:
                    return (
                        "🤔 Не понял, какие устройства/действия имеются в виду.\n"
                        f"Доступно: {names}\n"
                        "Попробуй `list` чтобы посмотреть устройства, или скажи естественно — "
                        "например `включи свет везде`, `выключи свет в комнате Маши`."
                    )
                return (
                    "🤔 I couldn't figure out which devices/actions you mean.\n"
                    f"Available: {names}\n"
                    "Try `list` to see devices, or speak naturally — "
                    "e.g. `включи свет везде`, `turn off light in living room`."
                )

            # ---------- Execute ----------
            results = []
            device_by_id = {d.get("deviceid"): d for d in controllable}
            for action, did, chs in actions:
                d = device_by_id.get(did)
                if d is None:
                    results.append(f"❌ unknown device `{did}`")
                    continue
                results.append(await _control_device(d, action, chs))
            return "\n".join(results)

    except aiohttp.ClientError as e:
        return f"Network error communicating with eWeLink: {e}"
    except Exception as e:
        return f"eWeLink tool error: {type(e).__name__}: {e}"
