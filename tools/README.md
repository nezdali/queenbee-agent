# `tools/`

This directory holds saved Queen Bee tools, plus a curated set of
pre-built ones that ship with the repo so you have working examples
out of the box.

Each tool is a pair of files:

```
tools/<name>.py     # async def run(context: dict) -> str
tools/<name>.json   # manifest: description, trigger_keywords, permission, status
```

## Pre-shipped tools

### Pure scrapers / public APIs (no auth required)
| Tool | What it does |
| --- | --- |
| `amazon_search` | Amazon product search (uses SerpAPI) |
| `anekdot_ru_fetcher` | Fetches a fresh joke from anekdot.ru |
| `booking_com_search` | Hotel search on booking.com |
| `btc_price_coingecko` | Bitcoin spot price via CoinGecko |
| `estonia_cosmetics_lookup` | Compares cosmetics prices across Estonian retailers |
| `estonia_fuel_prices` | Latest petrol/diesel prices in Estonia |
| `euribor_rate_checker` | Euribor reference rates |
| `fifa_worldcup_status` | World Cup standings / fixtures |
| `google_shopping_search` | Google Shopping (SerpAPI) |
| `home4you_scraper` | Estonian furniture retailer Home4You |
| `humalakoda_prices` | Humalakoda product price lookup |
| `ikea_estonia_product_search` | IKEA Estonia product search |
| `jysk_estonia_products` | JYSK Estonia product search |
| `kolga_laht_tide_tool` | Kolga Bay (Estonia) tide chart |
| `riga_alcohol_prices` | Latvian alcohol shop prices |
| `snooker_championship_status` | Snooker tournament status |
| `stavanger_alcohol_prices` | Vinmonopolet Stavanger prices |
| `stavanger_fuel_prices` | Fuel prices around Stavanger |
| `stopwatch_timer_manager` | Per-user stopwatches & timers |
| `tallinn_alcohol_prices` | Tallinn alcohol shop prices |
| `tallinn_riga_weather` | Weather for Tallinn / Riga |
| `tldr_summary_tool` | Summarises a URL or text via LLM |

### Tools that need credentials (see top-level `README.md` → Optional Integrations)
| Tool | Auth |
| --- | --- |
| `duolingo_stats_tool` | None — uses Duolingo public profile API |
| `spotify_lookup_tool` | Spotify OAuth (`SPOTIFY_CLIENT_ID/SECRET/REDIRECT_URI`) |
| `movie_search` | `TMDB_API_KEY` (+ optional YouTube cookies for yt-dlp) |
| `media_creator` | `OPENAI_API_KEY` (and optional `ELEVENLABS_API_KEY`) |
| `openai_api_usage_status` | `OPENAI_ADMIN_API_KEY` |
| `ewelink_smart_home` | `EWELINK_EMAIL` / `EWELINK_PASSWORD` / `EWELINK_REGION` |
| `huum_sauna` | `HUUM_USERNAME` / `HUUM_PASSWORD` |
| `cozytouch_heatpump` | `COZYTOUCH_USERNAME` / `COZYTOUCH_PASSWORD` |

## Adding your own

From inside Telegram (admin role):

```
>> fetch the current Bitcoin price from CoinGecko
```

The factory will draft code, run a security review, and (after admin
approval for non-admin users) drop the new `<name>.py` + `<name>.json`
into this folder. See the top-level README for details.

## What's **not** committed

Runtime state files are git-ignored:
`*_cache.json`, `*_seen.json`, `*_state.json`, `*.db`,
`regcar_cache.json`, `task_notifications.json`, `stock_watchlist.json`,
`anekdot_seen.json`, `movie_cache.db`.
