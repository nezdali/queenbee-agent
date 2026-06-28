"""
Configuration module.

Loads settings from environment variables (.env supported via python-dotenv).
Secrets and tokens are NEVER stored in source — only via env vars.

Optional: set AZURE_KEYVAULT_URL to hydrate secrets from Azure Key Vault on
startup. Otherwise .env / shell env is the only source.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional Azure Key Vault hydration
# ---------------------------------------------------------------------------
# Maps KV secret name (hyphens) -> env var name (underscores).
# Only listed entries are loaded. Add your own secrets as needed.
_KV_SECRET_MAP: dict[str, str] = {
    "TELEGRAM-BOT-TOKEN":     "TELEGRAM_BOT_TOKEN",
    "OPENAI-API-KEY":         "OPENAI_API_KEY",
    "QB-ADMIN-USER-ID":       "QB_ADMIN_USER_ID",
    "ALLOWED-USERS":          "ALLOWED_USERS",
    "ALLOWED-USERNAMES":      "ALLOWED_USERNAMES",
    "SERPAPI-KEY":            "SERPAPI_KEY",
    "OLLAMA-API-KEY":         "OLLAMA_API_KEY",
    # --- Optional integrations ---
    "STRAVA-CLIENT-ID":       "STRAVA_CLIENT_ID",
    "STRAVA-CLIENT-SECRET":   "STRAVA_CLIENT_SECRET",
    "SPOTIFY-CLIENT-ID":      "SPOTIFY_CLIENT_ID",
    "SPOTIFY-CLIENT-SECRET":  "SPOTIFY_CLIENT_SECRET",
    "SPOTIFY-REDIRECT-URI":   "SPOTIFY_REDIRECT_URI",
    "EWELINK-EMAIL":          "EWELINK_EMAIL",
    "EWELINK-PASSWORD":       "EWELINK_PASSWORD",
    "EWELINK-PHONE":          "EWELINK_PHONE",
    "EWELINK-REGION":         "EWELINK_REGION",
    "HUUM-USERNAME":          "HUUM_USERNAME",
    "HUUM-PASSWORD":          "HUUM_PASSWORD",
    "COZYTOUCH-USERNAME":     "COZYTOUCH_USERNAME",
    "COZYTOUCH-PASSWORD":     "COZYTOUCH_PASSWORD",
    "TMDB-API-KEY":           "TMDB_API_KEY",
    "OPENAI-ADMIN-API-KEY":   "OPENAI_ADMIN_API_KEY",
    "ELEVENLABS-API-KEY":     "ELEVENLABS_API_KEY",
    "YANDEX-API-KEY":         "YANDEX_API_KEY",
}

_KEYVAULT_URL: str = os.getenv("AZURE_KEYVAULT_URL", "")


def _load_from_keyvault() -> None:
    """Hydrate os.environ from Azure Key Vault. No-op if AZURE_KEYVAULT_URL is unset."""
    if not _KEYVAULT_URL:
        return
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError:
        logger.warning("azure-identity / azure-keyvault-secrets not installed; skipping KV load")
        return

    try:
        credential = DefaultAzureCredential()
        kv_client = SecretClient(vault_url=_KEYVAULT_URL, credential=credential)
        loaded = 0
        for kv_name, env_name in _KV_SECRET_MAP.items():
            try:
                secret = kv_client.get_secret(kv_name)
                os.environ[env_name] = (secret.value or "").strip()
                loaded += 1
            except Exception as e:
                logger.debug("KV secret %s not found or unreadable: %s", kv_name, e)
        logger.info("Loaded %d secrets from Key Vault", loaded)
    except Exception as e:
        logger.error("Key Vault load failed: %s", e)


_load_from_keyvault()


def save_secret_to_keyvault(name: str, value: str) -> bool:
    """Persist a secret back to Key Vault. Returns True on success. No-op if KV unset."""
    if not _KEYVAULT_URL or not value:
        return False
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        credential = DefaultAzureCredential()
        kv_client = SecretClient(vault_url=_KEYVAULT_URL, credential=credential)
        kv_client.set_secret(name, value)
        return True
    except Exception as e:
        logger.error("Could not write KV secret %s: %s", name, e)
        return False


# ---------------------------------------------------------------------------
# Required: Telegram + LLM
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# LLM model. Defaults to a small GPT-class model; can be overridden per-user.
LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
CODEX_MODEL: str = os.getenv("CODEX_MODEL", "gpt-4o-mini")

# Model role aliases used by services/model_router.py
CHAT_MODEL: str = os.getenv("CHAT_MODEL", LLM_MODEL)
IMAGE_MODEL: str = os.getenv("IMAGE_MODEL", "gpt-4o-mini")
VIDEO_MODEL: str = os.getenv("VIDEO_MODEL", "gpt-4o-mini")

# Optional: point at an OpenAI-compatible endpoint other than api.openai.com
# (Azure OpenAI, vLLM, LiteLLM proxy, etc.). Leave empty to use the default.
OPENAI_API_BASE: str = os.getenv("OPENAI_API_BASE", "")

# System prompt prepended to every conversation.
SYSTEM_PROMPT: str = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful, friendly AI assistant inside a Telegram chat. "
    "Be concise, clear, and accurate."
)

# Conversation history depth (number of message pairs to remember per user).
HISTORY_DEPTH: int = int(os.getenv("HISTORY_DEPTH", "20"))
MAX_HISTORY: int = HISTORY_DEPTH  # alias used by core.conversation

# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
def _parse_int_list(value: str) -> list[int]:
    out: list[int] = []
    for chunk in (value or "").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            out.append(int(chunk))
    return out


def _parse_str_list(value: str) -> list[str]:
    return [s.strip().lstrip("@").lower() for s in (value or "").split(",") if s.strip()]


# Empty list = no restriction. Comma-separated lists in env.
ALLOWED_USERS: list[int] = _parse_int_list(os.getenv("ALLOWED_USERS", ""))
ALLOWED_USERNAMES: list[str] = _parse_str_list(os.getenv("ALLOWED_USERNAMES", ""))

# Telegram user ID of the bot admin (gets the `admin` role automatically).
# If 0/unset, no user has admin and admin commands are denied.
QB_ADMIN_USER_ID: int = int(os.getenv("QB_ADMIN_USER_ID", "0") or "0")

# Optional: SerpAPI key for Google Shopping / Search in generated tools.
SERPAPI_KEY: str = os.getenv("SERPAPI_KEY", "")

# ---------------------------------------------------------------------------
# Optional third-party integrations (all read from env vars; empty = disabled)
# ---------------------------------------------------------------------------
# Strava (fitness OAuth). Create app at https://www.strava.com/settings/api
# Callback domain: localhost  — flow is /stravaconnect then /stravaauth <code>.
STRAVA_CLIENT_ID: str = os.getenv("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET: str = os.getenv("STRAVA_CLIENT_SECRET", "")

# Spotify (music OAuth). Create app at https://developer.spotify.com/dashboard
SPOTIFY_CLIENT_ID: str = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET: str = os.getenv("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI: str = os.getenv("SPOTIFY_REDIRECT_URI", "")

# eWeLink (Sonoff / iTead smart home — email+password).
EWELINK_EMAIL: str = os.getenv("EWELINK_EMAIL", "")
EWELINK_PASSWORD: str = os.getenv("EWELINK_PASSWORD", "")
EWELINK_PHONE: str = os.getenv("EWELINK_PHONE", "")
EWELINK_REGION: str = os.getenv("EWELINK_REGION", "eu")

# Huum sauna controller (username+password).
HUUM_USERNAME: str = os.getenv("HUUM_USERNAME", "")
HUUM_PASSWORD: str = os.getenv("HUUM_PASSWORD", "")

# Atlantic / Cozytouch (heat pump — username+password).
COZYTOUCH_USERNAME: str = os.getenv("COZYTOUCH_USERNAME", "")
COZYTOUCH_PASSWORD: str = os.getenv("COZYTOUCH_PASSWORD", "")

# TMDB (movie metadata). Get a free API key at https://www.themoviedb.org/settings/api
TMDB_API_KEY: str = os.getenv("TMDB_API_KEY", "")

# OpenAI Admin API key (separate org-level key for usage/cost endpoints).
OPENAI_ADMIN_API_KEY: str = os.getenv("OPENAI_ADMIN_API_KEY", "")

# ElevenLabs TTS (optional, used by media_creator tool).
ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")

# Yandex translation (optional).
YANDEX_API_KEY: str = os.getenv("YANDEX_API_KEY", "")

# ---------------------------------------------------------------------------
# KNOWN_USERS — alias -> Telegram user_id for cross-user lookups
# ---------------------------------------------------------------------------
# Used by Strava handlers (and any other multi-user integration) so the LLM
# can resolve names like "Alice" -> user_id 123456. Format:
#   KNOWN_USERS="alice:123456;bob:7891011;ben:7891011"
# Keys are lowercased on lookup. Multiple aliases can map to the same uid.
def _parse_known_users(value: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for entry in (value or "").split(";"):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        alias, uid_str = entry.split(":", 1)
        alias = alias.strip().lower().lstrip("@")
        uid_str = uid_str.strip()
        if alias and uid_str.isdigit():
            out[alias] = int(uid_str)
    return out


KNOWN_USERS: dict[str, int] = _parse_known_users(os.getenv("KNOWN_USERS", ""))

# ---------------------------------------------------------------------------
# RBAC: role -> permission patterns
# ---------------------------------------------------------------------------
# A user has a permission if any of their roles' patterns matches it
# (exact match, or `*` wildcard). Customise per deployment.
ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin":  ["*"],
    "public": ["public"],
}

# Static (env-driven) user -> roles mapping. Used in addition to the persistent
# JSON store managed by /adduser, /deluser. Format: "uid1:role1,role2;uid2:role3"
def _parse_user_roles(value: str) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for entry in (value or "").split(";"):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        uid_str, roles_str = entry.split(":", 1)
        if uid_str.strip().isdigit():
            out[int(uid_str.strip())] = [r.strip().lower() for r in roles_str.split(",") if r.strip()]
    return out


USER_ROLES: dict[int, list[str]] = _parse_user_roles(os.getenv("USER_ROLES", ""))

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_config() -> None:
    """Fail loudly if required settings are missing."""
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing) +
            "\nSet them in .env or your environment."
        )
    if not QB_ADMIN_USER_ID:
        logger.warning(
            "QB_ADMIN_USER_ID is unset; admin-only commands "
            "(/adduser, /deltool, /setrole, etc.) will be denied for everyone."
        )
