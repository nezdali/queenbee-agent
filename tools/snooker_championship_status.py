async def run(context: dict) -> str:
    import re
    from datetime import datetime
    import aiohttp
    from bs4 import BeautifulSoup
    from tool_utils import parse_args

    BASE = "https://snooker.org"
    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    TIMEOUT = aiohttp.ClientTimeout(total=20)

    user_args = parse_args(context) or []
    GENERIC_WORDS = {
        "stats", "status", "live", "score", "scores", "result", "results",
        "match", "matches", "today", "now", "current", "ongoing", "update",
        "updates", "latest", "info", "championship", "tournament",
    }
    query_tokens = [a.lower() for a in user_args if a and a.strip() and a.lower() not in GENERIC_WORDS]
    query = " ".join(query_tokens).strip()

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

    def clean(text):
        return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()

    def md_escape(text):
        # Escape characters that confuse legacy Markdown parse mode.
        return (text or "").replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")

    def fmt_time(iso_str):
        # "2026-04-25 09:00:00Z" -> "25 Apr 09:00"
        try:
            dt = datetime.strptime(iso_str, "%Y-%m-%d %H:%M:%SZ")
            return dt.strftime("%d %b %H:%M")
        except Exception:
            return iso_str

    ROUND_LABELS = {
        "rd 1": "Round 1", "rd 2": "Round 2", "rd 3": "Round 3", "rd 4": "Round 4",
        "last 128": "Last 128", "last 64": "Last 64", "last 32": "Last 32",
        "last 16": "Last 16", "quarterfinal": "Quarter-final", "quarter-final": "Quarter-final",
        "semifinal": "Semi-final", "semi-final": "Semi-final", "final": "Final",
    }

    def pretty_round(raw):
        r = clean(raw).lower()
        # Normalise "Rd 2 ( 25 )" -> round: "Rd 2", bo: 25
        m = re.match(r"(.+?)\s*\(\s*(\d+)\s*\)", r)
        bo = None
        label = r
        if m:
            label = m.group(1).strip()
            bo = m.group(2)
        label = ROUND_LABELS.get(label, label.title())
        return label, bo

    try:
        async with aiohttp.ClientSession(headers={"User-Tool": UA}, timeout=TIMEOUT) as session:
            home_html, status = await fetch(session, f"{BASE}/")
            if not home_html or status != 200:
                return "⚠️ Couldn't reach snooker.org right now. Please try again later."

            soup = BeautifulSoup(home_html, "html.parser")
            candidates = []
            for a in soup.find_all("a", href=True):
                m = re.match(r"^/event/(\d+)$", a["href"])
                if not m:
                    continue
                name = clean(a.get_text(" "))
                if not name:
                    continue
                pair = (m.group(1), name)
                if pair not in candidates:
                    candidates.append(pair)

            if not candidates:
                return "⚠️ Couldn't find any current snooker events on snooker.org."

            chosen = None
            if query:
                for eid, name in candidates:
                    if query in name.lower():
                        chosen = (eid, name)
                        break
            if not chosen:
                for eid, name in candidates:
                    if "world championship" in name.lower():
                        chosen = (eid, name)
                        break
                if not chosen:
                    chosen = candidates[0]

            event_id, event_name = chosen

            live_url = f"{BASE}/res/index.asp?template=21&event={event_id}"
            live_html, lstatus = await fetch(session, live_url)
            if not live_html or lstatus != 200:
                return f"Found event *{md_escape(event_name)}* but couldn't load its live scores page."

            live_soup = BeautifulSoup(live_html, "html.parser")

            # Also fetch upcoming + recent-results pages for fuller context.
            upcoming_html, _ = await fetch(session, f"{BASE}/res/index.asp?template=24&event={event_id}")
            results_html, _ = await fetch(session, f"{BASE}/res/index.asp?template=22&event={event_id}")
            upcoming_soup = BeautifulSoup(upcoming_html, "html.parser") if upcoming_html else None
            results_soup = BeautifulSoup(results_html, "html.parser") if results_html else None

            # Parse matches structurally.
            matches = []
            def parse_table(soup_obj, section_hint):
                if not soup_obj:
                    return
                tbl2 = soup_obj.find("table", class_="matches")
                if not tbl2:
                    return
                rows2 = tbl2.find_all("tr", recursive=False) or tbl2.find_all("tr")
                pending = None
                for tr in rows2:
                    cls = tr.get("class") or []
                    tds = tr.find_all(["td", "th"])
                    # Live/upcoming layout: 10 cells [round, "", p1, s1, "-", s2, "", p2, "", dates]
                    # Results layout:       11 cells [date, round, "", p1, s1, "-", s2, "", p2, "", ""]
                    is_match_row = "oneonone" in cls and len(tds) >= 10
                    if is_match_row:
                        cells = [clean(td.get_text(" ")) for td in tds]
                        if len(tds) >= 11 and re.match(r"\d{4}-\d{2}-\d{2}", cells[0]):
                            # Results format
                            date_matches = [cells[0]]
                            round_lbl, bo = pretty_round(cells[1])
                            p1 = cells[3]
                            s1 = cells[4]
                            s2 = cells[6]
                            p2 = cells[8]
                        else:
                            round_lbl, bo = pretty_round(cells[0])
                            p1 = cells[2]
                            s1 = cells[3]
                            s2 = cells[5]
                            p2 = cells[7]
                            dates_raw = cells[9] if len(cells) > 9 else ""
                            date_matches = re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}Z", dates_raw)
                        is_scheduled = s1 in ("v", "", "-")
                        is_finished = "finished" in cls or (section_hint == "finished" and not is_scheduled)
                        is_ongoing = "unfinished" in cls and not is_finished and not is_scheduled
                        pending = {
                            "round": round_lbl,
                            "bo": bo,
                            "p1": p1,
                            "p2": p2,
                            "s1": s1,
                            "s2": s2,
                            "dates": date_matches,
                            "finished": is_finished,
                            "ongoing": is_ongoing,
                            "scheduled": is_scheduled,
                            "status": "",
                            "key": (round_lbl, p1, p2),
                        }
                        matches.append(pending)
                    elif "info" in cls and pending is not None and len(tds) == 1:
                        pending["status"] = clean(tds[0].get_text(" "))
                        pending = None

            # Event header from live page
            header_text = event_name
            tbl = live_soup.find("table", class_="matches")
            if tbl:
                hdr = tbl.find("tr", class_="event")
                if hdr:
                    header_text = clean(hdr.get_text(" "))

            parse_table(live_soup, "live")
            # Recent results
            parse_table(results_soup, "finished")
            # Upcoming
            parse_table(upcoming_soup, "scheduled")

            # De-duplicate by (round, p1, p2), keeping the most informative entry.
            uniq = {}
            for m in matches:
                key = m["key"]
                existing = uniq.get(key)
                if existing is None:
                    uniq[key] = m
                else:
                    # Prefer ongoing > finished > scheduled
                    rank = lambda x: (2 if x["ongoing"] else 1 if x["finished"] else 0)
                    if rank(m) > rank(existing):
                        uniq[key] = m
            matches = list(uniq.values())

            # Build pretty output (legacy Markdown).
            out = []
            # Title line: extract name + date range if present
            title_line = f"🎱 *{md_escape(header_text)}*"
            out.append(title_line)
            out.append(f"🔗 [Live scores on snooker.org]({live_url})")

            if not matches:
                out.append("")
                out.append("No live match data parsed — see the link above.")
                return "\n".join(out)

            ongoing = [m for m in matches if m["ongoing"]]
            finished = [m for m in matches if m["finished"]]
            scheduled = [m for m in matches if m["scheduled"]]

            def fmt_match(m, section):
                round_tag = m["round"] + (f" · bo{m['bo']}" if m["bo"] else "")
                p1 = md_escape(m["p1"])
                p2 = md_escape(m["p2"])
                if section == "scheduled":
                    when = fmt_time(m["dates"][0]) + " UTC" if m["dates"] else "TBD"
                    return f"• {round_tag} — {p1} vs {p2}\n   🕒 {when}"
                # score line
                score = f"*{m['s1']}–{m['s2']}*"
                if section == "ongoing":
                    icon = "🟢"
                elif section == "finished":
                    icon = "🏁"
                else:
                    icon = "•"
                header = f"{icon} {round_tag}"
                if section == "finished" and m["dates"]:
                    header += f"  ·  {fmt_time(m['dates'][0])}"
                lines = [header, f"   {p1}  {score}  {p2}"]
                status = (m["status"] or "").strip()
                if status:
                    if m["dates"] and "resume" in status.lower() and len(m["dates"]) >= 2:
                        lines.append(f"   ⏸ Resumes {fmt_time(m['dates'][-1])} UTC")
                    elif m["dates"] and section == "ongoing":
                        lines.append(f"   ⏸ {md_escape(status)}")
                    else:
                        lines.append(f"   {md_escape(status)}")
                return "\n".join(lines)

            if ongoing:
                out.append("")
                out.append("🟢 *Ongoing*")
                for m in ongoing[:5]:
                    out.append(fmt_match(m, "ongoing"))

            if finished:
                out.append("")
                out.append("🏁 *Recent results*")
                for m in finished[:5]:
                    out.append(fmt_match(m, "finished"))

            if scheduled:
                out.append("")
                out.append("📅 *Upcoming*")
                for m in scheduled[:5]:
                    out.append(fmt_match(m, "scheduled"))

            # ------------------------------------------------------------------
            # Top 5 players (world rankings)
            # ------------------------------------------------------------------
            try:
                rank_url = f"{BASE}/res/index.asp?template=31&season=2025"
                rank_html, rstatus = await fetch(session, rank_url)
                if rank_html and rstatus == 200:
                    rsoup = BeautifulSoup(rank_html, "html.parser")
                    rtbl = rsoup.find("table", class_="display")
                    top = []
                    if rtbl:
                        header_cells = [clean(th.get_text(" ")).lower() for th in rtbl.find_all("tr")[0].find_all(["th", "td"])]
                        try:
                            name_idx = header_cells.index("name")
                        except ValueError:
                            name_idx = 9
                        try:
                            country_idx = header_cells.index("nationality")
                        except ValueError:
                            country_idx = name_idx + 1
                        try:
                            total_idx = header_cells.index("total")
                        except ValueError:
                            total_idx = country_idx + 2
                        # Change column: last "±"
                        change_idx = None
                        for i, h in enumerate(header_cells):
                            if h == "±":
                                change_idx = i
                        for tr in rtbl.find_all("tr")[1:]:
                            tds = tr.find_all(["td", "th"])
                            if len(tds) <= max(name_idx, total_idx):
                                continue
                            rank_cell = clean(tds[0].get_text(" ")).rstrip(".")
                            if not rank_cell.isdigit():
                                continue
                            name = clean(tds[name_idx].get_text(" "))
                            country = clean(tds[country_idx].get_text(" ")) if country_idx < len(tds) else ""
                            points = clean(tds[total_idx].get_text(" ")) if total_idx < len(tds) else ""
                            change = clean(tds[change_idx].get_text(" ")) if change_idx is not None and change_idx < len(tds) else ""
                            top.append((rank_cell, name, country, points, change))
                            if len(top) >= 5:
                                break
                    if top:
                        out.append("")
                        out.append("🏆 *Top 5 world rankings*")
                        medals = {"1": "🥇", "2": "🥈", "3": "🥉"}
                        for rank_cell, name, country, points, change in top:
                            medal = medals.get(rank_cell, f"{rank_cell}⃣")
                            line = f"{medal} *{md_escape(name)}*"
                            if country:
                                line += f"  ·  {md_escape(country)}"
                            if points:
                                line += f"  ·  {md_escape(points)} pts"
                            if change and change not in ("", "0"):
                                line += f"  ({md_escape(change)})"
                            out.append(line)
            except Exception as exc:
                # Rankings are optional; log but don't fail the whole call.
                out.append("")
                out.append(f"(Could not load rankings: {exc})")

            return "\n".join(out)
    except Exception as exc:
        return f"⚠️ Something went wrong while checking the snooker championship: {exc}"
