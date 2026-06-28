async def run(context: dict) -> str:
    from pathlib import Path
    import json
    import time
    import re

    EPSILON = 1e-3

    def get_default_state():
        return {
            "stopwatch": {
                "running": False,
                "start_time": None,
                "elapsed": 0.0
            },
            "timer": {
                "running": False,
                "target_time": None,
                "duration": None,
                "remaining": None,
                "last_started": None
            }
        }

    def load_state(path: Path):
        state = get_default_state()
        if not path.exists():
            return state, None
        try:
            raw = path.read_text(encoding="utf-8")
            if not raw.strip():
                return state, None
            data = json.loads(raw)
        except Exception as exc:
            return state, f"State file invalid and was reset ({exc})."
        if isinstance(data, dict):
            sw_src = data.get("stopwatch")
            if isinstance(sw_src, dict):
                if "running" in sw_src:
                    state["stopwatch"]["running"] = bool(sw_src.get("running"))
                if "start_time" in sw_src:
                    try:
                        state["stopwatch"]["start_time"] = float(sw_src.get("start_time"))
                    except (TypeError, ValueError):
                        state["stopwatch"]["start_time"] = None
                if "elapsed" in sw_src:
                    try:
                        state["stopwatch"]["elapsed"] = max(0.0, float(sw_src.get("elapsed")))
                    except (TypeError, ValueError):
                        state["stopwatch"]["elapsed"] = 0.0
            timer_src = data.get("timer")
            if isinstance(timer_src, dict):
                if "running" in timer_src:
                    state["timer"]["running"] = bool(timer_src.get("running"))
                if "target_time" in timer_src:
                    try:
                        state["timer"]["target_time"] = float(timer_src.get("target_time"))
                    except (TypeError, ValueError):
                        state["timer"]["target_time"] = None
                if "duration" in timer_src:
                    try:
                        state["timer"]["duration"] = float(timer_src.get("duration"))
                    except (TypeError, ValueError):
                        state["timer"]["duration"] = None
                if "remaining" in timer_src:
                    try:
                        rem_val = timer_src.get("remaining")
                        state["timer"]["remaining"] = float(rem_val) if rem_val is not None else None
                    except (TypeError, ValueError):
                        state["timer"]["remaining"] = None
                if "last_started" in timer_src:
                    try:
                        state["timer"]["last_started"] = float(timer_src.get("last_started"))
                    except (TypeError, ValueError):
                        state["timer"]["last_started"] = None
        return state, None

    def save_state(path: Path, state: dict):
        try:
            serializable = {
                "stopwatch": {
                    "running": bool(state["stopwatch"].get("running")),
                    "start_time": float(state["stopwatch"].get("start_time")) if state["stopwatch"].get("start_time") is not None else None,
                    "elapsed": float(state["stopwatch"].get("elapsed", 0.0))
                },
                "timer": {
                    "running": bool(state["timer"].get("running")),
                    "target_time": float(state["timer"].get("target_time")) if state["timer"].get("target_time") is not None else None,
                    "duration": float(state["timer"].get("duration")) if state["timer"].get("duration") is not None else None,
                    "remaining": float(state["timer"].get("remaining")) if state["timer"].get("remaining") is not None else None,
                    "last_started": float(state["timer"].get("last_started")) if state["timer"].get("last_started") is not None else None
                }
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(serializable, indent=2, sort_keys=True), encoding="utf-8")
            return None
        except Exception as exc:
            return str(exc)

    def refresh_stopwatch(state: dict):
        changed = False
        sw = state["stopwatch"]
        if sw["running"] and sw["start_time"] is None:
            sw["running"] = False
            changed = True
        if sw["elapsed"] < 0:
            sw["elapsed"] = 0.0
            changed = True
        return changed

    def refresh_timer(state: dict):
        changed = False
        timer = state["timer"]
        now = time.time()
        if timer["running"]:
            target = timer.get("target_time")
            if target is None:
                timer["running"] = False
                timer["remaining"] = timer.get("duration")
                changed = True
            else:
                remaining = target - now
                if remaining <= EPSILON:
                    timer["running"] = False
                    timer["target_time"] = None
                    timer["remaining"] = 0.0
                    changed = True
        else:
            if timer.get("target_time") is not None:
                timer["target_time"] = None
                changed = True
        if timer.get("remaining") is not None and timer["remaining"] < 0:
            timer["remaining"] = 0.0
            changed = True
        return changed

    def format_stopwatch(seconds: float):
        seconds = max(0.0, float(seconds))
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:05.2f}"

    def format_timer(seconds: float):
        seconds = max(0.0, float(seconds))
        total = int(round(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def parse_duration(tokens):
        if not tokens:
            return None
        combined = "".join(str(token) for token in tokens)
        combined = combined.lower().replace(" ", "")
        if not combined:
            return None
        replacements = {
            "hours": "h",
            "hour": "h",
            "hrs": "h",
            "hr": "h",
            "minutes": "m",
            "minute": "m",
            "mins": "m",
            "min": "m",
            "seconds": "s",
            "second": "s",
            "secs": "s",
            "sec": "s"
        }
        for key, val in replacements.items():
            if key in combined:
                combined = combined.replace(key, val)
        if ":" in combined and all(c.isdigit() or c == ":" for c in combined):
            parts = combined.split(":")
            if not 1 <= len(parts) <= 3:
                return None
            try:
                parts = [float(p) for p in parts]
            except ValueError:
                return None
            if len(parts) == 3:
                hours, minutes, seconds_val = parts
            elif len(parts) == 2:
                hours = 0.0
                minutes, seconds_val = parts
            else:
                hours = 0.0
                minutes = 0.0
                seconds_val = parts[0]
            total_seconds = hours * 3600 + minutes * 60 + seconds_val
            return total_seconds if total_seconds > 0 else None
        matches = re.findall(r"(\d+(?:\.\d+)?)([hms])", combined)
        if matches:
            total_seconds = 0.0
            consumed = 0
            for value, unit in matches:
                consumed += len(value) + len(unit)
                val = float(value)
                if unit == "h":
                    total_seconds += val * 3600
                elif unit == "m":
                    total_seconds += val * 60
                elif unit == "s":
                    total_seconds += val
            if consumed != len(combined):
                return None
            return total_seconds if total_seconds > 0 else None
        try:
            value = float(combined)
            return value if value > 0 else None
        except ValueError:
            return None

    def usage_text():
        return (
            "Timer & Stopwatch commands:\n"
            "- stopwatch start | stop | status | reset\n"
            "- timer start <duration> | stop | status | reset\n"
            "Durations can be like 90, 1m30s, 2h, or 00:05:00."
        )

    def build_status(state: dict):
        now = time.time()
        sw = state["stopwatch"]
        elapsed = sw.get("elapsed", 0.0)
        if sw.get("running"):
            start_time = sw.get("start_time") or now
            elapsed_display = elapsed + max(0.0, now - start_time)
            sw_status = f"Stopwatch: running ({format_stopwatch(elapsed_display)})"
        else:
            sw_status = f"Stopwatch: stopped ({format_stopwatch(elapsed)})"
        timer = state["timer"]
        if timer.get("running"):
            target = timer.get("target_time") or now
            remaining = max(0.0, target - now)
            timer_status = f"Timer: running ({format_timer(remaining)} remaining)"
        else:
            remaining = timer.get("remaining")
            if remaining is not None:
                if remaining > EPSILON:
                    timer_status = f"Timer: paused ({format_timer(remaining)} remaining)"
                elif abs(remaining) <= EPSILON:
                    duration = timer.get("duration")
                    if duration:
                        timer_status = f"Timer: completed ({format_timer(duration)})"
                    else:
                        timer_status = "Timer: completed"
                else:
                    timer_status = "Timer: idle"
            else:
                duration = timer.get("duration")
                if duration:
                    timer_status = f"Timer: idle (last duration {format_timer(duration)})"
                else:
                    timer_status = "Timer: idle"
        return f"{sw_status}\n{timer_status}"

    def handle_stopwatch(state: dict, action: str):
        now = time.time()
        sw = state["stopwatch"]
        if action == "start":
            if sw.get("running"):
                elapsed = sw.get("elapsed", 0.0) + max(0.0, now - (sw.get("start_time") or now))
                message = "Stopwatch is already running.\nElapsed: " + format_stopwatch(elapsed)
                return message, False
            sw["running"] = True
            sw["start_time"] = now
            sw["elapsed"] = float(sw.get("elapsed", 0.0))
            message = "Stopwatch started.\nCurrent elapsed: " + format_stopwatch(sw["elapsed"])
            return message, True
        if action == "stop":
            if not sw.get("running"):
                message = "Stopwatch is already stopped.\nElapsed: " + format_stopwatch(sw.get("elapsed", 0.0))
                return message, False
            start_time = sw.get("start_time") or now
            elapsed = sw.get("elapsed", 0.0) + max(0.0, now - start_time)
            sw["elapsed"] = elapsed
            sw["running"] = False
            sw["start_time"] = None
            message = "Stopwatch paused at " + format_stopwatch(elapsed) + "."
            return message, True
        if action == "reset":
            sw["running"] = False
            sw["start_time"] = None
            sw["elapsed"] = 0.0
            return "Stopwatch reset to 00:00:00.00.", True
        elapsed = sw.get("elapsed", 0.0)
        if sw.get("running"):
            elapsed = elapsed + max(0.0, now - (sw.get("start_time") or now))
            status = "Stopwatch is running.\nElapsed: " + format_stopwatch(elapsed)
        else:
            status = "Stopwatch is stopped.\nElapsed: " + format_stopwatch(elapsed)
        return status, False

    def handle_timer(state: dict, action: str, extra_args):
        now = time.time()
        timer = state["timer"]
        if action == "start":
            if timer.get("running"):
                target = timer.get("target_time") or now
                remaining = max(0.0, target - now)
                message = "Timer is already running.\nTime remaining: " + format_timer(remaining)
                return message, False
            requested = parse_duration(extra_args)
            resumed = False
            reused_previous = False
            if requested is None:
                remaining = timer.get("remaining")
                if remaining is not None and remaining > EPSILON:
                    requested = float(remaining)
                    resumed = True
                elif timer.get("duration"):
                    requested = float(timer["duration"])
                    reused_previous = True
                else:
                    return "Please specify a duration, e.g. timer start 5m.", False
            if resumed and timer.get("duration"):
                base_duration = float(timer["duration"])
            else:
                timer["duration"] = requested
                base_duration = requested
            timer["running"] = True
            timer["target_time"] = now + requested
            timer["remaining"] = requested
            timer["last_started"] = now
            if resumed:
                message = "Timer resumed with " + format_timer(requested) + " remaining."
            elif reused_previous:
                message = "Timer restarted for " + format_timer(requested) + " (last saved duration)."
            else:
                message = "Timer started for " + format_timer(base_duration) + "."
            return message, True
        if action == "stop":
            if not timer.get("running"):
                remaining = timer.get("remaining")
                if remaining is not None and remaining > EPSILON:
                    message = "Timer is already paused with " + format_timer(remaining) + " remaining."
                    return message, False
                return "Timer is not running.", False
            target = timer.get("target_time") or now
            remaining = max(0.0, target - now)
            timer["running"] = False
            timer["target_time"] = None
            timer["remaining"] = remaining
            message = "Timer paused with " + format_timer(remaining) + " remaining."
            return message, True
        if action == "reset":
            timer["running"] = False
            timer["target_time"] = None
            timer["duration"] = None
            timer["remaining"] = None
            timer["last_started"] = None
            return "Timer reset.", True
        if timer.get("running"):
            target = timer.get("target_time") or now
            remaining = max(0.0, target - now)
            return "Timer is running.\nTime remaining: " + format_timer(remaining), False
        remaining = timer.get("remaining")
        if remaining is not None:
            if remaining > EPSILON:
                return "Timer is paused with " + format_timer(remaining) + " remaining.", False
            if abs(remaining) <= EPSILON:
                duration = timer.get("duration")
                if duration:
                    return "Timer completed (" + format_timer(duration) + ").", False
                return "Timer completed.", False
        duration = timer.get("duration")
        if duration:
            return "Timer is idle. Last duration: " + format_timer(duration) + ".", False
        return "Timer is idle.", False

    try:
        args = context.get("args") or []
        args = [arg for arg in args if isinstance(arg, str) and arg.strip()]
        trigger = (context.get("trigger_word") or "").strip().lower()
        # When dispatched by trigger keyword (e.g. "stopwatch start"), the
        # bot strips the trigger word from args. Prepend it so the parser
        # below sees ["stopwatch", "start"] regardless of dispatch path.
        if trigger in ("stopwatch", "timer") and (not args or args[0].lower() not in ("stopwatch", "timer")):
            args = [trigger] + args
        if not args:
            return usage_text()
        state_file = Path.home() / ".telegram_timekeeper" / "timekeeper_state.json"
        state, load_warning = load_state(state_file)
        state_changed = False
        state_changed = refresh_stopwatch(state) or state_changed
        state_changed = refresh_timer(state) or state_changed
        first = args[0].lower()
        action_aliases = {
            "start": "start",
            "run": "start",
            "resume": "start",
            "begin": "start",
            "go": "start",
            "stop": "stop",
            "pause": "stop",
            "halt": "stop",
            "status": "status",
            "state": "status",
            "check": "status",
            "reset": "reset",
            "restart": "reset",
            "clear": "reset"
        }
        message = ""
        updated = False
        if first in ("status", "overview"):
            message = build_status(state)
        elif first in ("stopwatch", "timer"):
            if len(args) > 1:
                action_token = args[1].lower()
                action = action_aliases.get(action_token)
                if not action:
                    return "Unknown action: " + args[1] + "\n" + usage_text()
                extra = args[2:]
            else:
                action = "status"
                extra = []
            if first == "stopwatch":
                message, updated = handle_stopwatch(state, action)
            else:
                message, updated = handle_timer(state, action, extra)
        else:
            return usage_text()
        total_changed = state_changed or updated
        save_error = None
        if total_changed:
            save_error = save_state(state_file, state)
        if load_warning:
            message = "⚠️ " + load_warning + "\n\n" + message
        if save_error:
            message += "\n⚠️ State persistence failed: " + save_error
        return message
    except Exception as exc:
        return f"Operation failed: {exc}"
