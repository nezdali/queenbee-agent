async def run(context: dict) -> str:
    """Search movies via TMDb. Optional YouTube full-movie finder with video analysis."""
    import aiohttp
    import asyncio
    import subprocess
    import os
    import re
    import json as _json
    import tempfile
    import shutil
    import sqlite3
    from html import escape as esc

    try:
        args = context.get("args", []) or []
        raw_query = " ".join(args).strip()
        query_lower = raw_query.lower()

        TMDB_KEY = os.getenv("TMDB_API_KEY", "")
        OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

        UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        TMDB_BASE = "https://api.themoviedb.org/3"
        TMDB_IMG = "https://image.tmdb.org/t/p/w500"

        GENRE_MAP = {
            28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
            80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
            14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
            9648: "Mystery", 10749: "Romance", 878: "Sci-Fi", 10770: "TV Movie",
            53: "Thriller", 10752: "War", 37: "Western",
        }

        import pathlib
        DB_PATH = str(pathlib.Path(__file__).parent / "movie_cache.db")

        def _init_db():
            con = sqlite3.connect(DB_PATH)
            try:
                con.execute("""
                    CREATE TABLE IF NOT EXISTS movies (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        title       TEXT NOT NULL,
                        year        TEXT,
                        genres      TEXT,
                        tag         TEXT NOT NULL,
                        youtube_id  TEXT,
                        youtube_url TEXT,
                        tmdb_id     INTEGER,
                        minutes     INTEGER,
                        motion      REAL,
                        speech      REAL,
                        ai_conf     REAL,
                        lang        TEXT DEFAULT '',
                        added       TEXT DEFAULT (datetime('now'))
                    )
                """)
                # Migrate existing DBs: add lang column if missing
                try:
                    con.execute("ALTER TABLE movies ADD COLUMN lang TEXT DEFAULT ''")
                except Exception:
                    pass  # column already exists
                con.execute("CREATE INDEX IF NOT EXISTS idx_movies_tag ON movies(tag)")
                con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_movies_ytid ON movies(youtube_id)")
                con.commit()
            finally:
                con.close()

        def _normalize_tag(query):
            words = sorted(set(query.lower().split()))
            return " ".join(words)

        def _get_cached_movies(tag, limit):
            con = sqlite3.connect(DB_PATH)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(
                    "SELECT * FROM movies WHERE tag = ? ORDER BY added DESC LIMIT ?",
                    (tag, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                con.close()

        def _is_cached(youtube_id=None, title=None):
            con = sqlite3.connect(DB_PATH)
            try:
                if youtube_id:
                    row = con.execute("SELECT 1 FROM movies WHERE youtube_id = ?", (youtube_id,)).fetchone()
                    if row:
                        return True
                if title:
                    row = con.execute("SELECT 1 FROM movies WHERE LOWER(title) = LOWER(?)", (title,)).fetchone()
                    if row:
                        return True
                return False
            finally:
                con.close()

        def _save_movie(title, year, genres, tag, youtube_id, youtube_url, tmdb_id, minutes, motion, speech, ai_conf, lang=""):
            con = sqlite3.connect(DB_PATH)
            try:
                con.execute(
                    """INSERT OR IGNORE INTO movies
                       (title, year, genres, tag, youtube_id, youtube_url,
                        tmdb_id, minutes, motion, speech, ai_conf, lang)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (title, year, genres, tag, youtube_id, youtube_url,
                     tmdb_id, minutes, motion, speech, ai_conf, lang),
                )
                con.commit()
            finally:
                con.close()

        LANG_FLAGS = {
            "en": "🇺🇸 English", "fr": "🇫🇷 French", "de": "🇩🇪 German",
            "es": "🇪🇸 Spanish", "it": "🇮🇹 Italian", "ru": "🇷🇺 Russian",
            "ja": "🇯🇵 Japanese", "ko": "🇰🇷 Korean", "hi": "🇮🇳 Indian",
            "sv": "🇸🇪 Swedish", "no": "🇳🇴 Norwegian", "da": "🇩🇰 Danish",
            "tr": "🇹🇷 Turkish", "zh": "🇨🇳 Chinese", "th": "🇹🇭 Thai",
            "pt": "🇵🇹 Portuguese", "pl": "🇵🇱 Polish", "nl": "🇳🇱 Dutch",
            "fi": "🇫🇮 Finnish", "cs": "🇨🇿 Czech", "ar": "🇸🇦 Arabic",
            "fa": "🇮🇷 Persian", "el": "🇬🇷 Greek", "hu": "🇭🇺 Hungarian",
            "ro": "🇷🇴 Romanian", "ka": "🇬🇪 Georgian", "et": "🇪🇪 Estonian",
            "lv": "🇱🇻 Latvian", "sr": "🇷🇸 Serbian", "hr": "🇭🇷 Croatian",
            "bg": "🇧🇬 Bulgarian",
        }

        def _fmt_movie_line(m):
            title = esc(m.get("title") or "?")
            year = m.get("year") or "?"
            url = m.get("youtube_url") or ""
            minutes = m.get("minutes") or 0
            if url:
                return f'• <a href="{url}"><b>{title}</b></a> ({year}) — {minutes} min'
            return f"• <b>{title}</b> ({year}) — {minutes} min"

        def _group_movies(movies):
            """Group movies by country (lang), then by first genre."""
            from collections import OrderedDict
            by_country = OrderedDict()
            for m in movies:
                lang = m.get("lang") or ""
                country_label = LANG_FLAGS.get(lang, f"🌍 {lang.upper()}" if lang else "🌍 Unknown")
                if country_label not in by_country:
                    by_country[country_label] = OrderedDict()
                genres_str = m.get("genres") or ""
                first_genre = genres_str.split(",")[0].strip() if genres_str else "Other"
                if first_genre not in by_country[country_label]:
                    by_country[country_label][first_genre] = []
                by_country[country_label][first_genre].append(m)
            return by_country

        def _format_cached(movies, tag):
            lines = [f"<b>🎬 Cached results for: {esc(tag)}</b> ({len(movies)} movies)\n"]
            grouped = _group_movies(movies)
            for country, genres in grouped.items():
                country_movies = sum(len(v) for v in genres.values())
                lines.append(f"<b>{country}</b> ({country_movies})")
                inner = []
                for genre, gm in genres.items():
                    inner.append(f"<b>🎭 {esc(genre)}</b>")
                    for m in gm:
                        inner.append(_fmt_movie_line(m))
                lines.append("<blockquote expandable>" + "\n".join(inner) + "</blockquote>")
                lines.append("")
            lines.append(f"<i>Use <code>movie new {esc(tag)}</code> to find fresh results.</i>")
            return "\n".join(lines)

        def _list_cache(limit=20):
            _init_db()
            con = sqlite3.connect(DB_PATH)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(
                    "SELECT * FROM movies ORDER BY added DESC, id DESC LIMIT ?",
                    (max(1, min(int(limit), 100)),),
                ).fetchall()
            finally:
                con.close()

            if not rows:
                return "<b>🎬 Movie cache</b>\n\n<i>Cache is empty.</i>"

            movies = [dict(r) for r in rows]
            grouped = _group_movies(movies)

            lines = [f"<b>🎬 Movie cache</b> ({len(movies)} movies)", ""]
            for country, genres in grouped.items():
                country_movies = sum(len(v) for v in genres.values())
                lines.append(f"<b>{country}</b> ({country_movies})")
                inner = []
                for genre, gm in genres.items():
                    inner.append(f"<b>🎭 {esc(genre)}</b>")
                    for m in gm:
                        inner.append(_fmt_movie_line(m))
                lines.append("<blockquote expandable>" + "\n".join(inner) + "</blockquote>")
                lines.append("")
            return "\n".join(lines)

        async def _tmdb_discover(query, tmdb_key, tmdb_base, tmdb_img, genre_map, ua, session):
            import re
            rev_genre = {v.lower(): k for k, v in genre_map.items()}
            # Russian genre aliases → English genre name (lowercase)
            ru_genre = {
                "комедия": "comedy", "комедию": "comedy", "комедий": "comedy",
                "драма": "drama", "драму": "drama",
                "боевик": "action", "боевики": "action", "экшен": "action", "экшн": "action",
                "ужасы": "horror", "ужас": "horror", "хоррор": "horror",
                "триллер": "thriller", "триллеры": "thriller",
                "фантастика": "sci-fi", "фантастику": "sci-fi",
                "фэнтези": "fantasy", "фентези": "fantasy",
                "мелодрама": "romance", "мелодраму": "romance", "романтика": "romance",
                "мультфильм": "animation", "мультик": "animation", "анимация": "animation",
                "детектив": "mystery", "детективы": "mystery",
                "документальный": "documentary", "документалка": "documentary",
                "военный": "war", "военные": "war", "война": "war",
                "криминал": "crime", "криминальный": "crime",
                "приключения": "adventure", "приключение": "adventure",
                "семейный": "family", "семейные": "family",
                "исторический": "history", "история": "history",
                "вестерн": "western",
                "музыкальный": "music", "мюзикл": "music",
            }
            words = query.lower().split()
            genre_id = None
            for w in words:
                if w in rev_genre:
                    genre_id = rev_genre[w]
                    break
                mapped = ru_genre.get(w)
                if mapped and mapped in rev_genre:
                    genre_id = rev_genre[mapped]
                    break
            params = {"api_key": tmdb_key, "language": "en-US", "sort_by": "popularity.desc", "page": 1}
            if genre_id:
                params["with_genres"] = genre_id
            lang_map = {"french": "fr", "german": "de", "spanish": "es", "italian": "it",
                        "russian": "ru", "japanese": "ja", "korean": "ko", "indian": "hi",
                        "swedish": "sv", "norwegian": "no", "danish": "da"}
            # Russian language aliases
            ru_lang = {
                "русский": "ru", "русские": "ru", "русском": "ru", "русскую": "ru", "российский": "ru", "российские": "ru",
                "французский": "fr", "французские": "fr", "французскую": "fr",
                "немецкий": "de", "немецкие": "de",
                "испанский": "es", "испанские": "es",
                "итальянский": "it", "итальянские": "it",
                "японский": "ja", "японские": "ja",
                "корейский": "ko", "корейские": "ko", "корейскую": "ko",
                "индийский": "hi", "индийские": "hi",
                "шведский": "sv", "шведские": "sv",
                "норвежский": "no", "норвежские": "no",
                "датский": "da", "датские": "da",
                "турецкий": "tr", "турецкие": "tr", "турецкую": "tr",
                "китайский": "zh", "китайские": "zh",
                "эстонский": "et", "эстонские": "et",
            }
            for w in words:
                if w in lang_map:
                    params["with_original_language"] = lang_map[w]
                    break
                if w in ru_lang:
                    params["with_original_language"] = ru_lang[w]
                    break
            year_match = re.search(r'(\d{4})', query)
            decade_match = re.search(r'(\d{2})s', query)
            if year_match:
                params["primary_release_year"] = int(year_match.group(1))
            elif decade_match:
                decade = int(decade_match.group(1))
                if decade < 100:
                    century = 1900 if decade >= 20 else 2000
                    params["primary_release_date.gte"] = f"{century + decade}-01-01"
                    params["primary_release_date.lte"] = f"{century + decade + 9}-12-31"
            async with session.get(
                f"{tmdb_base}/discover/movie", params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return f"TMDb discover failed ({resp.status})"
                data = await resp.json()
            results = data.get("results", [])[:5]
            if not results:
                return "No movies found for that query."
            lines = [f"<b>🎬 Movies: {esc(query.title())}</b>\n"]
            for m in results:
                title = m.get("title", "?")
                year = (m.get("release_date") or "")[:4]
                rating = m.get("vote_average", 0)
                genres = ", ".join(genre_map.get(g, "?") for g in m.get("genre_ids", []))
                overview = (m.get("overview") or "")[:120]
                if len(m.get("overview", "")) > 120:
                    overview += "…"
                lines.append(f"<b>{esc(title)}</b> ({year}) — ⭐ {rating:.1f}")
                lines.append(f"  🎭 {esc(genres)}")
                if overview:
                    lines.append(f"  {esc(overview)}")
                lines.append("")
            lines.append("Tip: <code>movie [title]</code> for full details")
            return "\n".join(lines)

        async def _tmdb_info_mode(query, tmdb_key, tmdb_base, tmdb_img, genre_map, ua):
            async with aiohttp.ClientSession() as session:
                params = {"api_key": tmdb_key, "query": query, "language": "en-US", "page": 1}
                async with session.get(
                    f"{tmdb_base}/search/movie", params=params, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        return f"TMDb search failed ({resp.status})"
                    data = await resp.json()
                results = data.get("results", [])
                if not results:
                    return await _tmdb_discover(query, tmdb_key, tmdb_base, tmdb_img, genre_map, ua, session)
                movie = results[0]
                movie_id = movie["id"]
                detail_url = f"{tmdb_base}/movie/{movie_id}?api_key={tmdb_key}&language=en-US"
                credits_url = f"{tmdb_base}/movie/{movie_id}/credits?api_key={tmdb_key}"
                async with session.get(detail_url, timeout=aiohttp.ClientTimeout(total=10)) as r1, session.get(credits_url, timeout=aiohttp.ClientTimeout(total=10)) as r2:
                    detail = await r1.json() if r1.status == 200 else {}
                    credits = await r2.json() if r2.status == 200 else {}
            title = detail.get("title", movie.get("title", "?"))
            year = (detail.get("release_date") or "")[:4]
            rating = detail.get("vote_average", 0)
            votes = detail.get("vote_count", 0)
            runtime = detail.get("runtime", 0)
            overview = detail.get("overview", "No description available.")
            genres = ", ".join(g["name"] for g in detail.get("genres", []))
            tagline = detail.get("tagline", "")
            budget = detail.get("budget", 0)
            revenue = detail.get("revenue", 0)
            cast = credits.get("cast", [])[:5]
            cast_str = ", ".join(f"{esc(a['name'])} <i>({esc(a.get('character', '?'))})</i>" for a in cast)
            crew = credits.get("crew", [])
            directors = [c["name"] for c in crew if c.get("job") == "Director"]
            director_str = ", ".join(directors) if directors else "?"
            stars = "⭐" * max(1, round(rating / 2))
            lines = [f"<b>🎬 {esc(title)}</b>" + (f" ({year})" if year else "")]
            if tagline:
                lines.append(f"<i>{esc(tagline)}</i>")
            lines.append("")
            lines.append(f"{stars} <b>{rating:.1f}</b>/10 ({votes:,} votes)")
            if runtime:
                lines.append(f"⏱ {runtime} min  |  🎭 {esc(genres)}")
            else:
                lines.append(f"🎭 {esc(genres)}")
            lines.append(f"🎬 Director: {esc(director_str)}")
            lines.append("")
            lines.append(f"<b>Plot:</b> {esc(overview)}")
            lines.append("")
            if cast_str:
                lines.append(f"<b>Cast:</b> {cast_str}")
            if budget:
                lines.append(f"💰 Budget: ${budget:,.0f}")
            if revenue:
                lines.append(f"📈 Revenue: ${revenue:,.0f}")
            return "\n".join(lines)

        def _find_ytdlp():
            p = shutil.which("yt-dlp")
            if p:
                return p
            home = os.path.expanduser("~")
            for candidate in [os.path.join(home, ".local", "bin", "yt-dlp"), "/usr/local/bin/yt-dlp"]:
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate
            return "yt-dlp"

        async def _ai_classify(title, description, duration, openai_key, loop):
            def _call():
                try:
                    from openai import OpenAI
                    client = OpenAI(api_key=openai_key)
                    prompt = f'''Classify this YouTube video. Is it a full movie/series or something else?
Reply ONLY with a JSON object using this exact schema:
{{"type": "MOVIE_FULL"|"SERIES"|"TRAILER"|"REVIEW"|"NOT_MOVIE"|"UNCERTAIN", "confidence": 0.0-1.0, "title_guess": "original movie title", "year_guess": year_number_or_0}}

title: "{title}"
description: "{description[:300]}"
duration: {duration} minutes'''
                    resp = client.chat.completions.create(
                        model="gpt-4.1-mini",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.3,
                    )
                    text = (resp.choices[0].message.content or "").strip()
                    if text.startswith("```"):
                        text = text.split("```")[1]
                        if text.startswith("json"):
                            text = text[4:]
                    return _json.loads(text.strip())
                except Exception:
                    return None
            return await loop.run_in_executor(None, _call)

        async def _tmdb_verify(title, year_guess, tmdb_key, tmdb_base):
            params = {"api_key": tmdb_key, "query": title}
            if year_guess:
                params["year"] = year_guess
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{tmdb_base}/search/movie", params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            results = data.get("results", [])
            if not results:
                return None
            m = results[0]
            return {
                "tmdb_id": m["id"],
                "title": m["title"],
                "year": (m.get("release_date") or "")[:4],
                "genre_ids": m.get("genre_ids", []),
                "original_language": m.get("original_language", ""),
            }

        def _motion_score_ffprobe(video_path, ffmpeg_path):
            try:
                cmd = [ffmpeg_path, "-i", video_path, "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YDIF", "-f", "null", "-"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                diffs = []
                for line in result.stderr.split("\n"):
                    if "lavfi.signalstats.YDIF" in line:
                        try:
                            val = float(line.split("=")[-1].strip())
                            diffs.append(val)
                        except Exception:
                            pass
                if not diffs:
                    return 0
                import statistics
                avg = statistics.mean(diffs)
                return min(avg / 15.0, 1.0)
            except Exception:
                return 0

        def _speech_ratio_ffprobe(video_path, ffmpeg_path):
            try:
                cmd = [ffmpeg_path, "-i", video_path, "-af", "silencedetect=noise=-30dB:d=0.5", "-f", "null", "-"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                silence_total = 0
                for line in result.stderr.split("\n"):
                    if "silence_duration" in line:
                        try:
                            val = float(line.split("silence_duration:")[-1].strip())
                            silence_total += val
                        except Exception:
                            pass
                duration = 20.0
                for line in result.stderr.split("\n"):
                    if "Duration:" in line:
                        m = re.search(r'Duration:\s*(\d+):(\d+):(\d+)\.(\d+)', line)
                        if m:
                            duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                            break
                if duration <= 0:
                    return 0
                speech = max(0, (duration - silence_total) / duration)
                return min(speech, 1.0)
            except Exception:
                return 0

        async def _analyze_middle(youtube_id, minutes, loop):
            if minutes < 60:
                return {"motion_score": 0, "speech_ratio": 0, "slide_probability": 1, "is_real_movie": False}
            middle_sec = int(minutes * 60 / 2)
            end_sec = middle_sec + 20
            def _analyze():
                seg_file = os.path.join(tempfile.gettempdir(), f"seg_{youtube_id}_mid.mp4")
                home = os.path.expanduser("~")
                extra_paths = [os.path.join(home, ".deno", "bin"), os.path.join(home, ".local", "bin")]
                env = os.environ.copy()
                env["PATH"] = os.pathsep.join(extra_paths) + os.pathsep + env.get("PATH", "")
                try:
                    ytdlp = _find_ytdlp()
                    section = f"*{middle_sec}-{end_sec}"
                    cmd = [
                        ytdlp, "--download-sections", section,
                        "-f", "worst[ext=mp4]/worst",
                        "--force-overwrites",
                        "--remote-components", "ejs:github",
                        "-o", seg_file,
                        f"https://www.youtube.com/watch?v={youtube_id}",
                    ]
                    cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "youtube_cookies.txt")
                    if os.path.isfile(cookies_path):
                        cmd.insert(-1, "--cookies")
                        cmd.insert(-1, cookies_path)
                    dl = subprocess.run(cmd, capture_output=True, text=True, timeout=90, env=env)
                    if dl.returncode != 0:
                        return None
                    if not os.path.exists(seg_file) or os.path.getsize(seg_file) < 1000:
                        return None
                    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
                    motion = _motion_score_ffprobe(seg_file, ffmpeg)
                    speech = _speech_ratio_ffprobe(seg_file, ffmpeg)
                    is_real = ((motion > 0.15 or (motion > 0.12 and speech > 0.5)) and speech > 0.12 and minutes >= 70)
                    return {
                        "motion_score": round(motion, 3),
                        "speech_ratio": round(speech, 3),
                        "slide_probability": round(max(0, 1 - motion - speech), 3),
                        "is_real_movie": is_real,
                    }
                except Exception:
                    return None
                finally:
                    if os.path.exists(seg_file):
                        try:
                            os.remove(seg_file)
                        except Exception:
                            pass
            return await loop.run_in_executor(None, _analyze)

        async def _youtube_mode(query, limit, tmdb_key, openai_key, tmdb_base, genre_map, ua, tag, force_new):
            lines = [f"<b>🔍 Searching YouTube for: {esc(query)}</b>"]
            if force_new:
                lines.append("<i>(forced fresh search)</i>")
            lines.append("")
            yt_query = f"{query} full movie"
            loop = asyncio.get_running_loop()
            def _yt_search():
                BAD_WORDS = ["trailer", "teaser", "reaction", "clip", "review", "recap", "explained", "ending", "scene", "трейлер", "обзор"]
                try:
                    ytdlp = _find_ytdlp()
                    cmd = [ytdlp, f"ytsearch20:{yt_query}", "--dump-json", "--flat-playlist", "--no-warnings"]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    videos = []
                    for line in result.stdout.strip().split("\n"):
                        if not line:
                            continue
                        try:
                            v = _json.loads(line)
                        except Exception:
                            continue
                        dur_sec = v.get("duration") or 0
                        minutes = int(dur_sec / 60)
                        title = v.get("title", "")
                        if minutes < 60 or minutes > 240:
                            continue
                        if any(w in title.lower() for w in BAD_WORDS):
                            continue
                        videos.append({
                            "youtube_id": v.get("id", ""),
                            "title": title,
                            "description": (v.get("description", "") or "")[:300],
                            "channel": v.get("channel") or v.get("uploader") or "?",
                            "minutes": minutes,
                            "url": v.get("url") or f"https://www.youtube.com/watch?v={v.get('id', '')}",
                        })
                    return videos
                except Exception:
                    return []
            videos = await loop.run_in_executor(None, _yt_search)
            if not videos:
                return lines[0] + "\nNo full-length movie candidates found on YouTube."
            lines.append(f"Found {len(videos)} candidates, analyzing up to {limit}...\n")

            # Collect results for sorting: list of (passed_count, entry_lines)
            analyzed = []
            skipped_cached = 0
            processed = 0
            for video in videos:
                if processed >= limit:
                    break
                vid_title = video["title"][:80]
                yt_id = video["youtube_id"]
                yt_url = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else video["url"]
                minutes = video["minutes"]
                # Silently skip already-cached movies
                if _is_cached(youtube_id=yt_id):
                    skipped_cached += 1
                    continue
                entry = []
                entry.append(f'<b>📽 <a href="{yt_url}">{esc(vid_title)}</a></b>')
                entry.append(f"⏱ {minutes} min | 📺 {esc(video['channel'])}")
                ai_result = None
                ai_ok = False
                if openai_key:
                    ai_result = await _ai_classify(video["title"], video["description"], minutes, openai_key, loop)
                    if ai_result:
                        ai_type = ai_result.get("type", "UNCERTAIN")
                        ai_conf = ai_result.get("confidence", 0)
                        ai_guess = ai_result.get("title_guess", "")
                        ai_year = ai_result.get("year_guess", 0)
                        if ai_type in ("MOVIE_FULL", "SERIES") and ai_conf >= 0.5:
                            ai_ok = True
                            entry.append(f"🤖 AI: ✅ {ai_type} → \"{esc(ai_guess)}\" ({ai_year}), {ai_conf:.0%}")
                        else:
                            entry.append(f"🤖 AI: ❌ {ai_type} ({ai_conf:.0%})")
                    else:
                        entry.append("🤖 AI: ❌ classification failed")
                else:
                    entry.append("🤖 AI: ⏭ no API key")
                tmdb_ok = False
                tmdb_data = None
                search_title = ai_result.get("title_guess", "") if ai_result else ""
                if not search_title:
                    search_title = video["title"]
                if tmdb_key:
                    tmdb_data = await _tmdb_verify(search_title, ai_result.get("year_guess", 0) if ai_result else 0, tmdb_key, tmdb_base)
                    if tmdb_data:
                        tmdb_ok = True
                        genres = ", ".join(genre_map.get(g, "?") for g in tmdb_data.get("genre_ids", []))
                        entry.append(f"🎬 TMDb: ✅ {esc(tmdb_data['title'])} ({tmdb_data['year']}) — {esc(genres)}")
                    else:
                        entry.append("🎬 TMDb: ❌ not found")
                if tmdb_ok and tmdb_data:
                    _LANG_MAP = {
                        "french": "fr", "german": "de", "spanish": "es", "italian": "it",
                        "russian": "ru", "japanese": "ja", "korean": "ko", "indian": "hi",
                        "hindi": "hi", "swedish": "sv", "norwegian": "no", "danish": "da",
                        "turkish": "tr", "chinese": "zh", "thai": "th", "portuguese": "pt",
                        "polish": "pl", "dutch": "nl", "finnish": "fi", "czech": "cs",
                        "arabic": "ar", "persian": "fa", "greek": "el", "hungarian": "hu",
                        "romanian": "ro", "serbian": "sr", "croatian": "hr", "bulgarian": "bg",
                        "estonian": "et", "latvian": "lv", "georgian": "ka",
                        "французский": "fr", "немецкий": "de", "итальянский": "it",
                        "испанский": "es", "корейский": "ko", "японский": "ja",
                        "турецкий": "tr", "индийский": "hi",
                    }
                    requested_lang = None
                    for w in query.lower().split():
                        if w in _LANG_MAP:
                            requested_lang = _LANG_MAP[w]
                            break
                    if requested_lang:
                        movie_lang = tmdb_data.get("original_language", "")
                        if movie_lang != requested_lang:
                            entry.append(f"🌍 Country: ❌ wanted {requested_lang}, got {movie_lang} — skipped")
                            analyzed.append((0, entry))
                            processed += 1
                            continue
                        else:
                            entry.append(f"🌍 Country: ✅ {movie_lang}")
                verified_title = tmdb_data["title"] if tmdb_ok and tmdb_data else None
                if verified_title and _is_cached(title=verified_title):
                    skipped_cached += 1
                    continue
                video_ok = False
                analysis = await _analyze_middle(video["youtube_id"], minutes, loop)
                if analysis:
                    motion = analysis["motion_score"]
                    speech = analysis["speech_ratio"]
                    is_real = analysis["is_real_movie"]
                    video_ok = is_real
                    if is_real:
                        entry.append(f"🔬 Video: ✅ motion={motion:.2f}, speech={speech:.2f}")
                    else:
                        entry.append(f"🔬 Video: ❌ motion={motion:.2f}, speech={speech:.2f}")
                else:
                    entry.append("🔬 Video: ❌ analysis failed")
                checks = []
                if ai_ok:
                    checks.append("AI ✅")
                if tmdb_ok:
                    checks.append("TMDb ✅")
                if video_ok:
                    checks.append("Video ✅")
                passed = len(checks)
                if passed >= 2:
                    entry.append(f"<b>Result: {' | '.join(checks)} — ✅ PASS</b>")
                    movie_title = ai_result.get("title_guess", video["title"]) if ai_result else video["title"]
                    _save_movie(
                        title=movie_title,
                        year=str(ai_result.get("year_guess", "")) if ai_result else "",
                        genres=", ".join(genre_map.get(g, "?") for g in (tmdb_data or {}).get("genre_ids", [])) if tmdb_ok else "",
                        tag=tag,
                        youtube_id=video["youtube_id"],
                        youtube_url=yt_url,
                        tmdb_id=(tmdb_data or {}).get("tmdb_id") if tmdb_ok else None,
                        minutes=minutes,
                        motion=analysis["motion_score"] if analysis else 0,
                        speech=analysis["speech_ratio"] if analysis else 0,
                        ai_conf=ai_result.get("confidence", 0) if ai_result else 0,
                        lang=(tmdb_data or {}).get("original_language", "") if tmdb_ok else "",
                    )
                    entry.append("💾 <i>Saved to cache</i>")
                elif passed == 1:
                    entry.append(f"<b>Result: {' | '.join(checks)} — ⚠️ WEAK</b>")
                else:
                    entry.append("<b>Result: ❌ FAIL</b>")
                analyzed.append((passed, entry))
                processed += 1

            # Sort: PASS (3,2) first, then WEAK (1), then FAIL (0)
            analyzed.sort(key=lambda x: x[0], reverse=True)

            # Render each result as a collapsible blockquote
            for passed, entry in analyzed:
                header = entry[0]  # title line
                detail = "\n".join(entry[1:])
                lines.append(header)
                lines.append("<blockquote expandable>" + detail + "</blockquote>")
                lines.append("")

            summary = f"Analyzed {processed}/{len(videos)} candidates."
            if skipped_cached:
                summary += f" ({skipped_cached} already cached — skipped)"
            lines.append(summary)
            return "\n".join(lines)

        cache_keywords = {"cache", "cached", "list", "saved", "library", "history", "кэш", "кеш", "список"}
        if query_lower in cache_keywords or query_lower.startswith("cache ") or query_lower.startswith("cached ") or query_lower.startswith("list "):
            m = re.search(r'(\d+)', query_lower)
            list_limit = int(m.group(1)) if m else 20
            return _list_cache(list_limit)

        if not TMDB_KEY:
            if query_lower in cache_keywords or query_lower.startswith("cache ") or query_lower.startswith("cached ") or query_lower.startswith("list "):
                m = re.search(r'(\d+)', query_lower)
                list_limit = int(m.group(1)) if m else 20
                return _list_cache(list_limit)
            return "TMDB_API_KEY not configured."

        yt_mode = True   # default: always search for movies (YouTube discovery)
        force_new = False
        info_triggers = ["info", "инфо", "about", "описание", "details"]
        new_triggers = ["new", "новые", "fresh", "ещё", "еще", "другие"]
        # Noise words to strip from the query (don't carry meaning for search)
        noise_words = {"хочу", "хотел", "хотела", "давай", "покажи", "можно", "мне",
                       "фильм", "фильмы", "кино", "movie", "movies", "film", "films",
                       "какой-нибудь", "какой", "какую", "нибудь", "хороший", "хорошую",
                       "интересный", "интересную", "классный", "классную",
                       "want", "to", "i", "a", "the", "some", "good", "nice",
                       "me", "show", "suggest", "recommend", "please",
                       "youtube", "ютуб", "найди", "find", "analyze", "watch",
                       "смотреть", "посмотреть"}

        for trigger in new_triggers:
            if trigger in query_lower:
                force_new = True
                raw_query = raw_query.lower().replace(trigger, "").strip()
                break

        for trigger in info_triggers:
            if trigger in query_lower:
                yt_mode = False
                raw_query = raw_query.lower().replace(trigger, "").strip()
                break

        limit = 3
        m_limit = re.search(r'(\d+)\s*(movie|film|фильм|кино)', query_lower)
        if m_limit:
            limit = min(int(m_limit.group(1)), 5)
            raw_query = re.sub(r'\d+\s*(movie|film|фильм|кино)s?', '', raw_query).strip()

        # Strip noise words
        raw_query = " ".join(w for w in raw_query.split() if w.lower() not in noise_words).strip()

        if not raw_query:
            return (
                "<b>🎬 Movie Search</b>\n\n"
                "Usage:\n"
                "  <code>movie french comedy</code> — discover movies on YouTube\n"
                "  <code>movie new french comedy</code> — force fresh search (skip cache)\n"
                "  <code>movie info inception</code> — TMDB info about a specific movie\n"
                "  <code>movie cache</code> — list cached movies\n"
            )

        if not yt_mode:
            return await _tmdb_info_mode(raw_query, TMDB_KEY, TMDB_BASE, TMDB_IMG, GENRE_MAP, UA)

        _init_db()
        tag = _normalize_tag(raw_query)
        if not force_new:
            cached = _get_cached_movies(tag, limit)
            if cached:
                return _format_cached(cached, tag)

        return await _youtube_mode(raw_query, limit, TMDB_KEY, OPENAI_KEY, TMDB_BASE, GENRE_MAP, UA, tag, force_new)

    except Exception as e:
        return f"Failed: {e}"
