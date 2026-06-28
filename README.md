# queenbee-agent

**A self-extending LLM agent on Telegram, with a runtime, security-reviewed
tool factory.**

Most "Telegram + GPT" bots are a thin wrapper: a message goes in, an LLM
reply comes out. `queenbee-agent` is structurally different. The bot is a
full agent loop on top of any OpenAI-compatible model, with a tool registry
the model can call. The unusual part is that the **registry is not fixed**:
end users can describe a new capability in plain English (`>> describe what
the tool should do`) and the bot will write the Python code, run it through
a forbidden-pattern + LLM security review, hand it to the admin for
Approve/Reject, persist it as a first-class tool, and make it callable from
the very next turn.

## What it does

- **LLM agent loop on Telegram** — per-user conversation history, streaming
  responses, tool-calling, photo input, group-chat `@mention` handling.
- **OpenAI-compatible** — works with OpenAI, Anthropic Claude (via OpenAI
  compat), Google Gemini, Mistral, Groq, Together, OpenRouter, or a local
  Ollama instance. Pick the model with `LLM_MODEL` / `OPENAI_BASE_URL`.
- **Queen Bee tool factory** — runtime generation of new tools from a plain
  English description:
  1. Forbidden-intent regex check on the description (file listing, shell
     exec, secret extraction — bypassed for admin).
  2. The codex model writes an `async def run(context) -> str` Python module
     plus a JSON manifest with name / description / trigger keywords /
     required permission.
  3. The generated code is scanned for forbidden patterns
     (`subprocess`, `os.system`, `eval`, file deletion, …).
  4. For non-admin users, an async LLM **security review** runs and admin
     gets Approve / Reject buttons.
  5. Approved tools land in `tools/<name>.py` and are registered into the
     RBAC-aware tool registry, callable from the next turn.
- **Four ways to dispatch a tool**
  1. **LLM function calling** — the model decides during chat.
  2. **`/runtool <name> [args…]`** — explicit invocation.
  3. **Trigger-keyword auto-dispatch** — first word of a user message matches
     a tool's `trigger_keywords`.
  4. **Smart-intent dispatch** — if the message doesn't match a keyword but
     looks like a tool request, the bot proposes a tool with
     confirm/decline buttons.
- **Per-user RBAC** — every tool has a `permission` label (`public`,
  `finance`, `email`, `admin`, …). Users only see and run tools their role
  allows. Admin sees everything.
- **Scheduled jobs / monitors** — tools can run on a cron-like schedule
  (APScheduler) and push results back to the user.
- **Iteration loop** — `/qbtest`, `/qbfix <instruction>`, `/qbsave`,
  `/qbdiscard`, `/edittool <name> <instruction>` for refining a tool
  without leaving Telegram.
- **Operational hardening** — Azure Key Vault loader for secrets (AWS
  Secrets Manager / GCP Secret Manager recipes included), per-user rate
  limiting on tool generation, output sanitiser, group-chat allow-list.

## Ships with ~30 example tools

CoinGecko BTC price, weather, Estonia fuel / alcohol / cosmetics prices,
Amazon.de search, Google Shopping search, Booking.com / IKEA / JYSK
scrapers, Spotify lookup, Duolingo profile stats, snooker / FIFA / football
status, Euribor rate, Cozytouch / eWeLink / Huum smart-home control,
yt-dlp media downloader, TLDR summariser, and more — all in `tools/`,
each a self-contained `async def run(context) -> str` module.

---

## Prerequisites

Before you start, get these three things:

### 1. An LLM API key

The bot talks to any **OpenAI-compatible Chat Completions endpoint**, so you
can pick whichever provider you prefer:

| Provider | Get a key at | `OPENAI_BASE_URL` to set |
|---|---|---|
| **OpenAI** (default) | <https://platform.openai.com/api-keys> | leave unset (defaults to `https://api.openai.com/v1`) |
| **Anthropic Claude** | <https://console.anthropic.com/settings/keys> | `https://api.anthropic.com/v1/` (use [Anthropic's OpenAI-compatible endpoint](https://docs.anthropic.com/en/api/openai-sdk)) |
| **Google Gemini** | <https://aistudio.google.com/apikey> | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| **Mistral / Groq / Together / OpenRouter / local Ollama** | their respective dashboards | their OpenAI-compatible base URL |

Whatever provider you choose, put the key in `OPENAI_API_KEY` and (if needed)
set `OPENAI_BASE_URL` in `.env`. You can also override the default model with
`OPENAI_DEFAULT_MODEL` (e.g. `gpt-4o-mini`, `claude-3-5-sonnet-latest`,
`gemini-2.0-flash`, `llama-3.3-70b-versatile`, …).

### 2. A Telegram bot token + your Telegram user ID

- Get a bot token from [@BotFather](https://t.me/BotFather)
  (`/newbot` → follow prompts → copy the token).
- Get your own numeric Telegram ID from [@userinfobot](https://t.me/userinfobot).

### 3. A machine to run the bot on

The bot is a long-running Python process — it has to stay online to receive
Telegram updates. For local testing your laptop is fine; for production you
want a small always-on VM.

Any tiny Linux VM works (1 vCPU / 1 GB RAM is plenty). Cheapest options:

| Provider | Smallest tier | Approx. cost | Quick create |
|---|---|---|---|
| **Azure** | `Standard_B1s` Ubuntu 22.04 | ~$8/mo | `az vm create -g rg -n tgbot --image Ubuntu2204 --size Standard_B1s --admin-username azureuser --generate-ssh-keys` |
| **AWS EC2** | `t4g.nano` Ubuntu 22.04 (arm64) | ~$3/mo | `aws ec2 run-instances --image-id ami-0... --instance-type t4g.nano --key-name mykey` |
| **GCP Compute Engine** | `e2-micro` Ubuntu 22.04 | free tier eligible (US regions) | `gcloud compute instances create tgbot --machine-type=e2-micro --image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud` |
| **Hetzner / DigitalOcean / Vultr / Linode** | shared-CPU 1 GB | ~$4-5/mo | use their web console |
| **Raspberry Pi / home server** | any | $0 if you already own one | `ssh pi@<ip>` |

Once the VM is up:

```bash
ssh user@<vm-ip>
sudo apt update && sudo apt install -y python3.11 python3.11-venv git
git clone <this repo>
cd telegram_chatbot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env   # paste your tokens
python bot.py                       # smoke-test
```

To keep the bot running after you log out, create a systemd unit
(`/etc/systemd/system/telegram-bot.service`):

```ini
[Unit]
Description=Telegram Chatbot
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/telegram_chatbot
EnvironmentFile=/home/youruser/telegram_chatbot/.env
ExecStart=/home/youruser/telegram_chatbot/.venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-bot
journalctl -u telegram-bot -f      # follow logs
```

---

## Quick start

```bash
git clone <this repo>
cd telegram_chatbot
python -m venv .venv
source .venv/bin/activate          # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env
# Edit .env: set TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, QB_ADMIN_USER_ID
python bot.py
```

Get a bot token from [@BotFather](https://t.me/BotFather). Get your own
Telegram numeric user ID from [@userinfobot](https://t.me/userinfobot) and put
it in `QB_ADMIN_USER_ID`.

---

## Architecture

```
bot.py                       — entry point: registers handlers, starts polling
config.py                    — env-var + (optional) Azure Key Vault loading
tool_utils.py                — fetch_json/text/html/rendered helpers for tools
core/
  tool_factory.py            — Queen Bee: generate / edit / run tools
  tool_match.py              — keyword scoring for auto-dispatch
  agent_tools.py             — built-in LLM tools (list_tools, fetch_url, run_tool)
  conversation.py            — per-user history + model preference
  output_safety.py           — global output sanitiser
  tool_registry.py           — RBAC-aware tool registry
  user_roles.py              — persistent JSON user→roles store
handlers/
  core.py                    — /start, /help, /clear, /model, handle_message
  queen_bee.py               — >>, /tools, /runtool, /qbtest, /qbsave, ...
  admin_users.py             — /adduser, /deluser, /listusers, /setrole, ...
  tool_dispatch.py           — thinking phrases + translation helper
  strava_handlers.py         — /stravaconnect, /stravaauth, /stravahelp
  group.py                   — auto-leave non-allowed groups
  utils.py                   — shared helpers, ConversationManager
services/
  llm_service.py             — OpenAI API wrapper (streaming + tool-calling)
  model_router.py            — picks chat / codex / vision model per task
  strava_service.py          — Strava OAuth + REST wrapper (per-user tokens)
tools/                       — 30 pre-built tools + your own generated ones
                               (see tools/README.md for the list)
```

---

## Queen Bee Tool Factory

In a private chat with the bot:

```
>> fetch the current Bitcoin price from CoinGecko and format it nicely
```

The bot will:

1. Validate the request against a forbidden-intent regex list (file listing,
   shell exec, secret extraction, etc.) — admin users bypass this.
2. Ask the LLM to generate an `async def run(context: dict) -> str` Python module.
3. Scan the generated code for forbidden patterns (`subprocess`, `os.system`,
   `eval`, file deletion, etc.) — admin users bypass this.
4. For non-admin users: save the tool as `pending_review`, fire an async LLM
   security review, and present the admin with **Approve / Reject** buttons.
5. Once approved, the tool is callable via:
   - `/runtool <name>` — explicit
   - First-word keyword match (each tool has `trigger_keywords`)
   - The LLM itself, via the `run_tool` tool (RBAC-filtered per user)

### Managing tools

| Command | Purpose |
|---|---|
| `/tools` | List saved tools |
| `/toolhelp` | Comprehensive how-to + descriptions of every tool |
| `/toolhelp <name>` | Full detail for one tool |
| `/newtool` | Walkthrough on how to create a new tool |
| `/runtool <name>` | Run a tool explicitly |
| `/deltool <name>` (admin) | Delete a tool |
| `/edittool <name> <instruction>` | Ask the LLM to modify an existing tool |
| `/qbtest <description>` | Generate + run a tool without saving |
| `/qbsave` | Save the last test-generated tool |
| `/qbdiscard` | Throw it away |
| `/qbfix <bug description>` | Ask the LLM to fix the last test tool |
| `/qbdebug` | Show the last generated code |

---

## RBAC

Roles live in `config.ROLE_PERMISSIONS`:

```python
ROLE_PERMISSIONS = {
    "admin":  ["*"],          # everything
    "public": ["public"],     # default
}
```

Add your own (`finance`, `email`, `ops`, ...) and assign them to users via:

```
/adduser 123456789 finance,email @alice
/setrole my_tool finance       # only users with the `finance` role may run it
```

Built-in tools (`list_tools`, `fetch_url`, `run_tool`) are all `public`.
Generated tools default to `public`; the admin can promote them via `/setrole`.

---

## Adding your own domain tools

To wire a custom tool (e.g. a banking API) into the LLM orchestrator, register
it in `core/tool_registry.py` with a schema + handler + permission label:

```python
# In your module's init
import core.tool_registry as tr

tr.register(
    "my_tool",
    schema={"type": "function", "function": {"name": "my_tool", "description": "...", "parameters": {...}}},
    handler=async_handler,
    permission="finance",   # only users with a role mapping to 'finance' see it
)
```

The LLM will only see tools the calling user has permission for.

---

## Security

- Generated tools run in-process with `importlib`; they are NOT sandboxed.
  The defences are:
  - intent regex (pre-LLM) + code regex (post-LLM) for non-admin users
  - background LLM security review with admin approval
  - `pending_review` and `rejected` statuses block execution
  - 90-second hard timeout on every tool invocation
- The forbidden lists are in `core/tool_factory.py`
  (`_FORBIDDEN_INTENT_PATTERNS`, `_FORBIDDEN_PATTERNS`).
- Never set `QB_ADMIN_USER_ID` to a user you don't fully trust — admins bypass
  all checks and their generated code runs unverified.
- Telegram tokens, OpenAI keys, etc. are read from environment only. The
  `.gitignore` excludes `.env`, all `*token*.json`, all `*credentials*.json`,
  and all `*.pem`/`*.key` files.

---

## Optional Integrations

The repo ships with a handful of tools and handlers that talk to third-party
services. **All of them are off by default** — leave the matching env vars
blank and the integration is silently skipped. Set them to enable.

| Integration | What you get | Env vars |
| --- | --- | --- |
| Strava | `/stravaconnect`, `/stravaauth`, plus 4 LLM tools (athlete, activities, single activity zones, kudos) | `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` |
| Spotify | `spotify_lookup_tool` (track / artist / album lookup) | `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI` |
| Duolingo | `duolingo_stats_tool` (public profile lookup — no auth) | — |
| eWeLink | `ewelink_smart_home` tool (control Sonoff devices) | `EWELINK_EMAIL`, `EWELINK_PASSWORD`, `EWELINK_REGION` |
| Huum sauna | `huum_sauna` tool (status / heat-on / heat-off) | `HUUM_USERNAME`, `HUUM_PASSWORD` |
| Cozytouch heat pump | `cozytouch_heatpump` tool (temperature & mode) | `COZYTOUCH_USERNAME`, `COZYTOUCH_PASSWORD` |
| TMDB | `movie_search` tool (movie metadata) | `TMDB_API_KEY` |
| SerpAPI | `amazon_search`, `google_shopping_search`, `booking_com_search` | `SERPAPI_KEY` |
| OpenAI usage | `openai_api_usage_status` tool (cost report) | `OPENAI_ADMIN_API_KEY` |
| ElevenLabs | optional TTS in `media_creator` | `ELEVENLABS_API_KEY` |
| Yandex translate | optional translation helpers | `YANDEX_API_KEY` |

### Strava — connect a user

1. Go to <https://www.strava.com/settings/api> → **Create & Manage Your App**.
2. Set the **Authorization Callback Domain** to `localhost`.
3. Copy your Client ID + Client Secret into `.env`:
   ```env
   STRAVA_CLIENT_ID=12345
   STRAVA_CLIENT_SECRET=...
   ```
4. Restart the bot and run `/stravaconnect` in Telegram.
5. **Open the link in an external browser** (Telegram's in-app browser is
   blocked by Strava). Authorize, then copy the `code=` value from the
   resulting URL and run:
   ```
   /stravaauth <code>
   ```
6. The token is saved as `strava_token_<your_uid>.json` (location is
   overridable with `STRAVA_TOKEN_DIR`).

Once connected, ask the LLM things like *"how was my last ride?"* or
*"how much time did I spend in power zone 5 yesterday?"*. The LLM picks
the right Strava tool automatically.

To let users ask about each other (*"how was Alice's last ride?"*), populate
the `KNOWN_USERS` env var:

```env
KNOWN_USERS=alice:123456789;al:123456789;bob:987654321
```

### Spotify

1. Create an app at <https://developer.spotify.com/dashboard>.
2. Add a redirect URI (e.g. `http://localhost:8888/callback`).
3. Fill in:
   ```env
   SPOTIFY_CLIENT_ID=...
   SPOTIFY_CLIENT_SECRET=...
   SPOTIFY_REDIRECT_URI=http://localhost:8888/callback
   ```
4. Run the `spotify_lookup_tool` once — it will print an authorization URL
   and walk you through pasting the redirect URL back.

### eWeLink (Sonoff)

Use the same email/password you use for the eWeLink mobile app:

```env
EWELINK_EMAIL=you@example.com
EWELINK_PASSWORD=...
EWELINK_REGION=eu      # one of: us | eu | as | cn
# Optional: phone-based login instead of email
# EWELINK_PHONE=+3725551234
```

### Huum sauna

```env
HUUM_USERNAME=you@example.com
HUUM_PASSWORD=...
```

The tool queries Huum's REST API; same credentials as the Huum mobile app.

### Atlantic / Cozytouch heat pump

```env
COZYTOUCH_USERNAME=you@example.com
COZYTOUCH_PASSWORD=...
```

Uses the Atlantic / Cozytouch IO portal; same credentials as the mobile app.

### Duolingo

No auth — the tool uses Duolingo's public profile API:

```
duolingo <username>
```

### TMDB (movie_search tool)

Get a free v3 API key at <https://www.themoviedb.org/settings/api> and set:

```env
TMDB_API_KEY=...
```

YouTube trailer extraction uses `yt-dlp` if it's on `$PATH`; optionally drop a
`youtube_cookies.txt` file alongside `bot.py` to bypass bot-detection.

### OpenAI usage report

Generate a separate **Admin API key** at
<https://platform.openai.com/settings/organization/admin-keys> (org-scoped,
needed for the `/v1/organization/usage/*` endpoints) and set:

```env
OPENAI_ADMIN_API_KEY=sk-admin-...
```

---

## Optional: Secret manager (Azure / AWS / GCP)

For production you typically don't want plaintext API keys in `.env` on the
VM. Pick one of the cloud secret managers below, store every value listed in
`config._KV_SECRET_MAP` there, and grant your VM's identity read access.

### Azure Key Vault (built in)

Set `AZURE_KEYVAULT_URL=https://your-vault.vault.azure.net/` and the bot will
hydrate the env vars listed in `config._KV_SECRET_MAP` from your vault at
startup, using `DefaultAzureCredential` (Managed Identity in production,
`az login` locally). Leave it unset to use `.env` only.

Create a vault:

```bash
az group create -n tgbot-rg -l northeurope
az keyvault create -n tgbot-kv -g tgbot-rg -l northeurope
az keyvault secret set --vault-name tgbot-kv --name TELEGRAM-BOT-TOKEN --value '...'
az keyvault secret set --vault-name tgbot-kv --name OPENAI-API-KEY --value 'sk-...'
# Grant the VM's managed identity read access
az vm identity assign -g tgbot-rg -n tgbot
VM_PRINCIPAL=$(az vm show -g tgbot-rg -n tgbot --query identity.principalId -o tsv)
az role assignment create --role "Key Vault Secrets User" \
    --assignee "$VM_PRINCIPAL" \
    --scope $(az keyvault show -n tgbot-kv --query id -o tsv)
```

The bot expects vault secret names with dashes (e.g. `OPENAI-API-KEY`); they
are mapped to env vars with underscores at startup.

### AWS Secrets Manager (DIY)

The repo ships with the Azure loader, but the same pattern works on AWS — you
just need to plug a small loader into `config.py`.

Create the secret store:

```bash
# 1. Create one JSON secret holding every key/value
aws secretsmanager create-secret \
    --name tgbot/prod \
    --secret-string '{"TELEGRAM_BOT_TOKEN":"...","OPENAI_API_KEY":"sk-...","QB_ADMIN_USER_ID":"123456"}'

# 2. Create an IAM role that can read it and attach to your EC2 instance
ROLE_ARN=$(aws iam create-role --role-name tgbot-secrets-reader \
    --assume-role-policy-document file://trust-policy.json --query Role.Arn -o tsv)
aws iam put-role-policy --role-name tgbot-secrets-reader \
    --policy-name read-tgbot-secret \
    --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"secretsmanager:GetSecretValue","Resource":"arn:aws:secretsmanager:*:*:secret:tgbot/prod-*"}]}'
aws ec2 associate-iam-instance-profile --instance-id i-... \
    --iam-instance-profile Name=tgbot-secrets-reader
```

Then extend `config.py` near the Azure loader, e.g.:

```python
if aws_secret := os.getenv("AWS_SECRET_ID"):
    import boto3, json as _json
    blob = boto3.client("secretsmanager").get_secret_value(SecretId=aws_secret)
    for k, v in _json.loads(blob["SecretString"]).items():
        os.environ.setdefault(k, v)
```

### Google Cloud Secret Manager (DIY)

Same idea, GCP flavour:

```bash
# 1. Enable the API and create one secret per key/value
gcloud services enable secretmanager.googleapis.com
printf 'sk-...' | gcloud secrets create OPENAI_API_KEY --data-file=-
printf '...'    | gcloud secrets create TELEGRAM_BOT_TOKEN --data-file=-

# 2. Give the VM's service account read access
SA=$(gcloud compute instances describe tgbot --zone=europe-north1-a \
    --format='value(serviceAccounts[0].email)')
gcloud secrets add-iam-policy-binding OPENAI_API_KEY \
    --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding TELEGRAM_BOT_TOKEN \
    --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
```

Then extend `config.py`:

```python
if gcp_project := os.getenv("GCP_PROJECT_ID"):
    from google.cloud import secretmanager
    client = secretmanager.SecretManagerServiceClient()
    for key in ("OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", ...):
        name = f"projects/{gcp_project}/secrets/{key}/versions/latest"
        os.environ.setdefault(key, client.access_secret_version(name=name).payload.data.decode())
```

With any of the three, the VM never sees a plaintext `.env` and rotating a
key is just an update in the vault followed by a `systemctl restart
telegram-bot`.

---

## License

MIT — see [LICENSE](./LICENSE).
