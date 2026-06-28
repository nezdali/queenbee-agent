async def run(context: dict) -> str:
    import re
    import string
    import aiohttp
    from bs4 import BeautifulSoup
    from tool_utils import parse_args

    URL = "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup"
    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    TIMEOUT = aiohttp.ClientTimeout(total=20)

    is_voice = bool(context.get("language"))
    language = (context.get("language") or "").lower()
    is_ru = language.startswith("ru")

    GENERIC = {
        "fb", "worldcup", "worldcup2026", "fifa", "мундиаль", "чм",
        "status", "results", "table", "standings", "group", "groups",
        "show", "me", "the", "of", "in", "at", "current", "now",
        "vs", "v", "against", "and", "or",
        "goal", "goals", "scored", "score", "scoring", "points", "stats",
        "how", "many", "much", "did", "has", "have", "had", "win", "wins",
        "won", "loss", "losses", "lost", "draw", "draws", "drew", "played",
        "matches", "games",
        "top", "best", "leading", "scorer", "scorers", "leaders", "list",
        "player", "players",
        "против", "и", "или", "сейчас", "статус", "результат",
        "счёт", "счет", "матч", "матча", "матче", "матчи", "группа", "группе",
        "футбол", "футбола",
        "гол", "голы", "голов", "голa", "забил", "забила", "забили", "забито",
        "сколько", "очков", "очки", "победы", "поражени", "ничь",
        "этом", "этого", "этой", "на",
        "бомбардир", "бомбардиры", "бомбардиров", "лучший", "лучшие",
        "игрок", "игроки", "игроков", "топ",
        "today", "tomorrow", "yesterday", "when", "kickoff", "schedule",
        "fixtures", "fixture", "upcoming", "next",
        "сегодня", "завтра", "вчера", "когда", "играет", "играют",
        "расписание", "будет", "следующ", "кто",
    }

    user_args = parse_args(context) or []
    raw_extra = (context.get("raw_extra") or "").lower()
    user_message = (context.get("user_message") or "").lower()

    tokens = [a.lower().strip(string.punctuation) for a in user_args if a and a.strip()]
    wants_group_view = any(t in {"group", "groups", "группа", "группе", "группы"} for t in tokens)
    wants_scorers = any(
        t in {"scorers", "scorer", "top", "leaders", "leading",
              "бомбардир", "бомбардиры", "бомбардиров", "топ"}
        for t in tokens
    ) or bool(re.search(r"top\s+scorer|leading\s+scorer|best\s+scorer|"
                        r"бомбардир|кто\s+(?:больше|лучший|лучше)\s+забил|"
                        r"топ\s+\d*\s*(?:бомбардир|игрок|голеад)",
                        f"{user_message} {raw_extra}"))
    blob = f"{user_message} {raw_extra}"
    wants_today = any(t in {"today", "сегодня"} for t in tokens) or bool(
        re.search(r"\btoday\b|сегодня|кто\s+играет\s+сегодня", blob))
    wants_tomorrow = any(t in {"tomorrow", "завтра"} for t in tokens) or bool(
        re.search(r"\btomorrow\b|завтра|кто\s+играет\s+завтра", blob))
    wants_yesterday = any(t in {"yesterday", "вчера"} for t in tokens) or bool(
        re.search(r"\byesterday\b|вчера", blob))
    query_tokens = [t for t in tokens if t and t not in GENERIC]

    requested_group_letter = None
    for t in tokens:
        if len(t) == 1 and t in "abcdefghijkl":
            requested_group_letter = t.upper()
            break
    for t in tokens:
        m = re.match(r"^(?:group)?([a-l])$", t)
        if m:
            requested_group_letter = m.group(1).upper()
            break

    team_query = " ".join(query_tokens).strip()
    if requested_group_letter and team_query.lower() in ("", requested_group_letter.lower()):
        team_query = ""
    RU_TEAM_HINTS = {
        "мексик": "mexico", "корея": "south korea", "чехи": "czech", "чехия": "czech",
        "южная африк": "south africa", "южноафрик": "south africa", "юар": "south africa",
        "швейцар": "switzerland", "канад": "canada", "катар": "qatar", "босни": "bosnia",
        "шотланди": "scotland", "марокк": "morocco", "бразили": "brazil", "гаити": "haiti",
        "сша": "united states", "америк": "united states", "австрали": "australia",
        "турци": "turkey", "парагва": "paraguay",
        "герман": "germany", "кюрасао": "curaçao", "кот-д": "ivory", "ивуар": "ivory",
        "эквадор": "ecuador",
        "нидерланд": "netherlands", "голланди": "netherlands", "япони": "japan",
        "швеци": "sweden", "тунис": "tunisia",
        "бельги": "belgium", "иран": "iran", "новая зеланди": "new zealand", "новозеланд": "new zealand",
        "испани": "spain", "саудовск": "saudi", "уругва": "uruguay",
        "франци": "france", "сенегал": "senegal", "ирак": "iraq", "норвеги": "norway",
        "аргентин": "argentina", "австри": "austria", "иордан": "jordan", "алжир": "algeria",
        "португал": "portugal", "конго": "congo", "узбек": "uzbekistan",
        "англи": "england", "хорват": "croatia", "гана": "ghana",
    }

    # If the team_query is in Russian / Cyrillic, translate it to English using
    # RU_TEAM_HINTS so the substring matcher works against the Wikipedia table.
    if team_query and re.search(r"[а-яё]", team_query, re.IGNORECASE):
        tq_lower = team_query.lower()
        for ru, en in RU_TEAM_HINTS.items():
            if ru in tq_lower:
                team_query = en
                break

    def clean(text):
        return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()

    def md_escape(text):
        return (text or "").replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")

    async def fetch(session, url):
        try:
            async with session.get(url) as resp:
                raw = await resp.read()
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("windows-1252", errors="replace")
                return text, resp.status
        except Exception:
            return None, 0

    def parse_standings_table(tbl):
        rows = []
        all_rows = tbl.find_all("tr")
        if not all_rows:
            return rows
        header_cells = [clean(c.get_text(" ")).lower() for c in all_rows[0].find_all(["th", "td"])]
        if "pos" not in header_cells or "pts" not in header_cells:
            return rows
        idx = {h: i for i, h in enumerate(header_cells)}
        min_cells = idx.get("pts", len(header_cells) - 1) + 1
        team_idx = idx.get("team v t e", idx.get("team", 1))
        for tr in all_rows[1:]:
            tds = tr.find_all(["th", "td"])
            if len(tds) < min_cells:
                continue
            cells = [clean(td.get_text(" ")) for td in tds]
            try:
                pos = cells[idx["pos"]].rstrip(".")
                if not pos.isdigit():
                    continue
                team_raw = cells[team_idx]
                team = re.sub(r"\s*v\s*t\s*e\s*$", "", team_raw).strip()
                rows.append({
                    "pos": pos,
                    "team": team,
                    "pld": cells[idx["pld"]],
                    "w": cells[idx["w"]],
                    "d": cells[idx["d"]],
                    "l": cells[idx["l"]],
                    "gf": cells[idx["gf"]],
                    "ga": cells[idx["ga"]],
                    "gd": cells[idx["gd"]],
                    "pts": cells[idx["pts"]].split("[")[0].strip(),
                })
            except Exception:
                continue
        return rows

    # ---- Kickoff time parsing (Wikipedia → UTC) ----
    from datetime import datetime, timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
        TALLINN_TZ = ZoneInfo("Europe/Tallinn")
    except Exception:
        TALLINN_TZ = timezone(timedelta(hours=3))  # EEST fallback (summer)

    def _parse_kickoff_to_utc(iso_date, time_text):
        """Convert Wikipedia kickoff like '7:00 p.m.  UTC−6' (+ ISO date) to
        an aware UTC datetime. Returns None on any parse failure."""
        if not iso_date or not time_text:
            return None
        try:
            # Normalize unicode minus to ASCII and NBSP to space
            t = time_text.replace("\u2212", "-").replace("\xa0", " ")
            # Extract hour, minute, optional am/pm
            m = re.search(r"(\d{1,2})[:\.](\d{2})\s*(a\.?m\.?|p\.?m\.?)?",
                          t, re.IGNORECASE)
            if not m:
                return None
            hr = int(m.group(1))
            mn = int(m.group(2))
            mer = (m.group(3) or "").lower().replace(".", "")
            if mer == "pm" and hr < 12:
                hr += 12
            elif mer == "am" and hr == 12:
                hr = 0
            # Extract UTC offset hours (default 0)
            off_m = re.search(r"UTC\s*([+-]?\d{1,2})(?::?(\d{2}))?", t)
            off_hours = int(off_m.group(1)) if off_m else 0
            off_mins = int(off_m.group(2)) if off_m and off_m.group(2) else 0
            offset = timedelta(hours=off_hours,
                               minutes=off_mins if off_hours >= 0 else -off_mins)
            tz = timezone(offset)
            y, mo, d = (int(x) for x in iso_date.split("-"))
            local_dt = datetime(y, mo, d, hr, mn, tzinfo=tz)
            return local_dt.astimezone(timezone.utc)
        except Exception:
            return None

    def parse_matches(soup):
        out = []
        for box in soup.select("div.footballbox"):
            tbl = box.find("table", class_="fevent")
            if not tbl:
                continue
            home = tbl.find("th", class_="fhome")
            score = tbl.find("th", class_="fscore")
            away = tbl.find("th", class_="faway")
            if not (home and score and away):
                continue
            home_name = clean(home.get_text(" ").replace("(H)", ""))
            away_name = clean(away.get_text(" ").replace("(H)", ""))
            score_text = clean(score.get_text(" "))
            date_div = box.select_one("div.fdate")
            date_text = clean(date_div.get_text(" ")) if date_div else ""
            iso_match = re.search(r"(\d{4}-\d{2}-\d{2})", date_text)
            iso_date = iso_match.group(1) if iso_match else ""
            date_text = re.sub(r"\s*\(\s*\d{4}-\d{2}-\d{2}\s*\)\s*", "", date_text)
            played = bool(re.match(r"^\d+\s*[–-]\s*\d+", score_text))
            # Kickoff: Wikipedia stores e.g. "7:00 p.m.  UTC−6" in div.ftime
            time_div = box.select_one("div.ftime")
            time_text = clean(time_div.get_text(" ")) if time_div else ""
            kickoff_utc = _parse_kickoff_to_utc(iso_date, time_text)
            out.append({
                "home": home_name,
                "away": away_name,
                "score": score_text,
                "date": date_text,
                "iso_date": iso_date,
                "time_text": time_text,
                "kickoff_utc": kickoff_utc,  # aware datetime in UTC, or None
                "played": played,
            })
        return out

    def parse_scorers(soup):
        """Return a list of {'player': str, 'country': str, 'goals': int}
        sorted by goals desc, then by name. Skips own-goals sections."""
        h = soup.find(lambda t: t.name in ("h2", "h3", "h4")
                      and "goalscor" in t.get_text(" ").lower())
        if not h:
            return []
        results = []
        current_goals = None
        in_own_goals = False
        for e in h.find_all_next(["h2", "h3", "h4", "p", "ul"], limit=40):
            if e.name in ("h2", "h3", "h4") and e is not h:
                break
            if e.name == "p":
                txt = e.get_text(" ").strip().lower()
                if "own goal" in txt:
                    in_own_goals = True
                    current_goals = None
                    continue
                m = re.match(r"(\d+)\s+goals?", txt)
                if m:
                    in_own_goals = False
                    current_goals = int(m.group(1))
                continue
            if e.name == "ul" and current_goals is not None and not in_own_goals:
                for li in e.find_all("li", recursive=False):
                    # Country from flag image alt (e.g. "Germany national football team")
                    country = ""
                    for img in li.find_all("img", alt=True):
                        alt = (img.get("alt") or "").strip()
                        if not alt:
                            continue
                        country = re.sub(
                            r"\s+(?:men'?s\s+)?national\s+(?:football|soccer)\s+team$",
                            "", alt, flags=re.IGNORECASE,
                        ).strip()
                        if country:
                            break
                    # Player name = first text-bearing <a> that is not the country link
                    player_name = ""
                    for a in li.find_all("a"):
                        txt = clean(a.get_text(" "))
                        if not txt:
                            continue
                        if txt.lower() == country.lower():
                            continue
                        player_name = txt
                        break
                    if not player_name:
                        player_name = clean(li.get_text(" ").split("(")[0])
                    if player_name:
                        results.append({
                            "player": player_name,
                            "country": country or "—",
                            "goals": current_goals,
                        })
        return results

    def find_teams_in_text(haystack, all_teams):
        hits = []
        # Pass 1: full team name substring (most specific wins; longest first to
        # avoid "south korea" matching when only "south africa" is present).
        for t in sorted(all_teams, key=lambda x: -len(x)):
            tn = t.lower()
            if len(tn) >= 4 and tn in haystack and t not in hits:
                hits.append(t)
        # Pass 2: Russian hints (only adds new teams not already found).
        for ru, en in RU_TEAM_HINTS.items():
            if ru in haystack:
                for t in all_teams:
                    if en in t.lower() and t not in hits:
                        hits.append(t)
                        break
        # Pass 3: distinctive first-word fallback. Skip ambiguous first words
        # that appear in multiple team names ("south", "new", "saudi").
        first_word_count = {}
        for t in all_teams:
            fw = t.lower().split()[0] if t.lower().split() else ""
            first_word_count[fw] = first_word_count.get(fw, 0) + 1
        for t in all_teams:
            if t in hits:
                continue
            first = t.lower().split()[0] if t.lower().split() else ""
            if (len(first) >= 5
                and first_word_count.get(first, 0) == 1
                and re.search(rf"\b{re.escape(first)}\b", haystack)):
                hits.append(t)
        return hits

    try:
        async with aiohttp.ClientSession(headers={"User-Agent": UA}, timeout=TIMEOUT) as session:
            html, status = await fetch(session, URL)
            if not html or status != 200:
                return "⚠️ Couldn't reach Wikipedia World Cup page right now."

            soup = BeautifulSoup(html, "html.parser")

            groups = []
            for hd in soup.find_all(["h3", "h4"]):
                title = clean(hd.get_text(" "))
                m = re.match(r"^Group ([A-L])$", title)
                if not m:
                    continue
                letter = m.group(1)
                tbl = hd.find_next("table")
                if not tbl:
                    continue
                rows = parse_standings_table(tbl)
                if rows:
                    groups.append((letter, rows))

            if not groups:
                return "⚠️ Couldn't parse any group standings from Wikipedia."

            # Voice mode: very short, match-focused.
            if is_voice:
                team_to_group = {}
                team_to_row = {}
                for L, rs in groups:
                    for r in rs:
                        nm = r["team"].replace("(H)", "").strip()
                        team_to_group[nm] = L
                        team_to_row[nm] = r

                haystack = " ".join([user_message, raw_extra, " ".join(tokens)]).lower()
                found_teams = find_teams_in_text(haystack, list(team_to_group.keys()))
                matches = parse_matches(soup)

                if len(found_teams) >= 2:
                    a, b = found_teams[0], found_teams[1]
                    for m in matches:
                        pair = {m["home"].lower(), m["away"].lower()}
                        if a.lower() in pair and b.lower() in pair:
                            if m["played"]:
                                sc = m["score"].replace("–", "-")
                                return f"{m['home']} {sc} {m['away']}."
                            if is_ru:
                                return f"Матч {a} — {b} ещё не сыгран."
                            return f"{a} vs {b} not played yet."
                    if is_ru:
                        return f"Матч между {a} и {b} не найден."
                    return f"No match between {a} and {b} found."

                if len(found_teams) == 1:
                    nm = found_teams[0]
                    r = team_to_row[nm]
                    L = team_to_group[nm]
                    if is_ru:
                        return (f"{nm}: группа {L}, {r['pos']} место, "
                                f"{r['pts']} очков ({r['w']} побед, {r['d']} ничьих, {r['l']} поражений).")
                    return (f"{nm}: group {L}, place {r['pos']}, "
                            f"{r['pts']} points ({r['w']}W {r['d']}D {r['l']}L).")

                total_played = sum(1 for m in matches if m["played"])
                if is_ru:
                    return f"Чемпионат мира 2026: групповой этап, сыграно {total_played} матчей."
                return f"World Cup 2026: group stage, {total_played} matches played."

            # Text mode
            from html import escape as _esc

            out = []
            out.append("🏆 <b>FIFA World Cup 2026 — Group Stage</b>")

            def fmt_group(letter, rows):
                # Ultra-compact table to fit narrow Telegram bubbles (group-chat
                # replies). Combines W/D/L into one column and GF/GA/GD into a
                # single "GF:GA GD" column. Single-line <code> per row (no copy
                # button, monospace).
                TEAM_ABBREV = {
                    "Czech Republic": "Czech",
                    "United States": "USA",
                    "Saudi Arabia": "Saudi",
                    "Bosnia and Herzegovina": "Bosnia",
                    "Trinidad and Tobago": "Trinidad",
                    "South Korea": "S.Korea",
                    "South Africa": "S.Africa",
                    "New Zealand": "N.Zeal.",
                    "Netherlands": "Nethrl.",
                    "Switzerland": "Swiss",
                    "Cape Verde": "C.Verde",
                    "Ivory Coast": "I.Coast",
                    "Uzbekistan": "Uzbek.",
                    "DR Congo": "DRCongo",
                }
                TEAM_W = 8
                # Columns: #(1) Team(8) WDL(5) GF:GA(5) GD(2) Pts(2)
                # Total width = 1+1+8+1+5+1+5+1+2+1+2 = 28 chars
                headers = [
                    "#",
                    "Team".ljust(TEAM_W),
                    " WDL ",
                    "GF:GA",
                    "GD",
                    "Pt",
                ]
                data = []
                for r in rows:
                    raw_team = r["team"].replace("(H)", "").strip()
                    team = TEAM_ABBREV.get(raw_team, raw_team)[:TEAM_W].ljust(TEAM_W)
                    wdl = f"{r['w']}-{r['d']}-{r['l']}"
                    gfga = f"{str(r['gf']).rjust(2)}:{str(r['ga']).ljust(2)}"
                    data.append([
                        str(r["pos"]),
                        team,
                        wdl,
                        gfga,
                        str(r["gd"]).rjust(2),
                        str(r["pts"]).rjust(2),
                    ])

                def _fmt(row):
                    return " ".join(row)

                header_line = _fmt(headers)
                row_lines = [_fmt(row) for row in data]
                lines = [f"<code>{_esc(header_line)}</code>"]
                lines += [f"<code>{_esc(r)}</code>" for r in row_lines]
                return f"\n<b>Group {letter}</b>\n" + "\n".join(lines)

            def fmt_team_line(L, r):
                stats = (
                    f"{r['pld']}P  {r['w']}-{r['d']}-{r['l']}  "
                    f"{r['gf']}:{r['ga']} ({r['gd']})  {r['pts']}pts"
                )
                return (
                    f"<b>{_esc(r['team'])}</b> — Group {L}, #{r['pos']}\n"
                    f"<code>{_esc(stats)}</code>"
                )

            # Detect a focused question ("how many goals/wins/points did X have?")
            # and produce a one-line direct answer in the user's language.
            def _direct_stat_answer(r):
                blob = f"{user_message} {raw_extra}".lower()
                ru_blob = bool(re.search(r"[а-яё]", blob))
                team = r["team"].replace("(H)", "").strip()
                # Goals scored / conceded
                if re.search(r"\b(goals? scored|goals? for|gf)\b", blob) or \
                   re.search(r"забил|голов забил|сколько голов|голы забил", blob):
                    if ru_blob:
                        return f"⚽ <b>{_esc(team)}</b> забила <b>{_esc(r['gf'])}</b> голов на ЧМ-2026."
                    return f"⚽ <b>{_esc(team)}</b> has scored <b>{_esc(r['gf'])}</b> goals at the 2026 World Cup."
                if re.search(r"\b(goals? conceded|goals? against|ga)\b", blob) or \
                   re.search(r"пропустил|сколько пропущен", blob):
                    if ru_blob:
                        return f"🥅 <b>{_esc(team)}</b> пропустила <b>{_esc(r['ga'])}</b> голов."
                    return f"🥅 <b>{_esc(team)}</b> has conceded <b>{_esc(r['ga'])}</b> goals."
                # Wins
                if re.search(r"\b(wins?|won|how many wins)\b", blob) or \
                   re.search(r"побед|выигр|сколько побед", blob):
                    if ru_blob:
                        return f"🏅 <b>{_esc(team)}</b>: <b>{_esc(r['w'])}</b> побед на ЧМ-2026."
                    return f"🏅 <b>{_esc(team)}</b>: <b>{_esc(r['w'])}</b> wins at the 2026 World Cup."
                # Losses
                if re.search(r"\b(loss(es)?|lost|defeats?)\b", blob) or \
                   re.search(r"пораже|проигр", blob):
                    if ru_blob:
                        return f"❌ <b>{_esc(team)}</b>: <b>{_esc(r['l'])}</b> поражений."
                    return f"❌ <b>{_esc(team)}</b>: <b>{_esc(r['l'])}</b> losses."
                # Draws
                if re.search(r"\b(draws?|drew|ties?|tied)\b", blob) or \
                   re.search(r"ничь|ничей", blob):
                    if ru_blob:
                        return f"⚖️ <b>{_esc(team)}</b>: <b>{_esc(r['d'])}</b> ничьих."
                    return f"⚖️ <b>{_esc(team)}</b>: <b>{_esc(r['d'])}</b> draws."
                # Points
                if re.search(r"\b(points?|pts)\b", blob) or \
                   re.search(r"очк", blob):
                    if ru_blob:
                        return f"🏆 <b>{_esc(team)}</b>: <b>{_esc(r['pts'])}</b> очков."
                    return f"🏆 <b>{_esc(team)}</b>: <b>{_esc(r['pts'])}</b> points."
                return None

            # ---- Date-based match listing (today / tomorrow / yesterday) ----
            from datetime import date, timedelta

            def _eest_label(dt_utc):
                """Format a UTC datetime as 'HH:MM EEST' (or EET in winter)."""
                if not dt_utc:
                    return ""
                local = dt_utc.astimezone(TALLINN_TZ)
                tz_name = local.tzname() or "EEST"
                return f"{local.strftime('%H:%M')} {tz_name}"

            def fmt_matches_for_date(matches, target_iso, label, ru=False):
                # Filter by the EEST-local calendar date of kickoff when known;
                # fall back to the venue iso_date so old/historical entries
                # without kickoff_utc still match.
                def _matches_target(m):
                    ko = m.get("kickoff_utc")
                    if ko:
                        return ko.astimezone(TALLINN_TZ).date().isoformat() == target_iso
                    return m.get("iso_date") == target_iso
                day = [m for m in matches if _matches_target(m)]
                if not day:
                    if ru:
                        return f"📅 <b>{_esc(label)}</b>: матчей нет."
                    return f"📅 <b>{_esc(label)}</b>: no matches."
                lines = [f"📅 <b>{_esc(label)}</b>"]
                # Sort upcoming matches by kickoff time so they read chronologically
                day_sorted = sorted(
                    day,
                    key=lambda m: m.get("kickoff_utc") or datetime.max.replace(tzinfo=timezone.utc),
                )
                for m in day_sorted:
                    sc = m["score"].replace("–", "-")
                    home = m["home"].replace("(H)", "").strip()
                    away = m["away"].replace("(H)", "").strip()
                    time_str = _eest_label(m.get("kickoff_utc"))
                    if m["played"]:
                        if time_str:
                            lines.append(f"• {_esc(time_str)} — {_esc(home)} <b>{_esc(sc)}</b> {_esc(away)}")
                        else:
                            lines.append(f"• {_esc(home)} <b>{_esc(sc)}</b> {_esc(away)}")
                    elif time_str:
                        lines.append(f"• {_esc(time_str)} — {_esc(home)} vs {_esc(away)}")
                    else:
                        lines.append(f"• {_esc(home)} vs {_esc(away)}")
                return "\n".join(lines)

            if wants_today or wants_tomorrow or wants_yesterday:
                matches_all = parse_matches(soup)
                today = date.today()
                msg_is_ru = is_ru or bool(re.search(r"[а-яё]", f"{user_message} {raw_extra}", re.IGNORECASE))
                pieces = []
                if wants_yesterday:
                    iso = (today - timedelta(days=1)).isoformat()
                    label = f"Вчера ({iso})" if msg_is_ru else f"Yesterday ({iso})"
                    pieces.append(fmt_matches_for_date(matches_all, iso, label, ru=msg_is_ru))
                if wants_today:
                    iso = today.isoformat()
                    label = f"Сегодня ({iso})" if msg_is_ru else f"Today ({iso})"
                    pieces.append(fmt_matches_for_date(matches_all, iso, label, ru=msg_is_ru))
                if wants_tomorrow:
                    iso = (today + timedelta(days=1)).isoformat()
                    label = f"Завтра ({iso})" if msg_is_ru else f"Tomorrow ({iso})"
                    pieces.append(fmt_matches_for_date(matches_all, iso, label, ru=msg_is_ru))
                out.append("")
                out.append("\n\n".join(pieces))
                return "\n".join(out)

            # Explicit group letter (e.g. "fb group a" or "fb a") — show that group.
            if requested_group_letter:
                match = [(L, rs) for L, rs in groups if L == requested_group_letter]
                if not match:
                    return f"⚠️ Group {requested_group_letter} not found."
                out.append(fmt_group(*match[0]))
                return "\n".join(out)

            # ---- Scorers helpers ----
            def _country_matches(country, query):
                """Loose match: query substring vs Wikipedia country name."""
                if not country or not query:
                    return False
                c = country.lower()
                q = query.lower()
                return q in c or c.startswith(q.split()[0]) if q else False

            def fmt_scorer_table(rows, title):
                """rows: list of {player, country, goals}. Builds compact code block."""
                if not rows:
                    return ""
                NAME_W = 18
                COUNTRY_W = 8
                COUNTRY_ABBREV = {
                    "Czech Republic": "Czech",
                    "United States": "USA",
                    "Saudi Arabia": "Saudi",
                    "Bosnia and Herzegovina": "Bosnia",
                    "Trinidad and Tobago": "Trin.",
                    "South Korea": "S.Korea",
                    "South Africa": "S.Afr.",
                    "New Zealand": "N.Zeal.",
                    "Netherlands": "Nethr.",
                    "Switzerland": "Swiss",
                    "Cape Verde": "C.Verde",
                    "Ivory Coast": "I.Coast",
                    "Uzbekistan": "Uzbek.",
                    "DR Congo": "DRCongo",
                }
                header = f"{'Player'.ljust(NAME_W)} {'Team'.ljust(COUNTRY_W)} G"
                lines = [f"<b>{_esc(title)}</b>", f"<code>{_esc(header)}</code>"]
                for r in rows:
                    nm = r["player"][:NAME_W].ljust(NAME_W)
                    co = COUNTRY_ABBREV.get(r["country"], r["country"])[:COUNTRY_W].ljust(COUNTRY_W)
                    g = str(r["goals"]).rjust(1)
                    lines.append(f"<code>{_esc(f'{nm} {co} {g}')}</code>")
                return "\n".join(lines)

            scorers = parse_scorers(soup)
            # Sort by goals desc, then player name asc
            scorers.sort(key=lambda x: (-x["goals"], x["player"]))

            # Top-N scorers query (no team or team alongside scorers keyword)
            if wants_scorers and not team_query:
                top = scorers[:15]
                if not top:
                    return "⚠️ No goalscorer data available yet."
                out.append("")
                out.append(fmt_scorer_table(top, "⚽ Top scorers — 2026 World Cup"))
                return "\n".join(out)

            # Team query with scorers intent → show that team's scorers list.
            if wants_scorers and team_query:
                q = team_query.lower()
                team_scorers = [s for s in scorers if _country_matches(s["country"], q)]
                if not team_scorers:
                    return f"⚠️ No goals yet for <b>{_esc(team_query)}</b>."
                country_disp = team_scorers[0]["country"]
                out.append("")
                out.append(fmt_scorer_table(team_scorers, f"⚽ {country_disp} — goalscorers"))
                return "\n".join(out)

            # Team query with explicit "group" word → show full group standings.
            if team_query and wants_group_view:
                hits = []
                q = team_query.lower()
                for L, rs in groups:
                    for r in rs:
                        if q in r["team"].lower():
                            hits.append((L, r, rs))
                if not hits:
                    return f"⚠️ No team matched <b>{_esc(team_query)}</b>."
                shown_groups = set()
                for L, r, rs in hits:
                    if L in shown_groups:
                        continue
                    shown_groups.add(L)
                    out.append(fmt_group(L, rs))
                return "\n".join(out)

            # Team query alone (e.g. "fb brazil") → short team status + top scorer.
            if team_query:
                hits = []
                q = team_query.lower()
                for L, rs in groups:
                    for r in rs:
                        if q in r["team"].lower():
                            hits.append((L, r))
                if not hits:
                    return f"⚠️ No team matched <b>{_esc(team_query)}</b>."
                for L, r in hits:
                    direct = _direct_stat_answer(r)
                    if direct:
                        out.append("")
                        out.append(direct)
                    out.append("")
                    out.append(fmt_team_line(L, r))
                    # Append this country's top scorers (max 5)
                    team_name = r["team"].replace("(H)", "").strip()
                    team_scorers = [s for s in scorers
                                    if _country_matches(s["country"], team_name)][:5]
                    if team_scorers:
                        out.append("")
                        out.append(fmt_scorer_table(
                            team_scorers, f"⚽ {team_name} top scorers"))
                return "\n".join(out)

            # "fb group" with no team/letter → show all groups.
            if wants_group_view:
                for L, rs in groups:
                    out.append(fmt_group(L, rs))
                return "\n".join(out)

            # Default "fb" → brief tournament summary, no full standings dump.
            matches = parse_matches(soup)
            played = [m for m in matches if m["played"]]
            recent = played[-5:]
            group_letters = ", ".join(L for L, _ in groups)
            out.append(f"\n<i>{len(played)} matches played, {len(groups)} groups ({group_letters}).</i>")
            if recent:
                out.append("\n<b>Recent results:</b>")
                for m in recent:
                    sc = m["score"].replace("–", "-")
                    out.append(f"• {_esc(m['home'])} {_esc(sc)} {_esc(m['away'])}")
            if scorers:
                out.append("")
                out.append(fmt_scorer_table(scorers[:5], "⚽ Top scorers"))
            out.append("\n<i>Use</i> <code>fb &lt;team&gt;</code> <i>for team status,</i> "
                       "<code>fb group &lt;letter&gt;</code> <i>for a group (A–L),</i> "
                       "<code>fb scorers</code> <i>for top goalscorers,</i> "
                       "<code>fb scorers &lt;team&gt;</code> <i>for a country's scorers,</i> "
                       "<code>fb today</code> / <code>fb tomorrow</code> <i>for the day's matches.</i>")
            return "\n".join(out)
    except Exception as exc:
        return f"⚠️ Something went wrong while checking the World Cup: {exc}"
