# MANIFEST — review checklist for the GitHub-ready copy

This directory (`telegram_chatbot_public/`) is a sanitized snapshot of the
private project. Use this checklist to verify before publishing.

## What was excluded (intentionally)

### Secrets and auth files
- `.env`
- `gmail_credentials.json`, `gmail_token.json`
- `strava_token.json`, any `strava_token_*.json`
- `youtube_cookies.txt`
- `scheduled_jobs.json`
- Any `*.pem`, `*.key`, `*.crt`
- `upload_secrets_to_kv.sh`
- `sync_from_vm.ps1`

### Personal / domain-specific feature modules
Excluded entirely:
- `handlers/billing.py` — Swedbank PSD2 + Estonian utility bills
- `handlers/email_handler.py` — Gmail send
- `handlers/monitors.py` — daily report, MSFT monitor, task notifier
- `handlers/location.py` — location/weather
- `services/bill_parser.py`
- `services/gmail_service.py`
- `services/regcar_webhook.py`
- `services/swedbank_service.py`
- `services/voice_api.py`

Kept and lightly sanitised:
- `handlers/strava_handlers.py` — generic example name removed
- `services/strava_service.py` — clean, no personal data

### Generated tools — excluded
| Tool | Reason |
| --- | --- |
| `azure_subscription_cost_reporter` | personal Azure cost reporting |
| `check_current_ipv4` | VM-specific |
| `finai` | calls a private localhost recommendation service |
| `gpuvm_manager` | personal VM management |
| `list_chat_members` | personal Telegram channels |
| `list_local_files_vm` | VM filesystem access |
| `local_env_summary` | VM environment dump |
| `msft_price_move_monitor` | notifies a specific user |
| `regcar_kiosk_tool` (+ `regcar_cache.json`, `kiosk2.html`) | personal car-plates |
| `stock_watchlist` (+ json state) | personal stock list |
| `task_notifier` (+ `task_notifications.json`) | notifies specific users |
| `booking_com_search_old.py`, `booking_com_search_serpapi.py` | older duplicates |

### Generated tools — included (30)
See [`tools/README.md`](./tools/README.md) for the full list. All of them
are either:

* **public scrapers / APIs** that need no auth (most tools), or
* **integrations behind env-var credentials** that are silently no-ops when
  the env var is unset: Spotify, eWeLink, Huum sauna, Cozytouch heat pump,
  TMDB-powered movie search, OpenAI usage status, ElevenLabs-backed media
  creator, Duolingo public-profile lookup.

The repo also ships the **Strava** OAuth handler + service + LLM tool schemas
so users can ask natural-language questions about their fitness data.

### Tests
The `testing/` directory was excluded for v1 — it contained many probes that
hardcoded personal account IDs, IBANs, and bank credentials. Re-add clean,
generic tests later.

## What was kept and lightly sanitised

| File | Sanitisation |
|---|---|
| `core/tool_factory.py` | Stripped personal examples from the LLM system prompt + Queen Bee trigger docstring. No `gmail/strava/enefit` module references. |
| `handlers/admin_users.py` | Replaced `PAYBILL_ALLOWED_USERS` hint and `regcar_kiosk_tool` example with generic equivalents. |
| `handlers/utils.py` | Removed hardcoded admin user ID fallback (`824337870`) — now defaults to `0` (no admin). |
| `handlers/strava_handlers.py` | Removed hardcoded example athlete name; now references the env-driven `KNOWN_USERS` map. |
| `core/agent_tools.py` | Re-added clean Strava tool schemas + handlers, using `KNOWN_USERS` from config for cross-user lookup (instead of the original hardcoded names). |

## What was rewritten from scratch

These files had heavy entanglement with billing/gmail/finai and were
replaced with clean minimal versions:

- `bot.py`
- `config.py`
- `requirements.txt`
- `handlers/__init__.py`
- `handlers/core.py`

## What was kept verbatim

- `tool_utils.py`
- `core/__init__.py`, `core/tool_match.py`, `core/tool_utils.py`,
  `core/conversation.py`, `core/output_safety.py`, `core/tool_registry.py`,
  `core/user_roles.py`
- `services/__init__.py`, `services/llm_service.py`, `services/model_router.py`,
  `services/strava_service.py`
- `handlers/admin_users.py` (after the two text edits noted above)
- `handlers/tool_dispatch.py`, `handlers/group.py`, `handlers/queen_bee.py`,
  `handlers/utils.py`
- All 30 tools in `tools/` (see [`tools/README.md`](./tools/README.md))

## Fresh files

- `README.md`
- `.env.example`
- `.gitignore`
- `LICENSE` (MIT)
- `tools/README.md`
- `MANIFEST.md` (this file)

## Recommended review before pushing

1. **Grep for personal identifiers** — they should all be gone, but double-check:
   ```
   rg -n "vahulille|djatsuk|vitali|enefit|swedbank|paybill|copilotbot|824337870|EE502200001106503222|9\.223\.179\.167|785TJW|470TMJ|tarass?|gpuvm|finai|regcar" .
   ```
   Expect zero matches (or only false positives in comments/docs).
2. **Skim the LLM system prompt** in `core/tool_factory.py` — the personal
   module hints are gone but the prompt is still very long; trim further if you
   prefer.
3. **Run smoke test**:
   ```
   TELEGRAM_BOT_TOKEN=test:dummy OPENAI_API_KEY=sk-dummy \
     python -c "import bot, config; print('OK')"
   ```
4. **Drop the MANIFEST.md** before pushing if you don't want it in the public
   history (it's just for your review).

## Ready to publish?

```
cd telegram_chatbot_public
git init
git add .
git status                   # review what's about to be committed
git commit -m "Initial commit"
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main
```
