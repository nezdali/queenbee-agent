async def run(context: dict) -> str:
    try:
        import aiohttp
        import asyncio
        import json
        import re
        from datetime import datetime, timezone

        args = context.get("args", []) or []
        if not args:
            return "Укажите username из Duolingo. Пример: /duolingo username"

        username = args[0].strip().lstrip("@")
        if not re.match(r"^[A-Za-z0-9_.-]{2,64}$", username):
            return "Некорректный username Duolingo. Используйте только буквы, цифры, _, ., -"

        headers = {
            "User-Tool": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"
        }

        async def fetch_json(session, url):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        return {"ok": False, "status": resp.status, "text": text[:500], "url": url}
                    try:
                        return {"ok": True, "data": json.loads(text), "url": url}
                    except Exception:
                        return {"ok": False, "status": resp.status, "text": text[:500], "url": url}
            except Exception as e:
                return {"ok": False, "error": str(e), "url": url}

        async def fetch_text(session, url):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    text = await resp.text()
                    return {"ok": resp.status == 200, "status": resp.status, "text": text, "url": url}
            except Exception as e:
                return {"ok": False, "error": str(e), "url": url, "text": ""}

        def pick(dct, *paths, default=None):
            for path in paths:
                cur = dct
                ok = True
                for part in path:
                    if isinstance(cur, dict) and part in cur:
                        cur = cur[part]
                    else:
                        ok = False
                        break
                if ok:
                    return cur
            return default

        def fmt_int(v):
            try:
                return f"{int(v):,}".replace(",", " ")
            except Exception:
                return str(v) if v is not None else "—"

        def fmt_date_s(s):
            """Format Unix timestamp in seconds."""
            try:
                return datetime.fromtimestamp(int(s), tz=timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                return None

        def fmt_date_ms(ms):
            try:
                return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                return None

        urls = [
            f"https://www.duolingo.com/2017-06-30/users?username={username}",
            f"https://www.duolingo.com/users/{username}",
            f"https://www.duolingo.com/2017-06-30/users?username={username}&fields=totalXp,streak,username,name,creationDate,hasPlus,subscriberLevel,currentCourseId,learningLanguage,fromLanguage,courses,profileCountry"
        ]

        async with aiohttp.ClientSession(headers=headers) as session:
            results = await asyncio.gather(*[fetch_json(session, u) for u in urls])
            web_profile = await fetch_text(session, f"https://www.duolingo.com/profile/{username}")

        data_sources = []
        for r in results:
            if r.get("ok") and isinstance(r.get("data"), dict):
                data_sources.append((r["url"], r["data"]))

        merged = {}
        chosen_url = None
        for url, data in data_sources:
            if not chosen_url:
                chosen_url = url
            users = data.get("users")
            if isinstance(users, list) and users:
                user0 = users[0]
                if isinstance(user0, dict):
                    merged.update(user0)
            if any(k in data for k in ["username", "totalXp", "streak", "courses"]):
                merged.update(data)

        if not merged:
            if web_profile.get("ok") and web_profile.get("text"):
                text = web_profile.get("text", "")
                if "not found" in text.lower() or "404" in text[:200].lower():
                    return f"Пользователь Duolingo *{username}* не найден."
            details = []
            for r in results:
                if r.get("ok") is False:
                    if "status" in r:
                        details.append(f"{r.get('status')} {r.get('url')}")
                    elif "error" in r:
                        details.append(f"{r.get('error')} {r.get('url')}")
            msg = "; ".join(details[:3]) if details else "не удалось получить данные"
            return f"Не получилось получить статистику Duolingo для *{username}*: {msg}"

        uname = merged.get("username") or username
        display_name = merged.get("name") or merged.get("fullname") or uname
        total_xp = merged.get("totalXp")
        streak_raw = merged.get("streak")
        streak_current = pick(merged, ("streakData", "currentStreak"))
        # currentStreak can be None, an int, or a dict {"length": N, "startDate": ..., "endDate": ...}
        if isinstance(streak_current, dict):
            streak = streak_current.get("length")
        elif streak_current is not None:
            streak = streak_current
        else:
            streak = streak_raw
        creation = merged.get("creationDate") or merged.get("joinedClassroomAt")
        has_plus = merged.get("hasPlus")
        profile_country = merged.get("profileCountry") or merged.get("country")
        learning_language = merged.get("learningLanguage") or merged.get("currentCourseId")
        from_language = merged.get("fromLanguage")
        courses = merged.get("courses") if isinstance(merged.get("courses"), list) else []
        gems = merged.get("gems") or merged.get("lingots")
        followers = merged.get("followersCount") or merged.get("numFollowers")
        following = merged.get("followingCount") or merged.get("numFollowing")

        course_lines = []
        if courses:
            for c in courses[:5]:
                if not isinstance(c, dict):
                    continue
                title = c.get("title") or c.get("learningLanguage") or c.get("id") or "course"
                xp = c.get("xp") or c.get("totalXp")
                crowns = c.get("crowns") or c.get("crownsTotal")
                line = f"- {title}"
                extra = []
                if xp is not None:
                    extra.append(f"XP {fmt_int(xp)}")
                if crowns is not None:
                    extra.append(f"crowns {fmt_int(crowns)}")
                if extra:
                    line += " — " + ", ".join(extra)
                course_lines.append(line)

        html_hints = []
        if web_profile.get("ok") and web_profile.get("text"):
            text = web_profile["text"]
            streak_match = re.search(r'"streakData"\s*:\s*\{[^}]*?"currentStreak"\s*:\s*(\d+)', text)
            xp_match = re.search(r'"totalXp"\s*:\s*(\d+)', text)
            if streak_match and not streak:
                streak = int(streak_match.group(1))
                html_hints.append("streak из HTML")
            if xp_match and total_xp is None:
                total_xp = int(xp_match.group(1))
                html_hints.append("XP из HTML")

        lines = []
        lines.append(f"*Duolingo статистика*: {display_name} (@{uname})")
        lines.append(f"- Total XP: {fmt_int(total_xp)}")
        lines.append(f"- Streak: {fmt_int(streak)} days")
        if gems is not None:
            lines.append(f"- Gems/Lingots: {fmt_int(gems)}")
        if followers is not None or following is not None:
            lines.append(f"- Followers/Following: {fmt_int(followers)}/{fmt_int(following)}")
        if learning_language or from_language:
            lines.append(f"- Course: {learning_language or '—'} from {from_language or '—'}")
        if profile_country:
            lines.append(f"- Country: {profile_country}")
        if creation:
            # creationDate from Duolingo API is Unix seconds, not milliseconds
            d = fmt_date_s(creation) or fmt_date_ms(creation)
            if d:
                lines.append(f"- Joined: {d}")
        if has_plus is not None:
            lines.append(f"- Super/Plus: {'yes' if has_plus else 'no'}")
        if course_lines:
            lines.append("- Courses:")
            lines.extend(course_lines)

        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при получении статистики Duolingo: {str(e)}"