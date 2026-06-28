"""
Queen Bee Tool Factory.

Generates, saves and executes sub-tools on demand.

Trigger in chat: start any message with ">>" followed by a description.
Example:
    >> create a tool that fetches the current Bitcoin price from CoinGecko

Saved tools live in the  tools/  directory as:
    tools/<name>.py    — executable Python module
    tools/<name>.json  — metadata manifest

Each tool exposes exactly one function:
    async def run(context: dict) -> str
"""

import importlib.util
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TOOLS_DIR = Path(__file__).parent.parent / "tools"


def _tools_dir() -> Path:
    TOOLS_DIR.mkdir(exist_ok=True)
    return TOOLS_DIR


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ToolMeta:
    name: str
    description: str
    requires_google_auth: bool
    trigger_keywords: list
    created_at: str
    version: int = 1
    status: str = "approved"   # approved | pending_review | rejected
    created_by: int = 0        # Telegram user ID of the creator
    help_example: str = ""     # Example usage shown in /toolhelp
    permission: str = "public" # RBAC permission required to run this tool


# ---------------------------------------------------------------------------
# Security: forbidden patterns in generated tool code
# ---------------------------------------------------------------------------

# Map category -> list of regex patterns.  Matched against generated code.
_FORBIDDEN_PATTERNS: dict[str, list[str]] = {
    "command execution": [
        r"\bsubprocess\b",
        r"\bos\.system\s*\(",
        r"\bos\.popen\s*\(",
        r"\bos\.exec[vle]",
        r"\bos\.spawn",
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"\b__import__\s*\(",
    ],
    "file listing": [
        r"\bos\.listdir\s*\(",
        r"\bos\.scandir\s*\(",
        r"\bos\.walk\s*\(",
        r"\bglob\.glob\s*\(",
        r"\.iterdir\s*\(",
        r"\.rglob\s*\(",
        r"\.glob\s*\(",
        r"\bfrom\s+glob\s+import",
        r"\bimport\s+glob\b",
    ],
    "file deletion": [
        r"\bos\.remove\s*\(",
        r"\bos\.unlink\s*\(",
        r"\bos\.rmdir\s*\(",
        r"\bshutil\.rmtree\s*\(",
        r"\.unlink\s*\(",
        r"\.rmdir\s*\(",
    ],
}

# ---------------------------------------------------------------------------
# Security: forbidden request intent patterns (checked on user description,
# before the LLM is ever called). Admin users bypass this check.
# ---------------------------------------------------------------------------

_FORBIDDEN_INTENT_PATTERNS: list[tuple[str, list[str]]] = [
    ("file/directory listing", [
        r"\blist\b.{0,40}\bfile",
        r"\blist\b.{0,40}\bdirector",
        r"\bshow\b.{0,40}\bfile",
        r"\bshow\b.{0,40}\bdirector",
        r"\bscan\b.{0,40}\bfile",
        r"\bscan\b.{0,40}\bdirector",
        r"\bfind\b.{0,40}\bfile",
        r"\bfilesystem\b",
        r"\bfile\s*system\b",
        r"\bls\b.{0,20}\b(/|dir|folder)",
        r"\btree\b.{0,30}\bdirector",
    ]),
    ("file deletion", [
        r"\bdelete\b.{0,40}\bfile",
        r"\bdelete\b.{0,40}\bfolder",
        r"\bremove\b.{0,40}\bfile",
        r"\bremove\b.{0,40}\bfolder",
        r"\bwipe\b.{0,40}\bfile",
        r"\bclean\b.{0,40}\bfile",
        r"\bclean\s*up\b.{0,40}\bfile",
        r"\brm\b.{0,20}\b(-rf?|file|dir)",
    ]),
    ("command/shell execution", [
        r"\brun\b.{0,30}\bcommand\b",
        r"\brun\b.{0,30}\bshell\b",
        r"\bexecute\b.{0,30}\bcommand\b",
        r"\bexecute\b.{0,30}\bscript\b",
        r"\bshell\b.{0,30}\bcommand\b",
        r"\bbash\b",
        r"\bpowershell\b",
        r"\bsubprocess\b",
        r"\bssh\b.{0,30}\bcommand\b",
    ]),
    ("secret/credential access", [
        r"\benv\b.{0,30}\bvariable",
        r"\benv\b.{0,30}\bsecret",
        r"\b\.env\b",
        r"\bapi.?key\b",
        r"\bpassword\b",
        r"\bcredential",
        r"\btoken\b.{0,30}\b(read|dump|show|list|print|get|steal|extract)",
        r"\bkey.?vault\b",
        r"\bsecret\b.{0,30}\b(read|dump|show|list|print|get|steal|extract)",
        r"\bprivate.?key\b",
    ]),
    ("VM/system information", [
        r"\bvm\b.{0,30}\b(info|status|access|control)",
        r"\bserver\b.{0,30}\b(file|directory|folder|info|status)",
        r"\bhost\b.{0,30}\b(file|directory|info|status)",
        r"\bsystem\b.{0,30}\b(info|status|access|file|directory)",
        r"\bos\b.{0,20}\b(info|version|file|directory|env)",
        r"\bprocess\b.{0,30}\b(list|running|kill|info)",
        r"\bnetwork\b.{0,30}\b(interface|config|ip\b|scan)",
    ]),
]


def check_qb_request_intent(description: str, user_id: int) -> str | None:
    """Return an error message if the request description contains forbidden intent,
    or None if it is safe to proceed. Admin users bypass this check entirely."""
    from config import QB_ADMIN_USER_ID
    if QB_ADMIN_USER_ID and user_id == QB_ADMIN_USER_ID:
        return None
    import re as _re
    text = description.lower()
    for category, patterns in _FORBIDDEN_INTENT_PATTERNS:
        for pat in patterns:
            if _re.search(pat, text):
                return (
                    f"🚫 Request blocked: tools are not allowed to perform "
                    f"*{category}* on this server.\n"
                    "Try asking for something that uses public APIs or your own data."
                )
    return None


_SECURITY_RULES = """\
8. SECURITY — you MUST follow these rules for ALL generated code:
   - NEVER use subprocess, os.system, os.popen, os.exec*, os.spawn, eval(), or exec().
   - NEVER list files on the host system (os.listdir, os.scandir, os.walk, glob, .iterdir, .glob, .rglob).
   - NEVER delete or modify files (os.remove, os.unlink, os.rmdir, shutil.rmtree, Path.unlink, Path.rmdir).
   - NEVER write to files outside the run() function's own return value.
   Any violation will cause the tool to be rejected.
"""


def _validate_tool_code(code: str, user_id: int) -> str | None:
    """Return an error message if code contains forbidden patterns, or None if safe.
    Admin users bypass validation entirely."""
    from config import QB_ADMIN_USER_ID
    if QB_ADMIN_USER_ID and user_id == QB_ADMIN_USER_ID:
        return None
    import re as _re
    for category, patterns in _FORBIDDEN_PATTERNS.items():
        for pat in patterns:
            if _re.search(pat, code):
                return (
                    f"❌ Generated code was rejected for security reasons "
                    f"(forbidden operation: {category}).\n"
                    "Rephrase your request to avoid file system access or command execution."
                )
    return None


# ---------------------------------------------------------------------------
# LLM-based security review (async, fires after tool is saved)
# ---------------------------------------------------------------------------

_SECURITY_REVIEW_PROMPT = """\
You are a security expert reviewing Python code that will run on a server.

Analyze the code for genuinely dangerous behaviour only:
1. Data exfiltration — reading env vars / files and sending them to external endpoints
2. Obfuscated execution — base64/hex decode + eval/exec of dynamic strings
3. Credential harvesting — accessing os.environ for secrets, tokens, passwords
4. Destructive operations — overwriting or deleting files on the host
5. Resource abuse — crypto mining, fork bombs, infinite loops
6. Backdoors — reverse shells, spawning persistent listeners

Normal patterns that are fine: HTTP API calls to public services, returning text,
parsing data, reading config values that the tool legitimately needs.

Return ONLY a JSON object with no extra text:
{"safe": true, "issues": []}
or
{"safe": false, "issues": ["short description of issue 1", ...]}
"""


async def review_tool_security(name: str, code: str, created_by: int, bot) -> None:
    """LLM security review — runs as a background asyncio task after tool is saved.

    If the review passes: marks the tool as 'approved' and notifies the creator.
    If issues are found: keeps the tool as 'pending_review' and notifies the admin.
    """
    import json as _json
    from config import QB_ADMIN_USER_ID
    from services.model_router import ModelRouter
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    router = ModelRouter()

    manifest_path = _tools_dir() / f"{name}.json"

    def _update_status(new_status: str) -> None:
        try:
            data = _json.loads(manifest_path.read_text(encoding="utf-8"))
            data["status"] = new_status
            manifest_path.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("Could not update status for tool '%s': %s", name, exc)

    try:
        raw = await router.create_json(
            router.codex_model(),
            system_prompt=_SECURITY_REVIEW_PROMPT,
            user_prompt=f"Review this tool code:\n\n```python\n{code}\n```",
        )
        result = _json.loads(raw or "{}")
    except Exception as e:
        logger.warning("Security review LLM call failed for tool '%s': %s — auto-approving", name, e)
        _update_status("approved")
        try:
            await bot.send_message(chat_id=created_by,
                text=f"✅ Tool `{name}` passed security review and is ready to use.",
                parse_mode="Markdown")
        except Exception:
            pass
        return

    if result.get("safe", True):
        logger.info("Security review passed for tool '%s'", name)
        _update_status("approved")
        try:
            await bot.send_message(chat_id=created_by,
                text=f"✅ Tool `{name}` passed security review and is ready.\nRun it with: `/runtool {name}`",
                parse_mode="Markdown")
        except Exception:
            pass
        return

    # Issues found — keep pending_review, alert admin
    issues = result.get("issues", [])
    issues_text = "\n".join(f"• {i}" for i in issues) or "• Unspecified security concern"
    logger.warning("Security review flagged tool '%s': %s", name, issues)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"qb_approve:{name}:{created_by}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"qb_reject:{name}:{created_by}"),
    ]])
    alert = (
        f"🔍 *Security Review Alert*\n\n"
        f"Tool `{name}` (created by user `{created_by}`) was flagged:\n\n"
        f"{issues_text}\n\n"
        f"The tool is *blocked* until you approve or reject it."
    )
    if QB_ADMIN_USER_ID:
        try:
            await bot.send_message(chat_id=QB_ADMIN_USER_ID, text=alert,
                parse_mode="Markdown", reply_markup=keyboard)
        except Exception as e:
            logger.error("Could not send security alert to admin: %s", e)
    # Also notify the creator that their tool is under manual review
    try:
        await bot.send_message(chat_id=created_by,
            text=f"⏳ Tool `{name}` has been flagged for manual admin review. You'll be notified once it's approved.",
            parse_mode="Markdown")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# LLM system prompt for code generation
# ---------------------------------------------------------------------------

_QB_SYSTEM_PROMPT = """\
You are an expert Python tool code generator for a Telegram bot assistant.

The bot project has these modules you can import INSIDE the run() function:
  config         — LLM_MODEL, plus any project-specific env vars exposed there
  llm_service    — `from services.llm_service import get_llm_response`
                   then `await get_llm_response(history, model=None)` where history
                   is a list of {"role": "system|user|assistant", "content": "..."}.
                   Use this for summarisation, translation, classification, etc.
  tool_utils    — PREFERRED shared utility module. Import inside run():
                   from tool_utils import fetch_json, fetch_text, fetch_html, fetch_rendered, parse_args
                   • fetch_json(url, params=, headers=, timeout=15, method="GET", json_body=) → (data, status)
                   • fetch_text(url, params=, headers=, timeout=15) → (text, status)
                   • fetch_html(url, params=, headers=, timeout=15) → (BeautifulSoup, status)
                   • fetch_rendered(url, selector=, wait_for=, timeout=20, return_html=False, stealth=False) → dict
                       Headless Chromium (Playwright) — use for SPAs / JS-rendered pages /
                       Cloudflare-protected sites where fetch_html returns an empty shell.
                       Returns {status, url, title, text, text_truncated, stealth[, html]}.
                       Pass stealth=True for sites that block bots.
                   • parse_args(context) → list[str]
                   All fetch functions return (None, 0) on network error.
                   USE tool_utils whenever possible instead of raw aiohttp boilerplate.
  HTTP/web       — aiohttp and httpx are also available for advanced cases (custom sessions, POST bodies, etc.)
  HTML parsing   — beautifulsoup4 (bs4) is INSTALLED. fetch_html() returns a BeautifulSoup object directly.
  Standard lib   — asyncio, json, datetime, re, os, pathlib, etc.

Generate a Python tool based on the user's description.
Return ONLY a valid JSON object (no markdown fences, no extra text) with keys:
{
  "name": "snake_case_name_max_40_chars",
  "description": "One-line description of what this tool does",
  "requires_google_auth": true or false,
  "trigger_keywords": ["word1", "word2"],
  "code": "<complete Python module source as a string>"
}

Rules for the generated code string:
1. Must contain exactly: async def run(context: dict) -> str
2. context["user_id"]     — int Telegram user ID
   context["args"]        — list[str] extra arguments the user passed
   context["google_auth"] — bool, True if gmail_token.json exists
3. Return a human-readable string (Telegram MarkdownV1 is OK).
4. Handle ALL exceptions internally; never raise — return error strings.
5. All imports must be inside the function body.
6. Do NOT use markdown fences inside the code string.
7. ALWAYS generate working code. NEVER refuse or say something is not possible.
   If the user asks for a crypto price — use aiohttp to call CoinGecko. Just do it.
   If the user asks for weather — use aiohttp to call a weather API. Just do it.
   You are generating code that runs in a real Python environment with internet access.
8. SCRAPING SPA WEBSITES: Many modern e-commerce and retail websites (Douglas, Notino,
   Kaubamaja, ilu.ee, 1a.ee, etc.) are Single Page Applications (SPAs) — their product
   data is loaded by JavaScript AFTER page load, so aiohttp/BeautifulSoup will get an
   empty shell. Cloudflare-protected sites will return HTTP 403.
   Two options when the target website is an SPA or blocks bots:
   (a) PLAYWRIGHT (preferred when the user explicitly asks for scraping a specific
       site, needs to apply on-page filters, paginate, or wants real DOM data):
         from tool_utils import fetch_rendered
         result = await fetch_rendered(url, wait_for=".product-card", stealth=True,
                                       return_html=True, timeout=25)
         if result.get("error"):
             return f"Fetch failed: {result['error']}"
         from bs4 import BeautifulSoup
         soup = BeautifulSoup(result.get("html", ""), "html.parser")
         # ...parse products, follow ?page=N links by calling fetch_rendered again...
       Use stealth=True for Cloudflare/PerimeterX/DataDome. ALWAYS pass wait_for to
       a real content selector and use return_html=True if you need to parse DOM.
       Deduplicate via a set of product URLs. Wrap each page fetch in try/except.
   (b) SERPAPI Google Shopping (use when the user asks for a generic product/price
       lookup across stores, not a specific site):
         api_key = os.getenv("SERPAPI_KEY", "")
         params = {"engine": "google_shopping", "q": query, "gl": "ee", "hl": "en",
                   "api_key": api_key, "num": 10}
         data, status = await fetch_json("https://serpapi.com/search.json", params=params)
         results = data.get("shopping_results") or data.get("inline_shopping_results") or []
       Each result has: title, price, extracted_price, source (store name), link.
       Set "gl" to the target country code (ee=Estonia, lv=Latvia, de=Germany, etc.).
   - For non-shopping SerpAPI searches:
       params = {"engine": "google", "q": query, "gl": "ee", "api_key": api_key}
   - SERPAPI_KEY is available via os.getenv("SERPAPI_KEY"). Always check it is set.
   - Direct aiohttp+BeautifulSoup is fine for public APIs, simple server-rendered
     HTML, and JSON REST endpoints.
9. SCRAPING HTML TABLES (price lists, rate tables, schedules, leaderboards):
   The single most common bug is mis-treating the row that contains the row label.
   Many real-world tables put the row label in a `<th>` cell, not a `<td>`. Use:
       for row in table.find_all('tr'):
           cells = row.find_all(['td', 'th'])      # accept both
           if len(cells) < 2:
               continue
           # Skip the COLUMN-HEADER row (all <th>, no <td> data cells)
           if all(c.name == 'th' for c in cells):
               continue
           label = cells[0].get_text(' ', strip=True)   # first cell is the label,
                                                         # regardless of its tag
           value = cells[1].get_text(' ', strip=True)
   NEVER do `if cells[0].name == 'th': continue` — that drops every data row
   on sites like euribor-rates.eu, ECB, central-bank pages, sports leaderboards.
   When the user wants a "change" / "delta" / "vs yesterday" value and the page
   only shows historical columns (today, yesterday, day-before, …), COMPUTE it as
   `today_value - prev_value`. Do NOT read the next column as if it were a delta —
   it is another absolute value.
   When picking which `<table>` to parse, prefer the one whose `get_text()` contains
   the topic keywords AND whose first row has 2+ cells.
10. WHEN A SCRAPE RETURNS NO ROWS, log/print:
    - HTTP status
    - len(html)
    - number of `<table>` tags found
    - first row's `[(cell.name, cell.get_text(strip=True)) for cell in row.find_all(['td','th'])]`
    so the next fix iteration has ground truth instead of guessing.
"""

# Security rules appended dynamically for non-admin users (see _build_system_prompt)
_QB_SYSTEM_PROMPT_ADMIN = _QB_SYSTEM_PROMPT  # admin: no extra restrictions


def _build_system_prompt(user_id: int) -> str:
    from config import QB_ADMIN_USER_ID
    if QB_ADMIN_USER_ID and user_id == QB_ADMIN_USER_ID:
        return _QB_SYSTEM_PROMPT_ADMIN
    return _QB_SYSTEM_PROMPT + _SECURITY_RULES


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

def list_tools() -> list[ToolMeta]:
    tools = []
    for f in _tools_dir().glob("*.json"):
        # Skip non-manifest JSON files (e.g. regcar_cache.json)
        if not (f.with_suffix(".py")).exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            tools.append(ToolMeta(**{k: data[k] for k in ToolMeta.__dataclass_fields__ if k in data}))
        except Exception as e:
            logger.warning("Could not load tool manifest %s: %s", f, e)
    return sorted(tools, key=lambda a: a.name)


def get_tool_code(name: str) -> str | None:
    path = _tools_dir() / f"{name}.py"
    return path.read_text(encoding="utf-8") if path.exists() else None


def save_tool(meta: ToolMeta, code: str) -> None:
    d = _tools_dir()
    (d / f"{meta.name}.py").write_text(code, encoding="utf-8")
    (d / f"{meta.name}.json").write_text(
        json.dumps(asdict(meta), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("Tool '%s' saved to %s/", meta.name, d)


def save_tool_meta(meta: ToolMeta) -> None:
    """Persist only the JSON manifest for an existing tool (no code change)."""
    d = _tools_dir()
    js = d / f"{meta.name}.json"
    if not js.exists():
        raise FileNotFoundError(f"Manifest not found for {meta.name}")
    js.write_text(
        json.dumps(asdict(meta), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("Tool meta '%s' updated", meta.name)


def delete_tool(name: str) -> bool:
    d = _tools_dir()
    py = d / f"{name}.py"
    js = d / f"{name}.json"
    if not py.exists():
        return False
    py.unlink(missing_ok=True)
    js.unlink(missing_ok=True)
    return True


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

async def generate_tool(description: str, user_id: int = 0) -> tuple[ToolMeta, str] | tuple[None, str]:
    """
    Ask the LLM to generate a tool for the given description.
    Returns (ToolMeta, code_str) on success, or (None, error_message) on failure.
    """
    from services.model_router import ModelRouter

    router = ModelRouter()

    try:
        raw = await router.create_json(
            router.codex_model(),
            system_prompt=_build_system_prompt(user_id),
            user_prompt=f"Create a tool for: {description}",
        )
        data = json.loads(raw or "{}")
    except Exception as e:
        logger.exception("generate_tool: LLM call failed")
        return None, f"LLM error generating tool: {type(e).__name__}: {e}"

    required = {"name", "description", "requires_google_auth", "trigger_keywords", "code"}
    missing = required - set(data.keys() if isinstance(data, dict) else [])
    if missing:
        snippet = (raw or "")[:300]
        logger.warning("generate_tool: missing fields %s. Raw snippet: %s", missing, snippet)
        return None, f"LLM response missing fields: {missing}\nRaw: {snippet}"

    if "async def run(context" not in data["code"]:
        return None, "Generated code is missing the required `async def run(context: dict) -> str` function."

    security_error = _validate_tool_code(data["code"], user_id)
    if security_error:
        return None, security_error

    # Sanitize name to safe snake_case
    name = re.sub(r"[^a-z0-9_]", "_", data["name"].lower().strip())[:50].strip("_")
    if not name:
        name = "unnamed_tool"

    from config import QB_ADMIN_USER_ID
    meta = ToolMeta(
        name=name,
        description=str(data["description"])[:200],
        requires_google_auth=bool(data["requires_google_auth"]),
        trigger_keywords=[str(k) for k in data.get("trigger_keywords", [])],
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by=user_id,
        # Non-admin tools start as pending_review; admin tools are auto-approved
        status="approved" if (QB_ADMIN_USER_ID and user_id == QB_ADMIN_USER_ID) else "pending_review",
    )
    return meta, data["code"]


_QB_EDIT_PROMPT = """\
You are an expert Python tool code editor for a Telegram bot assistant.

You will receive the CURRENT code of an existing tool and an EDIT INSTRUCTION from the user.
Apply the requested changes to the code and return the COMPLETE updated tool.

Return ONLY a valid JSON object (no markdown fences, no extra text) with keys:
{
  "description": "Updated one-line description (keep original if not changed)",
  "trigger_keywords": ["word1", "word2"],
  "code": "<complete updated Python module source as a string>"
}

Rules:
1. Return the FULL updated code, not just the diff.
2. Must keep: async def run(context: dict) -> str
3. Handle ALL exceptions internally; never raise — return error strings.
4. All imports must be inside the function body.
5. ALWAYS generate working code. NEVER refuse.
6. PREFER using `from tool_utils import fetch_json, fetch_text, fetch_html, parse_args`
   for HTTP fetching instead of raw aiohttp boilerplate.
7. The user prompt may include OBSERVED OUTPUT (what the current tool actually
   returned when run) and OBSERVED PAGE / OBSERVED TABLE PREVIEW sections (a real
   sample of what the target URL serves right now). TREAT THESE AS GROUND TRUTH.
   They are more reliable than the user's description. Adjust the code to match
   the actual structure shown there.
8. HTML TABLE PITFALL — do NOT skip rows whose first cell is `<th>`. Many sites
   put the row label in `<th>` and the values in `<td>`. Use:
       cells = row.find_all(['td', 'th'])
       if all(c.name == 'th' for c in cells):   # skip ONLY pure-header rows
           continue
       label = cells[0].get_text(' ', strip=True)
   For "change vs yesterday" / "delta" requests, COMPUTE today - prev rather than
   reading the next column as if it already were a delta.
9. If the user asks you to switch to a specific endpoint, USE that exact URL
   instead of inventing a new one.
"""


async def edit_tool(
    name: str,
    instruction: str,
    user_id: int = 0,
    extra_context: str = "",
    override_code: str | None = None,
) -> tuple[ToolMeta, str] | tuple[None, str]:
    """
    Load an existing tool, send its code + edit instruction to the LLM,
    and return (updated ToolMeta, updated code) or (None, error).

    `extra_context` is appended verbatim to the user prompt (used for ground-truth
    OBSERVED OUTPUT / OBSERVED PAGE blocks produced by `gather_fix_context`).
    `override_code` lets the caller treat a string as the current code without
    touching disk (used for auto-retry: pass the first attempt's code as the
    baseline for a second LLM round).
    """
    from config import QB_ADMIN_USER_ID
    from services.model_router import ModelRouter

    router = ModelRouter()

    if override_code is not None:
        current_code = override_code
    else:
        current_code = get_tool_code(name)
        if current_code is None:
            return None, f"Tool `{name}` not found."

    manifest_path = _tools_dir() / f"{name}.json"
    if not manifest_path.exists():
        return None, f"Tool manifest for `{name}` not found."
    old_meta_data = json.loads(manifest_path.read_text(encoding="utf-8"))

    system_prompt = _QB_EDIT_PROMPT
    if not (QB_ADMIN_USER_ID and user_id == QB_ADMIN_USER_ID):
        system_prompt += _SECURITY_RULES

    user_prompt = (
        f"Tool name: {name}\n"
        f"Current description: {old_meta_data.get('description', '')}\n"
        f"Current keywords: {old_meta_data.get('trigger_keywords', [])}\n\n"
        f"Current code:\n```python\n{current_code}\n```\n\n"
        f"Edit instruction: {instruction}"
    )
    if extra_context:
        user_prompt += "\n\n" + extra_context

    try:
        raw = await router.create_json(
            router.codex_model(),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        data = json.loads(raw or "{}")
    except Exception as e:
        return None, f"LLM error editing tool: {e}"

    code = data.get("code", "")
    if not code or "async def run(context" not in code:
        return None, "LLM returned invalid code (missing `async def run`)."

    security_error = _validate_tool_code(code, user_id)
    if security_error:
        return None, security_error

    meta = ToolMeta(
        name=name,
        description=str(data.get("description", old_meta_data.get("description", "")))[:200],
        requires_google_auth=old_meta_data.get("requires_google_auth", False),
        trigger_keywords=[str(k) for k in data.get("trigger_keywords", old_meta_data.get("trigger_keywords", []))],
        created_at=old_meta_data.get("created_at", datetime.now(timezone.utc).isoformat()),
        version=old_meta_data.get("version", 1) + 1,
        created_by=old_meta_data.get("created_by", 0),
        status="approved" if (QB_ADMIN_USER_ID and user_id == QB_ADMIN_USER_ID) else "pending_review",
        help_example=old_meta_data.get("help_example", ""),
    )
    return meta, code


_URL_RE = re.compile(r"https?://[^\s)>\]\"']+", re.IGNORECASE)


def _looks_like_failure(output: str) -> bool:
    """Heuristic: did the tool fail / return an unhelpful placeholder?"""
    if not output:
        return True
    s = output.strip()
    if not s:
        return True
    low = s.lower()
    failure_markers = (
        "❌", "⚠️",
        "не удалось", "ошибка", "не найдено", "попробуйте позже",
        "failed", "error fetching", "could not fetch", "no data",
        "traceback", "exception", "http 4", "http 5",
        "access denied", "permission denied", "unauthorized",
        "forbidden", "not authorized", "is not configured",
    )
    return any(m in low for m in failure_markers)


async def gather_fix_context(
    current_code: str,
    instruction: str,
    test_context: dict,
    *,
    timeout_sec: int = 20,
    max_html_chars: int = 3500,
) -> dict:
    """Collect ground-truth signals to attach to a /qbfix LLM prompt.

    Runs the current tool once and (best-effort) fetches any URL mentioned in
    the instruction or in the source code so the model can see what's actually
    on the page. All operations are best-effort; any failures are swallowed.

    Returns a dict with keys: observed_output, fetched_urls (list of dicts with
    url, status, length, content_snippet, table_preview).
    """
    observed_output = ""
    try:
        observed_output = await test_tool_code(current_code, dict(test_context), timeout_sec=timeout_sec)
    except Exception as e:
        observed_output = f"<test_tool_code crashed: {type(e).__name__}: {e}>"

    urls: list[str] = []
    seen: set[str] = set()
    for src in (instruction or "", current_code or ""):
        for m in _URL_RE.findall(src):
            cleaned = m.rstrip(".,;:'\"")
            if cleaned not in seen:
                seen.add(cleaned)
                urls.append(cleaned)
            if len(urls) >= 3:
                break
        if len(urls) >= 3:
            break

    fetched: list[dict] = []
    if urls:
        try:
            from tool_utils import fetch_text
            from bs4 import BeautifulSoup
        except Exception:
            fetch_text = None
            BeautifulSoup = None
        for url in urls:
            entry = {"url": url, "status": 0, "length": 0, "content_snippet": "", "table_preview": ""}
            if fetch_text is None:
                fetched.append(entry)
                continue
            try:
                text, status = await fetch_text(url, timeout=15)
                entry["status"] = int(status or 0)
                if text:
                    entry["length"] = len(text)
                    entry["content_snippet"] = text[:max_html_chars]
                    if BeautifulSoup is not None and "<table" in text.lower():
                        try:
                            soup = BeautifulSoup(text, "html.parser")
                            tables = soup.find_all("table")
                            preview_lines = [f"tables_found={len(tables)}"]
                            for ti, table in enumerate(tables[:2]):
                                rows = table.find_all("tr")
                                preview_lines.append(f"--- table[{ti}] rows={len(rows)} ---")
                                for ri, row in enumerate(rows[:6]):
                                    cells = row.find_all(["td", "th"])
                                    summary = [(c.name, c.get_text(" ", strip=True)[:60]) for c in cells]
                                    preview_lines.append(f"row[{ri}]={summary}")
                            entry["table_preview"] = "\n".join(preview_lines)[:2000]
                        except Exception:
                            pass
            except Exception as e:
                entry["content_snippet"] = f"<fetch_text raised: {type(e).__name__}: {e}>"
            fetched.append(entry)

    return {"observed_output": observed_output, "fetched_urls": fetched}


def _format_fix_context(ctx: dict) -> str:
    """Render the ground-truth dict from gather_fix_context as prompt text."""
    parts: list[str] = []
    obs = (ctx.get("observed_output") or "").strip()
    if obs:
        parts.append("OBSERVED OUTPUT (current tool run, no args):\n" + obs[:1500])
    for entry in ctx.get("fetched_urls") or []:
        url = entry.get("url", "")
        st = entry.get("status", 0)
        ln = entry.get("length", 0)
        snippet = (entry.get("content_snippet") or "").strip()
        tpv = (entry.get("table_preview") or "").strip()
        block = [f"\nOBSERVED PAGE: {url}", f"HTTP={st} length={ln}"]
        if tpv:
            block.append("OBSERVED TABLE PREVIEW:\n" + tpv)
        if snippet:
            block.append("OBSERVED HTML/TEXT SNIPPET (truncated):\n" + snippet[:2500])
        parts.append("\n".join(block))
    return "\n\n".join(parts).strip()


async def fix_pending_code(
    name: str,
    description: str,
    trigger_keywords: list[str],
    current_code: str,
    instruction: str,
    user_id: int = 0,
    extra_context: str = "",
) -> tuple[str, list[str], str] | tuple[None, None, str]:
    """Ask the LLM to fix the given pending-tool code per the instruction.

    `extra_context` is appended to the user prompt verbatim (e.g. ground-truth
    OBSERVED OUTPUT / OBSERVED PAGE produced by `gather_fix_context`).

    Returns (new_code, new_trigger_keywords, new_description) on success or
    (None, None, error_message) on failure. Nothing is written to disk.
    """
    from config import QB_ADMIN_USER_ID
    from services.model_router import ModelRouter

    router = ModelRouter()

    system_prompt = _QB_EDIT_PROMPT
    if not (QB_ADMIN_USER_ID and user_id == QB_ADMIN_USER_ID):
        system_prompt += _SECURITY_RULES

    user_prompt = (
        f"Tool name: {name}\n"
        f"Current description: {description}\n"
        f"Current keywords: {trigger_keywords}\n\n"
        f"Current code:\n```python\n{current_code}\n```\n\n"
        f"Bug report / fix instruction: {instruction}"
    )
    if extra_context:
        user_prompt += "\n\n" + extra_context

    try:
        raw = await router.create_json(
            router.codex_model(),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        data = json.loads(raw or "{}")
    except Exception as e:
        return None, None, f"LLM error fixing tool: {type(e).__name__}: {e}"

    code = data.get("code", "")
    if not code or "async def run(context" not in code:
        return None, None, "LLM returned invalid code (missing `async def run`)."

    security_error = _validate_tool_code(code, user_id)
    if security_error:
        return None, None, security_error

    new_keywords = [str(k) for k in data.get("trigger_keywords", trigger_keywords)]
    new_description = str(data.get("description", description))[:200]
    return code, new_keywords, new_description


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

async def run_tool(name: str, context: dict) -> str:
    """
    Dynamically load and execute a saved tool.
    Returns the tool's result string (never raises).
    """
    py_path = _tools_dir() / f"{name}.py"
    if not py_path.exists():
        return f"❌ Tool `{name}` not found. Use /tools to list available tools."

    # ---- RBAC: check manifest 'permission' against caller's roles --------
    # Defense-in-depth: the LLM tool registry already filters by permission
    # when building the per-user schema, but the keyword-dispatch path goes
    # straight to run_tool(). Re-checking here guarantees that ALL execution
    # paths (keyword dispatch, /runtool, LLM tool call, smart intent router,
    # scheduled jobs) enforce the same role check.
    manifest_path = _tools_dir() / f"{name}.json"
    required_perm = "public"
    manifest_status = "approved"
    if manifest_path.exists():
        try:
            _manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            required_perm = (_manifest.get("permission") or "public").strip().lower()
            manifest_status = _manifest.get("status", "approved")
        except Exception:
            pass

    caller_uid = int(context.get("user_id") or 0)
    # Scheduled jobs / system calls run with user_id=0 and have implicit access
    # to public-only tools. Anything more privileged requires an explicit user.
    if caller_uid:
        from core.tool_registry import _user_roles, _role_has_permission
        from config import QB_ADMIN_USER_ID
        is_admin = bool(QB_ADMIN_USER_ID) and caller_uid == QB_ADMIN_USER_ID
        if not is_admin:
            roles = _user_roles(caller_uid)
            if not _role_has_permission(roles, required_perm):
                logger.warning(
                    "RBAC denial: user %s (roles=%s) tried to run tool '%s' "
                    "(permission=%s)", caller_uid, roles, name, required_perm,
                )
                return (
                    f"🚫 You don't have permission to run `{name}` "
                    f"(requires role: *{required_perm}*).\n"
                    f"Ask the admin to grant you this role."
                )
    elif required_perm not in ("public", ""):
        # No caller identity available and the tool is not public.
        logger.warning(
            "RBAC denial: unauthenticated call to tool '%s' (permission=%s)",
            name, required_perm,
        )
        return f"🚫 Tool `{name}` requires role *{required_perm}*; caller identity not available."

    # Check manifest status before executing
    if manifest_status == "pending_review":
        return f"⏳ Tool `{name}` is pending security review by admin. You'll be notified when it's approved."
    elif manifest_status == "rejected":
        return f"🚫 Tool `{name}` was rejected by admin for security reasons and cannot be run."

    # Evict any previous cached version so edits take effect
    mod_key = f"_qb_tool_{name}"
    sys.modules.pop(mod_key, None)

    try:
        spec = importlib.util.spec_from_file_location(mod_key, py_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as e:
        return f"❌ Failed to load tool `{name}`: {e}"

    run_fn = getattr(module, "run", None)
    if not callable(run_fn):
        return f"❌ Tool `{name}` has no callable `run()` function."

    # Outer hard timeout so a misbehaving tool (e.g. stuck network I/O that
    # doesn't honour its own per-request timeout) cannot wedge the bot forever.
    TOOL_HARD_TIMEOUT_SEC = 90
    import asyncio as _asyncio
    try:
        result = await _asyncio.wait_for(run_fn(context), timeout=TOOL_HARD_TIMEOUT_SEC)
        return str(result)
    except _asyncio.TimeoutError:
        logger.error("Tool '%s' timed out after %ss", name, TOOL_HARD_TIMEOUT_SEC)
        return f"⏱️ Tool `{name}` timed out after {TOOL_HARD_TIMEOUT_SEC}s and was cancelled."
    except Exception as e:
        logger.error("Tool '%s' raised an exception: %s", name, e)
        return f"❌ Tool `{name}` failed during execution: {e}"


async def test_tool_code(code: str, context: dict, timeout_sec: int = 30) -> str:
    """Execute tool code in-memory (without saving to disk) and return the result.

    Used for test-before-save workflow. Runs the code string directly,
    extracts the run() function, and calls it with the given context.
    Returns the tool output or an error string. Never raises.
    """
    import asyncio as _asyncio
    import types

    mod = types.ModuleType("_qb_test_tool")
    try:
        exec(compile(code, "<qb_test_tool>", "exec"), mod.__dict__)
    except Exception as e:
        return f"❌ Code failed to compile/load: {e}"

    run_fn = getattr(mod, "run", None)
    if not callable(run_fn):
        return "❌ Generated code has no callable `run()` function."

    try:
        result = await _asyncio.wait_for(run_fn(context), timeout=timeout_sec)
        return str(result)
    except _asyncio.TimeoutError:
        return f"❌ Tool test timed out after {timeout_sec}s."
    except Exception as e:
        return f"❌ Tool raised an exception during test: {e}"
