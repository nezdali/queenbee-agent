async def run(context: dict) -> str:
    try:
        import os
        import re
        import aiohttp
        import datetime
        import json
        import calendar
        from urllib.parse import quote

        args = context.get("args", []) or []
        raw_text = (
            context.get("raw_extra")
            or context.get("raw_text")
            or " ".join(args)
        ).strip()

        api_key = os.getenv("OPENAI_ADMIN_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return "OPENAI_ADMIN_API_KEY is not configured."

        today = datetime.datetime.utcnow().date()

        def _add_months(d: datetime.date, n: int) -> datetime.date:
            m = d.month - 1 + n
            y = d.year + m // 12
            m = m % 12 + 1
            day = min(d.day, calendar.monthrange(y, m)[1])
            return datetime.date(y, m, day)

        def _parse_period(text: str) -> tuple[datetime.date, datetime.date] | None:
            """Parse natural-language period. Returns (start, end_inclusive) or None."""
            if not text:
                return None
            t = text.lower().strip()
            # Strip filler/prefix words so phrases like "stats last month",
            # "usage for last 3 months", "of the last week" still match.
            t = re.sub(
                r"^(?:stats?|usage|costs?|cost|status|report|info|"
                r"for|of|the|in|on|over|during|please|show|give\s+me|me)\s+",
                "",
                t,
            ).strip()
            # Iterate stripping leading filler words too (handles chains like "of the").
            while True:
                new = re.sub(
                    r"^(?:for|of|the|in|on|over|during)\s+",
                    "",
                    t,
                ).strip()
                if new == t:
                    break
                t = new
            if not t:
                return None
            # today / this day
            if t in ("today", "this day"):
                return today, today
            # yesterday / last day
            if t in ("yesterday", "last day"):
                d = today - datetime.timedelta(days=1)
                return d, d
            # this week (Mon..today)
            if t in ("this week", "week"):
                start = today - datetime.timedelta(days=today.weekday())
                return start, today
            # last week (previous Mon..Sun)
            if t == "last week":
                this_mon = today - datetime.timedelta(days=today.weekday())
                last_mon = this_mon - datetime.timedelta(days=7)
                last_sun = this_mon - datetime.timedelta(days=1)
                return last_mon, last_sun
            # this month
            if t in ("this month", "month"):
                return today.replace(day=1), today
            # last month
            if t == "last month":
                first_this = today.replace(day=1)
                last_prev = first_this - datetime.timedelta(days=1)
                first_prev = last_prev.replace(day=1)
                return first_prev, last_prev
            # this year / last year
            if t in ("this year", "year"):
                return today.replace(month=1, day=1), today
            if t == "last year":
                return datetime.date(today.year - 1, 1, 1), datetime.date(today.year - 1, 12, 31)
            # last N days/weeks/months/years
            m = re.fullmatch(r"last\s+(\d+)\s+(day|days|week|weeks|month|months|year|years)", t)
            if m:
                n = int(m.group(1))
                unit = m.group(2)
                if unit.startswith("day"):
                    return today - datetime.timedelta(days=n - 1), today
                if unit.startswith("week"):
                    return today - datetime.timedelta(days=7 * n - 1), today
                if unit.startswith("month"):
                    start = _add_months(today, -n)
                    start = start + datetime.timedelta(days=1)
                    return start, today
                if unit.startswith("year"):
                    try:
                        start = today.replace(year=today.year - n) + datetime.timedelta(days=1)
                    except ValueError:
                        start = today.replace(year=today.year - n, day=28) + datetime.timedelta(days=1)
                    return start, today
            return None

        start_date: datetime.date | None = None
        end_date: datetime.date | None = None

        # 1) Try natural-language phrase from raw_extra/raw_text/joined args
        period = _parse_period(raw_text)
        if period:
            start_date, end_date = period

        # 2) Fall back to YYYY-MM-DD positional args
        if start_date is None and len(args) >= 1:
            try:
                start_date = datetime.datetime.strptime(args[0], "%Y-%m-%d").date()
            except Exception:
                pass
        if end_date is None and len(args) >= 2:
            try:
                end_date = datetime.datetime.strptime(args[1], "%Y-%m-%d").date()
            except Exception:
                pass

        # 3) Default: month-to-date
        if start_date is None:
            start_date = today.replace(day=1)
        if end_date is None:
            end_date = today

        start_ts = int(calendar.timegm(start_date.timetuple()))
        end_ts = int(calendar.timegm(end_date.timetuple())) + 86400

        try:
            import logging as _lg
            _lg.getLogger(__name__).info(
                "openai_usage period: raw=%r args=%r -> %s to %s",
                raw_text, args, start_date, end_date,
            )
        except Exception:
            pass

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        lines = [f"📊 OpenAI API Usage: {start_date} to {end_date}\n"]

        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. Total costs
            try:
                total_cost = 0.0
                costs_url = f"https://api.openai.com/v1/organization/costs?start_time={start_ts}&end_time={end_ts}&limit=31"
                async with session.get(costs_url) as resp:
                    if resp.status != 200:
                        lines.append(f"⚠️ Costs API: HTTP {resp.status}")
                    else:
                        data = json.loads(await resp.text())
                        for item in data.get("data", []):
                            for r in item.get("results", []):
                                amt = r.get("amount", {})
                                if isinstance(amt, dict):
                                    total_cost += float(amt.get("value", 0))
                                elif isinstance(amt, (int, float)):
                                    total_cost += float(amt)
                lines.append(f"💰 Total cost: ${total_cost:.4f}")
            except Exception as e:
                lines.append(f"⚠️ Costs API error: {e}")

            # 2. Completions usage grouped by model
            completions_url = (
                f"https://api.openai.com/v1/organization/usage/completions"
                f"?start_time={start_ts}&end_time={end_ts}"
                f"&group_by[]=model&bucket_width=1d&limit=31"
            )
            try:
                model_stats = {}
                page_url = completions_url
                while page_url:
                    async with session.get(page_url) as resp:
                        if resp.status != 200:
                            lines.append(f"⚠️ Completions API: HTTP {resp.status}")
                            break
                        data = json.loads(await resp.text())
                        for bucket in data.get("data", []):
                            for r in bucket.get("results", []):
                                model = r.get("model") or "unknown"
                                if model not in model_stats:
                                    model_stats[model] = {"input": 0, "output": 0, "cached": 0, "requests": 0}
                                model_stats[model]["input"] += r.get("input_tokens", 0)
                                model_stats[model]["output"] += r.get("output_tokens", 0)
                                model_stats[model]["cached"] += r.get("input_cached_tokens", 0)
                                model_stats[model]["requests"] += r.get("num_model_requests", 0)
                        if data.get("has_more") and data.get("next_page"):
                            page_url = (
                                f"https://api.openai.com/v1/organization/usage/completions"
                                f"?start_time={start_ts}&end_time={end_ts}"
                                f"&group_by[]=model&bucket_width=1d&limit=31"
                                f"&page={quote(data['next_page'])}"
                            )
                        else:
                            page_url = None

                if model_stats:
                    lines.append("\n📋 Per-model breakdown:")
                    total_input = 0
                    total_output = 0
                    total_requests = 0
                    for model in sorted(model_stats, key=lambda m: model_stats[m]["requests"], reverse=True):
                        s = model_stats[model]
                        total_input += s["input"]
                        total_output += s["output"]
                        total_requests += s["requests"]
                        cached_pct = f" ({s['cached']/s['input']*100:.0f}% cached)" if s["input"] > 0 and s["cached"] > 0 else ""
                        lines.append(
                            f"  • {model}\n"
                            f"    {s['requests']} requests | "
                            f"{s['input']:,} in{cached_pct} / {s['output']:,} out tokens"
                        )
                    lines.append(f"\n📈 Totals: {total_requests:,} requests | {total_input:,} in / {total_output:,} out tokens")
            except Exception as e:
                lines.append(f"⚠️ Completions usage error: {e}")

        lines.append(
            "\nTip: try `openai stats this month`, `last 3 months`, `last week`, "
            "`yesterday`, or explicit `YYYY-MM-DD YYYY-MM-DD`."
        )
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to fetch OpenAI API usage: {e}"