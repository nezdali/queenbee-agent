"""
Strava OAuth2 service — per-user tokens.

Each Telegram user has their own access/refresh token, stored as
`strava_token_<user_id>.json` (overridable via STRAVA_TOKEN_DIR env var).

Authentication flow:
1. Bot sends the user an authorization URL
2. User clicks it → Strava redirects to http://localhost/exchange_token?code=XXX
3. The page won't load, but the code= parameter is visible in the browser URL bar
4. User pastes the code back: /stravaauth <code>
5. Bot exchanges it for tokens and saves them keyed by the user's Telegram ID.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Legacy single-file path (used as fallback when no user_id is passed).
STRAVA_TOKEN_FILE = os.getenv("STRAVA_TOKEN_FILE", "strava_token.json")
# Directory for per-user token files.
STRAVA_TOKEN_DIR = os.getenv("STRAVA_TOKEN_DIR", ".")
_REDIRECT_URI = "http://localhost/exchange_token"
_AUTH_URL = "https://www.strava.com/oauth/mobile/authorize"
_TOKEN_URL = "https://www.strava.com/oauth/token"
_SCOPES = "read,activity:read_all,profile:read_all"


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------

def _token_path(user_id: int | None) -> Path:
    """Return the on-disk path for this user's token.

    When user_id is falsy, falls back to the legacy single-user file so
    existing deployments keep working.
    """
    if user_id:
        return Path(STRAVA_TOKEN_DIR) / f"strava_token_{int(user_id)}.json"
    return Path(STRAVA_TOKEN_FILE)


def _load_token(user_id: int | None = None) -> dict | None:
    p = _token_path(user_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Backward-compat: if per-user file is missing but the legacy shared
    # file exists, fall back to it (lets the original owner keep working
    # without re-authorizing immediately after the migration).
    if user_id:
        legacy = Path(STRAVA_TOKEN_FILE)
        if legacy.exists():
            try:
                return json.loads(legacy.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


def _save_token(data: dict, user_id: int | None = None) -> None:
    p = _token_path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Strava token saved to %s", p)
    # Best-effort backup to Azure Key Vault so the token survives VM rebuilds.
    # Per-user tokens are stored as STRAVA-TOKEN-<user_id>-JSON;
    # the legacy shared token stays as STRAVA-TOKEN-JSON.
    try:
        _backup_token_to_kv(data, user_id)
    except Exception as e:
        logger.debug("KV backup of Strava token failed (non-fatal): %s", e)


def _backup_token_to_kv(data: dict, user_id: int | None) -> None:
    kv_url = os.getenv("AZURE_KEYVAULT_URL", "")
    if not kv_url:
        return
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError:
        return
    name = (
        f"STRAVA-TOKEN-{int(user_id)}-JSON"
        if user_id
        else "STRAVA-TOKEN-JSON"
    )
    client = SecretClient(vault_url=kv_url, credential=DefaultAzureCredential())
    client.set_secret(name, json.dumps(data, ensure_ascii=False))
    logger.info("Strava token backed up to KV secret %s", name)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_auth_url() -> str:
    from config import STRAVA_CLIENT_ID
    return (
        f"{_AUTH_URL}"
        f"?client_id={STRAVA_CLIENT_ID}"
        f"&redirect_uri={_REDIRECT_URI}"
        f"&response_type=code"
        f"&approval_prompt=auto"
        f"&scope={_SCOPES}"
    )


async def exchange_code(code: str, user_id: int | None = None) -> dict:
    """Exchange an authorization code for access + refresh tokens."""
    import aiohttp
    from config import STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET

    async with aiohttp.ClientSession() as session:
        async with session.post(_TOKEN_URL, data={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
        }) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Strava token exchange failed: HTTP {resp.status} — {body[:300]}")
            data = await resp.json()

    _save_token(data, user_id)
    return data


async def _refresh_token(user_id: int | None = None) -> dict:
    """Silently refresh an expired token."""
    import aiohttp
    from config import STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET

    token = _load_token(user_id)
    if not token or not token.get("refresh_token"):
        raise RuntimeError("No Strava refresh token available. Run /stravaconnect first.")

    async with aiohttp.ClientSession() as session:
        async with session.post(_TOKEN_URL, data={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "refresh_token": token["refresh_token"],
            "grant_type": "refresh_token",
        }) as resp:
            resp.raise_for_status()
            data = await resp.json()

    _save_token(data, user_id)
    return data


async def get_access_token(user_id: int | None = None) -> str:
    """Return a valid access token for this user, refreshing if expired."""
    import time

    token = _load_token(user_id)
    if not token:
        raise RuntimeError("Not connected to Strava. Use /stravaconnect first.")

    if token.get("expires_at", 0) < time.time() + 60:
        token = await _refresh_token(user_id)

    return token["access_token"]


def is_connected(user_id: int | None = None) -> bool:
    return _load_token(user_id) is not None


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

async def get_athlete(user_id: int | None = None) -> dict:
    import aiohttp
    access_token = await get_access_token(user_id)
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://www.strava.com/api/v3/athlete",
            headers={"Authorization": f"Bearer {access_token}"},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


async def get_activities(per_page: int = 10, page: int = 1, user_id: int | None = None) -> list[dict]:
    import aiohttp
    access_token = await get_access_token(user_id)
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"per_page": per_page, "page": page},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


async def get_activity(activity_id: int, user_id: int | None = None) -> dict:
    import aiohttp
    access_token = await get_access_token(user_id)
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://www.strava.com/api/v3/activities/{activity_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


async def get_stats(athlete_id: int, user_id: int | None = None) -> dict:
    import aiohttp
    access_token = await get_access_token(user_id)
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://www.strava.com/api/v3/athletes/{athlete_id}/stats",
            headers={"Authorization": f"Bearer {access_token}"},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


async def get_activity_zones(activity_id: int, user_id: int | None = None) -> list[dict]:
    """Return zone distribution for an activity (HR + power if available).

    Each entry has shape: {"type": "heartrate"|"power", "distribution_buckets":
    [{"min": int, "max": int, "time": int_seconds}, ...], "sensor_based": bool,
    "points": int, "custom_zones": bool}.
    Strava only returns this for the OWNER of the activity (or if shared).
    """
    import aiohttp
    access_token = await get_access_token(user_id)
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://www.strava.com/api/v3/activities/{activity_id}/zones",
            headers={"Authorization": f"Bearer {access_token}"},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


async def get_activity_kudoers(activity_id: int, user_id: int | None = None) -> list[dict]:
    """Return athletes who gave kudos (likes) to an activity.

    Strava returns a list of SummaryAthlete objects:
    {"id": int, "firstname": str, "lastname": str, "username": str|None,
     "city": str|None, "country": str|None, ...}.
    Visible for activities the authenticated user can see.
    """
    import aiohttp
    access_token = await get_access_token(user_id)
    out: list[dict] = []
    page = 1
    per_page = 200
    async with aiohttp.ClientSession() as session:
        while True:
            async with session.get(
                f"https://www.strava.com/api/v3/activities/{activity_id}/kudoers",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"per_page": per_page, "page": page},
            ) as resp:
                resp.raise_for_status()
                batch = await resp.json()
            if not batch:
                break
            out.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
            if page > 10:  # safety cap
                break
    return out


async def get_activity_comments(activity_id: int, user_id: int | None = None) -> list[dict]:
    """Return comments on an activity (each includes athlete + text + posted_at)."""
    import aiohttp
    access_token = await get_access_token(user_id)
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://www.strava.com/api/v3/activities/{activity_id}/comments",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"per_page": 100},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()
