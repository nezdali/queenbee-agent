async def run(context: dict) -> str:
    import os
    import json
    import re
    import base64
    import secrets
    import pathlib
    import urllib.parse
    from datetime import datetime
    
    try:
        import aiohttp
    except Exception:
        return "Error: aiohttp is not available."

    try:
        user_id = context.get("user_id")
        args = context.get("args", []) or []

        data_dir = pathlib.Path("data")
        data_dir.mkdir(exist_ok=True)
        token_file = data_dir / "spotify_token.json"
        state_file = data_dir / "spotify_oauth_state.json"

        client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
        client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
        redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "").strip()

        def help_text() -> str:
            lines = []
            lines.append("Spotify tool")
            lines.append("")
            lines.append("Usage:")
            lines.append("- spotify help")
            lines.append("- spotify auth")
            lines.append("- spotify callback CODE STATE")
            lines.append("- spotify track <name>")
            lines.append("- spotify artist <name>")
            lines.append("- spotify album <name>")
            lines.append("- spotify search <query>")
            lines.append("- spotify genre <genre>")
            lines.append("- spotify year <year>")
            lines.append("- spotify library [count]")
            lines.append("- spotify playlists")
            lines.append("- spotify recent")
            lines.append("- spotify top [tracks|artists]")
            lines.append("")
            lines.append("Playback control (requires active Spotify device):")
            lines.append("- spotify play <track name>")
            lines.append("- spotify pause")
            lines.append("- spotify skip")
            lines.append("- spotify queue <track name>")
            lines.append("- spotify np  (now playing)")
            lines.append("- spotify preview <track name>  (30s clip in Telegram)")
            lines.append("- spotify devices")
            lines.append("")
            lines.append("Examples:")
            lines.append("- spotify track Numb")
            lines.append("- spotify artist Metallica")
            lines.append("- spotify genre rock")
            lines.append("- spotify year 1999")
            lines.append("- spotify search daft punk")
            lines.append("- spotify play Numb Linkin Park")
            lines.append("- spotify queue Enter Sandman")
            lines.append("- spotify preview Bohemian Rhapsody")
            lines.append("")
            lines.append("Authentication:")
            lines.append("This tool supports Spotify official OAuth Authorization Code flow.")
            lines.append("For first login, set these environment variables on the bot host:")
            lines.append("- SPOTIFY_CLIENT_ID")
            lines.append("- SPOTIFY_CLIENT_SECRET")
            lines.append("- SPOTIFY_REDIRECT_URI")
            lines.append("")
            lines.append("In Spotify Developer Dashboard:")
            lines.append("1. Create an app at https://developer.spotify.com/dashboard")
            lines.append("2. Add your redirect URI exactly as SPOTIFY_REDIRECT_URI")
            lines.append("3. Save settings")
            lines.append("")
            lines.append("Then run: spotify auth")
            lines.append("The tool will return an authorization URL.")
            lines.append("Open it, approve access, and copy the returned code and state from the redirect URL.")
            lines.append("Then send: spotify callback <code> <state>")
            lines.append("")
            lines.append("If app credentials are not configured, public search still works using Spotify client-credentials flow only when SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are set.")
            lines.append("User OAuth is preferable for first login and future account-linked extensions.")
            return "\n".join(lines)

        def load_json(path):
            try:
                if path.exists():
                    return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return {}
            return {}

        def save_json(path, data):
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        async def get_app_token():
            if not client_id or not client_secret:
                return None, "Spotify app credentials are missing. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET."
            basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://accounts.spotify.com/api/token",
                    headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
                    data={"grant_type": "client_credentials"},
                    timeout=30,
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        return None, f"Spotify token error: HTTP {resp.status} - {text[:300]}"
                    data = await resp.json()
                    return data.get("access_token"), None

        async def get_user_token_from_code(code: str):
            if not client_id or not client_secret or not redirect_uri:
                return None, "Missing SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, or SPOTIFY_REDIRECT_URI."
            basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://accounts.spotify.com/api/token",
                    headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                    },
                    timeout=30,
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        return None, f"OAuth exchange failed: HTTP {resp.status} - {text[:400]}"
                    data = json.loads(text)
                    return data, None

        async def refresh_user_token(refresh_token: str):
            if not client_id or not client_secret:
                return None, "Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET."
            basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://accounts.spotify.com/api/token",
                    headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                    timeout=30,
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        return None, f"Refresh failed: HTTP {resp.status} - {text[:400]}"
                    data = json.loads(text)
                    return data, None

        def token_valid(tok: dict) -> bool:
            try:
                exp = int(tok.get("expires_at", 0))
                return exp > int(datetime.utcnow().timestamp()) + 30
            except Exception:
                return False

        async def get_best_token():
            tok = load_json(token_file)
            if tok.get("access_token") and token_valid(tok):
                return tok.get("access_token"), None, "user"
            if tok.get("refresh_token"):
                refreshed, err = await refresh_user_token(tok.get("refresh_token"))
                if refreshed and refreshed.get("access_token"):
                    if not refreshed.get("refresh_token"):
                        refreshed["refresh_token"] = tok.get("refresh_token")
                    refreshed["expires_at"] = int(datetime.utcnow().timestamp()) + int(refreshed.get("expires_in", 3600))
                    save_json(token_file, refreshed)
                    return refreshed.get("access_token"), None, "user"
            app_token, err = await get_app_token()
            if app_token:
                return app_token, None, "app"
            return None, err or "No Spotify token available.", None

        async def spotify_get(url: str, token: str, params=None):
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                    timeout=30,
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        return None, f"Spotify API error: HTTP {resp.status} - {text[:400]}"
                    try:
                        return json.loads(text), None
                    except Exception:
                        return None, "Spotify API returned invalid JSON."

        async def spotify_put(url: str, token: str, json_body=None, params=None):
            async with aiohttp.ClientSession() as session:
                kwargs = {
                    "headers": {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    "timeout": 30,
                }
                if params:
                    kwargs["params"] = params
                if json_body is not None:
                    kwargs["data"] = json.dumps(json_body)
                async with session.put(url, **kwargs) as resp:
                    text = await resp.text()
                    if resp.status not in (200, 202, 204):
                        return None, f"Spotify API error: HTTP {resp.status} - {text[:400]}"
                    if text.strip():
                        try:
                            return json.loads(text), None
                        except Exception:
                            return {}, None
                    return {}, None

        async def spotify_post(url: str, token: str, json_body=None, params=None):
            async with aiohttp.ClientSession() as session:
                kwargs = {
                    "headers": {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    "timeout": 30,
                }
                if params:
                    kwargs["params"] = params
                if json_body is not None:
                    kwargs["data"] = json.dumps(json_body)
                async with session.post(url, **kwargs) as resp:
                    text = await resp.text()
                    if resp.status not in (200, 202, 204):
                        return None, f"Spotify API error: HTTP {resp.status} - {text[:400]}"
                    if text.strip():
                        try:
                            return json.loads(text), None
                        except Exception:
                            return {}, None
                    return {}, None

        def esc(text):
            """Escape HTML special chars."""
            return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        def fmt_artists(items):
            return ", ".join([a.get("name", "?") for a in (items or [])]) or "Unknown"

        def popularity_bar(score):
            try:
                n = int(score)
                filled = n // 10
                return "●" * filled + "○" * (10 - filled) + f" {n}%"
            except Exception:
                return str(score)

        def fmt_track(track):
            album = track.get("album", {}) or {}
            artists = fmt_artists(track.get("artists", []))
            release = album.get("release_date", "?")
            year = release[:4] if release else "?"
            url = (((track.get("external_urls") or {}).get("spotify")) or "")
            link = f'<a href="{esc(url)}">Open in Spotify</a>' if url else "N/A"
            return "\n".join([
                f"🎵 <b>{esc(track.get('name', '?'))}</b>",
                f"🎤 {esc(artists)}",
                f"💿 {esc(album.get('name', '?'))} ({year})",
                f"📊 {popularity_bar(track.get('popularity', '?'))}",
                f"🔗 {link}",
            ])

        def fmt_artist(artist):
            genres = ", ".join(artist.get("genres", [])[:10]) or "Unknown"
            url = (((artist.get("external_urls") or {}).get("spotify")) or "")
            followers = ((artist.get('followers') or {}).get('total', 0))
            try:
                followers_str = f"{int(followers):,}"
            except Exception:
                followers_str = str(followers)
            link = f'<a href="{esc(url)}">Open in Spotify</a>' if url else "N/A"
            return "\n".join([
                f"🎤 <b>{esc(artist.get('name', '?'))}</b>",
                f"🎸 {esc(genres)}",
                f"👥 {followers_str} followers",
                f"📊 {popularity_bar(artist.get('popularity', '?'))}",
                f"🔗 {link}",
            ])

        def fmt_album(album):
            artists = fmt_artists(album.get("artists", []))
            release = album.get("release_date", "?")
            year = release[:4] if release else "?"
            url = (((album.get("external_urls") or {}).get("spotify")) or "")
            link = f'<a href="{esc(url)}">Open in Spotify</a>' if url else "N/A"
            return "\n".join([
                f"💿 <b>{esc(album.get('name', '?'))}</b>",
                f"🎤 {esc(artists)}",
                f"📅 {year}",
                f"🎵 {album.get('total_tracks', '?')} tracks",
                f"🔗 {link}",
            ])

        # --- Russian / voice intent normalization ---
        # Map natural phrases like "поставь песню Numb" → ["play", "Numb"].
        # Reads raw_extra / user_message because voice_api may have translated
        # args to English already, losing the Russian play verb.
        _RU_PLAY = {
            "поставь", "поставить", "постав", "ставь", "включи", "включить",
            "врубай", "врубить", "сыграй", "сыграть", "проиграй", "заиграй",
            "плей", "запусти", "запустить", "начни", "исполни", "играй", "играть",
        }
        _RU_PAUSE = {"пауза", "паузу", "останови", "стоп", "остановить"}
        _RU_SKIP = {
            "следующая", "следующий", "следующую", "дальше", "скип", "пропусти",
            "переключи", "переключить",
        }
        _RU_QUEUE_VERBS = {"добавь", "добавить", "поставь"}
        _RU_NOUNS = {
            "песню", "песня", "песни", "песнь",
            "трек", "трека", "треки",
            "композицию", "композиция", "композиции", "композ",
            "музыку", "музыка", "музыки",
            "мелодию", "мелодия", "мелодии",
            "альбом", "альбома", "альбомы",
            "что-нибудь", "что-то",
        }
        _RU_FILLERS = {
            "мне", "нам", "пожалуйста", "сейчас", "какую-то", "какую",
            "любую", "любой", "плз", "плиз", "ну", "вот", "там", "ка",
            "в", "на", "о", "об", "про", "от", "для",
        }

        def _normalize_voice_intent():
            sources = []
            for key in ("user_message", "raw_extra"):
                v = context.get(key)
                if v:
                    sources.append(str(v))
            if args:
                sources.append(" ".join(str(a) for a in args))
            if not sources:
                return None
            text = " ".join(sources).lower()
            words = [w for w in re.split(r"[\s,.!?;:]+", text) if w]
            if not words:
                return None
            # Preview intent: short clip without active Spotify device.
            # Triggers: "превью", "отрывок", "кусочек", "сэмпл", "demo", "preview",
            # or "послушать" (без активного Spotify).
            _RU_PREVIEW = {
                "превью", "отрывок", "отрывка", "отрывки",
                "кусочек", "кусок", "кусочка",
                "сэмпл", "семпл", "сэмпла", "семпла",
                "demo", "preview", "snippet", "clip",
                "послушать", "послушай",
            }
            preview_idx = -1
            for i, w in enumerate(words):
                if w in _RU_PREVIEW:
                    preview_idx = i
                    break
            if preview_idx >= 0:
                rest = [t for t in words if t not in _RU_PREVIEW
                        and t not in _RU_PLAY and t not in _RU_NOUNS
                        and t not in _RU_FILLERS and t not in {"spotify", "spotifi"}]
                if rest:
                    return ["preview"] + rest

            # Find first play / pause / skip verb
            verb = None
            verb_idx = -1
            for i, w in enumerate(words):
                if w in _RU_PAUSE:
                    return ["pause"]
                if w in _RU_SKIP:
                    return ["skip"]
                if w in {"добавь", "добавить"}:
                    rest = [t for t in words[i + 1:] if t not in {"в", "очередь"}]
                    while rest and (rest[0] in _RU_NOUNS or rest[0] in _RU_FILLERS):
                        rest.pop(0)
                    _DROP_Q = {"spotify", "spotifi", "song", "track", "music", "музыка", "песня", "трек"}
                    rest = [t for t in rest if t not in _DROP_Q]
                    return ["queue"] + rest
                if w in _RU_PLAY:
                    verb = w
                    verb_idx = i
                    break
            if not verb:
                return None
            rest = words[verb_idx + 1:]
            # Detect "в очередь" → queue
            is_queue = False
            for j, w in enumerate(rest):
                if w == "очередь":
                    is_queue = True
                    # remove "в очередь" / "очередь" tokens
                    rest = [t for t in rest if t not in {"в", "очередь"}]
                    break
            # Strip leading nouns + fillers, but keep the actual title.
            while rest and (rest[0] in _RU_NOUNS or rest[0] in _RU_FILLERS):
                rest.pop(0)
            # Drop trigger keywords that may have leaked in (spotify, music words)
            _DROP = {"spotify", "spotifi", "song", "track", "music", "музыка", "песня", "трек"}
            rest = [t for t in rest if t not in _DROP]
            action = "queue" if is_queue else "play"
            return [action] + rest

        _normalized = _normalize_voice_intent()
        if _normalized:
            args = _normalized

        if not args:
            return help_text()

        cmd = (args[0] or "").strip().lower()

        if cmd in {"help", "-h", "--help"}:
            return help_text()

        if cmd == "auth":
            if not client_id or not redirect_uri:
                return "Spotify OAuth is not configured. Set SPOTIFY_CLIENT_ID and SPOTIFY_REDIRECT_URI, and preferably SPOTIFY_CLIENT_SECRET too.\n\n" + help_text()
            state = secrets.token_urlsafe(24)
            save_json(state_file, {"state": state, "user_id": user_id, "created_at": int(datetime.utcnow().timestamp())})
            params = {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": "user-read-email user-read-private user-library-read user-read-recently-played playlist-read-private playlist-read-collaborative user-top-read user-modify-playback-state user-read-playback-state user-read-currently-playing",
                "show_dialog": "true",
            }
            url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(params)
            return "Open this Spotify authorization URL and approve access:\n" + url + "\n\nAfter approval, copy code and state from the redirected URL and send:\nspotify callback <code> <state>"

        if cmd == "callback":
            if len(args) < 3:
                return "Usage: spotify callback <code> <state>"
            code = args[1]
            state = args[2]
            saved = load_json(state_file)
            if not saved or saved.get("state") != state:
                return "Invalid OAuth state. Run 'spotify auth' again and use the newest returned state."
            if saved.get("user_id") != user_id:
                return "This OAuth state belongs to a different user."
            token_data, err = await get_user_token_from_code(code)
            if err:
                return err
            token_data["expires_at"] = int(datetime.utcnow().timestamp()) + int(token_data.get("expires_in", 3600))
            save_json(token_file, token_data)
            return "Spotify authentication successful. User token saved. You can now search with: spotify search <query>"

        token, err, token_type = await get_best_token()
        if err or not token:
            return (err or "Spotify authentication failed.") + "\n\n" + help_text()

        if cmd == "track":
            query = " ".join(args[1:]).strip()
            if not query:
                return "Usage: spotify track <name>"
            data, err = await spotify_get("https://api.spotify.com/v1/search", token, {"q": query, "type": "track", "limit": 5})
            if err:
                return err
            items = (((data or {}).get("tracks") or {}).get("items") or [])
            if not items:
                return f"No tracks found for: {query}"
            out = [f"🎵 <b>Top Spotify tracks for:</b> <i>{esc(query)}</i>"]
            for i, item in enumerate(items[:5], 1):
                out.append("")
                out.append(f"<b>{i}.</b> {fmt_track(item)}")
            return "\n".join(out)

        if cmd == "artist" or cmd == "band":
            query = " ".join(args[1:]).strip()
            if not query:
                return "Usage: spotify artist <name>"
            data, err = await spotify_get("https://api.spotify.com/v1/search", token, {"q": query, "type": "artist", "limit": 5})
            if err:
                return err
            items = (((data or {}).get("artists") or {}).get("items") or [])
            if not items:
                return f"No artists found for: {query}"
            out = [f"🎤 <b>Top Spotify artists for:</b> <i>{esc(query)}</i>"]
            for i, item in enumerate(items[:5], 1):
                out.append("")
                out.append(f"<b>{i}.</b> {fmt_artist(item)}")
            return "\n".join(out)

        if cmd == "album":
            query = " ".join(args[1:]).strip()
            if not query:
                return "Usage: spotify album <name>"
            data, err = await spotify_get("https://api.spotify.com/v1/search", token, {"q": query, "type": "album", "limit": 5})
            if err:
                return err
            items = (((data or {}).get("albums") or {}).get("items") or [])
            if not items:
                return f"No albums found for: {query}"
            out = [f"💿 <b>Top Spotify albums for:</b> <i>{esc(query)}</i>"]
            for i, item in enumerate(items[:5], 1):
                out.append("")
                out.append(f"<b>{i}.</b> {fmt_album(item)}")
            return "\n".join(out)

        if cmd == "genre":
            query = " ".join(args[1:]).strip()
            if not query:
                return "Usage: spotify genre <genre>"
            q = f'genre:"{query}"'
            data, err = await spotify_get("https://api.spotify.com/v1/search", token, {"q": q, "type": "track", "limit": 10})
            if err:
                return err
            items = (((data or {}).get("tracks") or {}).get("items") or [])
            if not items:
                return f"No tracks found for genre: {query}"
            out = [f"🎸 <b>Spotify tracks for genre:</b> <i>{esc(query)}</i>"]
            for i, item in enumerate(items[:10], 1):
                artists = fmt_artists(item.get("artists", []))
                year = (((item.get("album") or {}).get("release_date", "") or "")[:4] or "?")
                url = (((item.get("external_urls") or {}).get("spotify")) or "")
                link = f' <a href="{esc(url)}">▶</a>' if url else ""
                out.append(f"{i}. 🎵 <b>{esc(item.get('name', '?'))}</b> — {esc(artists)} ({year}){link}")
            return "\n".join(out)

        if cmd == "year":
            query = " ".join(args[1:]).strip()
            if not query or not query.isdigit():
                return "Usage: spotify year <year>"
            q = f"year:{query}"
            data, err = await spotify_get("https://api.spotify.com/v1/search", token, {"q": q, "type": "track", "limit": 10})
            if err:
                return err
            items = (((data or {}).get("tracks") or {}).get("items") or [])
            if not items:
                return f"No tracks found for year: {query}"
            out = [f"📅 <b>Spotify tracks for year:</b> <i>{esc(query)}</i>"]
            for i, item in enumerate(items[:10], 1):
                artists = fmt_artists(item.get("artists", []))
                album = ((item.get("album") or {}).get("name", "?"))
                url = (((item.get("external_urls") or {}).get("spotify")) or "")
                link = f' <a href="{esc(url)}">▶</a>' if url else ""
                out.append(f"{i}. 🎵 <b>{esc(item.get('name', '?'))}</b> — {esc(artists)} | 💿 {esc(album)}{link}")
            return "\n".join(out)

        if cmd == "search":
            query = " ".join(args[1:]).strip()
            if not query:
                return "Usage: spotify search <query>"
            data, err = await spotify_get("https://api.spotify.com/v1/search", token, {"q": query, "type": "track,artist,album", "limit": 3})
            if err:
                return err
            tracks = (((data or {}).get("tracks") or {}).get("items") or [])
            artists = (((data or {}).get("artists") or {}).get("items") or [])
            albums = (((data or {}).get("albums") or {}).get("items") or [])
            out = [f"🔍 <b>Spotify search:</b> <i>{esc(query)}</i>"]
            out.append("")
            out.append("🎤 <b>Artists:</b>")
            if artists:
                for i, a in enumerate(artists[:3], 1):
                    genres = ", ".join(a.get("genres", [])[:3]) or "Unknown"
                    url = (((a.get("external_urls") or {}).get("spotify")) or "")
                    link = f' <a href="{esc(url)}">▶</a>' if url else ""
                    out.append(f"  {i}. <b>{esc(a.get('name', '?'))}</b> — {esc(genres)}{link}")
            else:
                out.append("  No artist results")
            out.append("")
            out.append("🎵 <b>Tracks:</b>")
            if tracks:
                for i, t in enumerate(tracks[:3], 1):
                    url = (((t.get("external_urls") or {}).get("spotify")) or "")
                    link = f' <a href="{esc(url)}">▶</a>' if url else ""
                    out.append(f"  {i}. <b>{esc(t.get('name', '?'))}</b> — {esc(fmt_artists(t.get('artists', [])))}{link}")
            else:
                out.append("  No track results")
            out.append("")
            out.append("💿 <b>Albums:</b>")
            if albums:
                for i, a in enumerate(albums[:3], 1):
                    url = (((a.get("external_urls") or {}).get("spotify")) or "")
                    link = f' <a href="{esc(url)}">▶</a>' if url else ""
                    out.append(f"  {i}. <b>{esc(a.get('name', '?'))}</b> — {esc(fmt_artists(a.get('artists', [])))}{link}")
            else:
                out.append("  No album results")
            return "\n".join(out)

        if cmd == "library" or cmd == "saved":
            count = 10
            if len(args) > 1:
                try:
                    count = min(int(args[1]), 50)
                except Exception:
                    pass
            data, err = await spotify_get("https://api.spotify.com/v1/me/tracks", token, {"limit": count})
            if err:
                return err
            items = (data or {}).get("items", [])
            if not items:
                return "Your Spotify library is empty or requires user auth. Run: spotify auth"
            total = (data or {}).get("total", "?")
            out = [f"📚 <b>Your Spotify Library</b> ({total} saved tracks, showing {len(items)})"]
            for i, item in enumerate(items, 1):
                t = item.get("track", {})
                artists = fmt_artists(t.get("artists", []))
                added = (item.get("added_at", "") or "")[:10]
                url = (((t.get("external_urls") or {}).get("spotify")) or "")
                link = f' <a href="{esc(url)}">▶</a>' if url else ""
                out.append(f"{i}. 🎵 <b>{esc(t.get('name', '?'))}</b> — {esc(artists)} ({added}){link}")
            return "\n".join(out)

        if cmd == "playlists":
            data, err = await spotify_get("https://api.spotify.com/v1/me/playlists", token, {"limit": 20})
            if err:
                return err
            items = (data or {}).get("items", [])
            if not items:
                return "No playlists found. Requires user auth — run: spotify auth"
            out = [f"📋 <b>Your Spotify Playlists</b>"]
            for i, p in enumerate(items, 1):
                tracks_count = ((p.get("tracks") or {}).get("total", "?"))
                owner = ((p.get("owner") or {}).get("display_name", "?"))
                url = (((p.get("external_urls") or {}).get("spotify")) or "")
                link = f' <a href="{esc(url)}">▶</a>' if url else ""
                out.append(f"{i}. 🎶 <b>{esc(p.get('name', '?'))}</b> — {tracks_count} tracks (by {esc(owner)}){link}")
            return "\n".join(out)

        if cmd == "recent":
            data, err = await spotify_get("https://api.spotify.com/v1/me/player/recently-played", token, {"limit": 15})
            if err:
                return err
            items = (data or {}).get("items", [])
            if not items:
                return "No recent tracks. Requires user auth — run: spotify auth"
            out = [f"⏱ <b>Recently Played</b>"]
            for i, item in enumerate(items, 1):
                t = item.get("track", {})
                artists = fmt_artists(t.get("artists", []))
                played = (item.get("played_at", "") or "")[:16].replace("T", " ")
                url = (((t.get("external_urls") or {}).get("spotify")) or "")
                link = f' <a href="{esc(url)}">▶</a>' if url else ""
                out.append(f"{i}. 🎵 <b>{esc(t.get('name', '?'))}</b> — {esc(artists)} ({played}){link}")
            return "\n".join(out)

        if cmd == "top":
            top_type = "tracks"
            if len(args) > 1 and args[1].lower().startswith("artist"):
                top_type = "artists"
            data, err = await spotify_get(f"https://api.spotify.com/v1/me/top/{top_type}", token, {"limit": 15, "time_range": "medium_term"})
            if err:
                return err
            items = (data or {}).get("items", [])
            if not items:
                return f"No top {top_type} found. Requires user auth — run: spotify auth"
            if top_type == "artists":
                out = [f"🏆 <b>Your Top Artists</b> (last 6 months)"]
                for i, a in enumerate(items, 1):
                    genres = ", ".join(a.get("genres", [])[:3]) or "?"
                    url = (((a.get("external_urls") or {}).get("spotify")) or "")
                    link = f' <a href="{esc(url)}">▶</a>' if url else ""
                    out.append(f"{i}. 🎤 <b>{esc(a.get('name', '?'))}</b> — {esc(genres)}{link}")
            else:
                out = [f"🏆 <b>Your Top Tracks</b> (last 6 months)"]
                for i, t in enumerate(items, 1):
                    artists = fmt_artists(t.get("artists", []))
                    url = (((t.get("external_urls") or {}).get("spotify")) or "")
                    link = f' <a href="{esc(url)}">▶</a>' if url else ""
                    out.append(f"{i}. 🎵 <b>{esc(t.get('name', '?'))}</b> — {esc(artists)}{link}")
            return "\n".join(out)

        if cmd == "play":
            is_voice = bool(context.get("language"))
            is_ru = (context.get("language") or "").lower().startswith("ru")
            query = " ".join(args[1:]).strip()
            if not query:
                # Resume playback
                _, err = await spotify_put("https://api.spotify.com/v1/me/player/play", token)
                if err:
                    if "NO_ACTIVE_DEVICE" in str(err) or "404" in str(err):
                        if is_voice:
                            return "Нет активного устройства Spotify." if is_ru else "No active Spotify device."
                        return "▶️ No active Spotify device found. Open Spotify on your phone/desktop first.\n\nUse <b>spotify devices</b> to check."
                    return err
                if is_voice:
                    return "Продолжаю воспроизведение." if is_ru else "Resumed playback."
                return "▶️ Playback resumed."
            # Search and play
            data, err = await spotify_get("https://api.spotify.com/v1/search", token, {"q": query, "type": "track", "limit": 1})
            if err:
                return err
            items = (((data or {}).get("tracks") or {}).get("items") or [])
            if not items:
                if is_voice:
                    return (f"Не нашёл трек: {query}." if is_ru else f"No tracks found for: {query}.")
                return f"No tracks found for: {esc(query)}"
            track = items[0]
            uri = track.get("uri", "")
            artists = fmt_artists(track.get("artists", []))
            track_name = track.get("name", "?")
            _, err = await spotify_put("https://api.spotify.com/v1/me/player/play", token, {"uris": [uri]})
            if err:
                if "NO_ACTIVE_DEVICE" in str(err) or "404" in str(err):
                    if is_voice:
                        return (f"Нашёл {track_name} {artists}, но нет активного устройства Spotify."
                                if is_ru else
                                f"Found {track_name} by {artists}, but no active Spotify device.")
                    return f"🎵 Found <b>{esc(track_name)}</b> — {esc(artists)}\n\n⚠️ No active Spotify device. Open Spotify on your phone/desktop first."
                return err
            if is_voice:
                return (f"Играю {track_name}, {artists}." if is_ru else f"Now playing {track_name} by {artists}.")
            url = (((track.get("external_urls") or {}).get("spotify")) or "")
            link = f' <a href="{esc(url)}">Open</a>' if url else ""
            return f"▶️ Now playing: <b>{esc(track_name)}</b> — {esc(artists)}{link}"

        if cmd == "pause" or cmd == "stop":
            is_voice = bool(context.get("language"))
            is_ru = (context.get("language") or "").lower().startswith("ru")
            _, err = await spotify_put("https://api.spotify.com/v1/me/player/pause", token)
            if err:
                if "NO_ACTIVE_DEVICE" in str(err) or "404" in str(err):
                    if is_voice:
                        return "Нет активного устройства Spotify." if is_ru else "No active Spotify device."
                    return "⏸ No active Spotify device found."
                return err
            if is_voice:
                return "Пауза." if is_ru else "Paused."
            return "⏸ Playback paused."

        if cmd == "skip" or cmd == "next":
            is_voice = bool(context.get("language"))
            is_ru = (context.get("language") or "").lower().startswith("ru")
            _, err = await spotify_post("https://api.spotify.com/v1/me/player/next", token)
            if err:
                if "NO_ACTIVE_DEVICE" in str(err) or "404" in str(err):
                    if is_voice:
                        return "Нет активного устройства Spotify." if is_ru else "No active Spotify device."
                    return "⏭ No active Spotify device found."
                return err
            if is_voice:
                return "Следующий трек." if is_ru else "Skipped to next."
            return "⏭ Skipped to next track."

        if cmd == "queue":
            query = " ".join(args[1:]).strip()
            if not query:
                return "Usage: spotify queue <track name>"
            data, err = await spotify_get("https://api.spotify.com/v1/search", token, {"q": query, "type": "track", "limit": 1})
            if err:
                return err
            items = (((data or {}).get("tracks") or {}).get("items") or [])
            if not items:
                return f"No tracks found for: {esc(query)}"
            track = items[0]
            uri = track.get("uri", "")
            artists = fmt_artists(track.get("artists", []))
            _, err = await spotify_post("https://api.spotify.com/v1/me/player/queue", token, params={"uri": uri})
            if err:
                if "NO_ACTIVE_DEVICE" in str(err) or "404" in str(err):
                    return f"🎵 Found <b>{esc(track.get('name', '?'))}</b> — {esc(artists)}\n\n⚠️ No active Spotify device. Open Spotify first."
                return err
            return f"📋 Added to queue: <b>{esc(track.get('name', '?'))}</b> — {esc(artists)}"

        if cmd == "np" or cmd == "nowplaying" or cmd == "now":
            data, err = await spotify_get("https://api.spotify.com/v1/me/player/currently-playing", token)
            if err:
                return err
            if not data or not data.get("item"):
                return "🔇 Nothing is currently playing."
            t = data["item"]
            artists = fmt_artists(t.get("artists", []))
            album = (t.get("album") or {}).get("name", "?")
            progress_ms = data.get("progress_ms", 0)
            duration_ms = t.get("duration_ms", 1)
            progress_s = int(progress_ms / 1000)
            duration_s = int(duration_ms / 1000)
            bar_len = 20
            filled = int(bar_len * progress_ms / max(duration_ms, 1))
            bar = "▓" * filled + "░" * (bar_len - filled)
            is_playing = "▶️" if data.get("is_playing") else "⏸"
            url = (((t.get("external_urls") or {}).get("spotify")) or "")
            link = f' <a href="{esc(url)}">Open</a>' if url else ""
            return "\n".join([
                f"{is_playing} <b>Now Playing</b>",
                f"",
                f"🎵 <b>{esc(t.get('name', '?'))}</b>",
                f"🎤 {esc(artists)}",
                f"💿 {esc(album)}",
                f"",
                f"{bar} {progress_s // 60}:{progress_s % 60:02d} / {duration_s // 60}:{duration_s % 60:02d}",
                f"🔗 {link}" if link else "",
            ])

        if cmd == "devices":
            data, err = await spotify_get("https://api.spotify.com/v1/me/player/devices", token)
            if err:
                return err
            devices = (data or {}).get("devices", [])
            if not devices:
                return "📱 No active Spotify devices found. Open Spotify on your phone/desktop."
            out = ["📱 <b>Your Spotify Devices</b>"]
            for d in devices:
                active = " ✅" if d.get("is_active") else ""
                vol = d.get("volume_percent", "?")
                out.append(f"• <b>{esc(d.get('name', '?'))}</b> ({esc(d.get('type', '?'))}) — Vol: {vol}%{active}")
            return "\n".join(out)

        if cmd == "preview":
            query = " ".join(args[1:]).strip()
            if not query:
                return "Usage: spotify preview <track name>"
            data, err = await spotify_get("https://api.spotify.com/v1/search", token, {"q": query, "type": "track", "limit": 1, "market": "from_token"})
            if err:
                return err
            items = (((data or {}).get("tracks") or {}).get("items") or [])
            if not items:
                return f"No tracks found for: {esc(query)}"
            track = items[0]
            track_id = track.get("id", "")
            preview_url = track.get("preview_url") or ""
            # Spotify deprecated preview_url in API — scrape from embed page
            if not preview_url and track_id:
                try:
                    async with aiohttp.ClientSession() as sess:
                        async with sess.get(
                            f"https://open.spotify.com/embed/track/{track_id}",
                            headers={"User-Tool": "Mozilla/5.0"},
                            timeout=10,
                        ) as resp:
                            if resp.status == 200:
                                html = await resp.text()
                                m = re.search(r"https://p\.scdn\.co/mp3-preview/[a-zA-Z0-9]+", html)
                                if m:
                                    preview_url = m.group(0)
                except Exception:
                    pass
            artists = fmt_artists(track.get("artists", []))
            if not preview_url:
                spotify_url = (((track.get("external_urls") or {}).get("spotify")) or "")
                link = f' <a href="{esc(spotify_url)}">Open in Spotify</a>' if spotify_url else ""
                return f"🎵 <b>{esc(track.get('name', '?'))}</b> — {esc(artists)}\n\n⚠️ No preview available for this track.{link}"
            # Download the 30s preview and return it as audio info
            async with aiohttp.ClientSession() as session:
                async with session.get(preview_url, timeout=30) as resp:
                    if resp.status != 200:
                        return f"Failed to download preview: HTTP {resp.status}"
                    audio_bytes = await resp.read()
            # Save to temp file and return path for bot to send
            import tempfile
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, dir="/tmp")
            tmp.write(audio_bytes)
            tmp.close()
            track_name = track.get("name", "preview")
            return f"__AUDIO_FILE__:{tmp.name}:{esc(track_name)} - {esc(artists)}"

        query = " ".join(args).strip()
        data, err = await spotify_get("https://api.spotify.com/v1/search", token, {"q": query, "type": "track,artist,album", "limit": 2})
        if err:
            return err + "\n\n" + help_text()
        tracks = (((data or {}).get("tracks") or {}).get("items") or [])
        artists = (((data or {}).get("artists") or {}).get("items") or [])
        albums = (((data or {}).get("albums") or {}).get("items") or [])
        out = [f"🔍 <b>Spotify quick search:</b> <i>{esc(query)}</i>"]
        if tracks:
            out.append("")
            out.append("🎵 <b>Best matching track:</b>")
            out.append(fmt_track(tracks[0]))
        if artists:
            out.append("")
            out.append("🎤 <b>Best matching artist:</b>")
            out.append(fmt_artist(artists[0]))
        if albums:
            out.append("")
            out.append("💿 <b>Best matching album:</b>")
            out.append(fmt_album(albums[0]))
        if len(out) == 1:
            out.append("No Spotify results found.")
        return "\n".join(out)
    except Exception as e:
        return f"Spotify tool error: {str(e)}"