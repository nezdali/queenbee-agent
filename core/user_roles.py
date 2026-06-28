"""
Persistent user → roles store.

Roles are stored as JSON outside the project directory so they survive
deployments and don't sit in the working dir. The path is overridable
via the USER_ROLES_FILE env var (default:
~/.local/share/telegram_bot/user_roles.json).

The file is also mirrored to Azure Key Vault as `USER-ROLES-JSON` so the
mapping survives VM rebuilds.

File format:
    {
      "<user_id>": {"roles": ["public", "finance"], "username": "alice"},
      ...
    }

This module is intentionally tiny — no async, no locking. Concurrent edits
from the Telegram admin are rare enough that the last writer wins.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _default_path() -> Path:
    """Return the default user_roles.json path (~/.local/share/...)."""
    return Path.home() / ".local" / "share" / "telegram_bot" / "user_roles.json"


def _path() -> Path:
    """Resolve the user_roles file path on every call (so tests can repoint it)."""
    return Path(
        os.getenv("USER_ROLES_FILE", str(_default_path()))
    ).expanduser()


# Back-compat alias for callers that imported USER_ROLES_FILE directly.
# Note: this captures the value at import time; for live updates use _path().
USER_ROLES_FILE: Path = _path()


def _kv_secret_name() -> str:
    return "USER-ROLES-JSON"


def _ensure_dir() -> None:
    _path().parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def load() -> dict[int, dict]:
    """Load the user→roles mapping from disk. Returns {} if file missing."""
    p = _path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to read %s: %s", p, e)
        return {}
    out: dict[int, dict] = {}
    for k, v in raw.items():
        try:
            out[int(k)] = {
                "roles": list(v.get("roles", ["public"])),
                "username": str(v.get("username", "")),
            }
        except (TypeError, ValueError):
            continue
    return out


def save(mapping: dict[int, dict]) -> None:
    """Persist the mapping to disk and mirror to KV."""
    _ensure_dir()
    payload = {
        str(uid): {
            "roles": list(info.get("roles", ["public"])),
            "username": str(info.get("username", "")),
        }
        for uid, info in mapping.items()
    }
    _path().write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        from config import save_secret_to_keyvault
        save_secret_to_keyvault(_kv_secret_name(), json.dumps(payload))
    except Exception as e:
        logger.debug("Could not mirror user roles to KV: %s", e)


def hydrate_from_kv() -> None:
    """If the local file is missing, try to seed it from Key Vault."""
    p = _path()
    if p.exists():
        return
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        kv_url = os.getenv("AZURE_KEYVAULT_URL", "")
        if not kv_url:
            return
        client = SecretClient(vault_url=kv_url, credential=DefaultAzureCredential())
        secret = client.get_secret(_kv_secret_name())
        if secret and secret.value:
            _ensure_dir()
            p.write_text(secret.value, encoding="utf-8")
            logger.info("user_roles seeded from KV -> %s", p)
    except Exception as e:
        logger.debug("hydrate_from_kv: %s", e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_roles(user_id: int, fallback: list[str] | None = None) -> list[str]:
    """Return the role list for a user. Empty mapping falls back to fallback."""
    mapping = load()
    info = mapping.get(int(user_id))
    if info is None:
        return list(fallback) if fallback else ["public"]
    return list(info["roles"])


def set_roles(user_id: int, roles: list[str], username: str = "") -> dict:
    """Replace a user's roles. Returns the saved record."""
    mapping = load()
    record = {
        "roles": [r.strip().lower() for r in roles if r and r.strip()],
        "username": username.lstrip("@").lower(),
    }
    if "public" not in record["roles"]:
        record["roles"].insert(0, "public")
    mapping[int(user_id)] = record
    save(mapping)
    return record


def remove_user(user_id: int) -> bool:
    """Remove a user. Returns True if a row was deleted."""
    mapping = load()
    if int(user_id) in mapping:
        mapping.pop(int(user_id))
        save(mapping)
        return True
    return False


def list_all() -> dict[int, dict]:
    """Return the full user→roles mapping (loaded fresh from disk)."""
    return load()
