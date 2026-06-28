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

Grouped by topic. The **Auth** column lists required env vars
(see top-level `README.md` → *Optional Integrations*); `—` means no
credentials needed.

### Finance & markets
| Tool | What it does | Auth |
| --- | --- | --- |
| `btc_price_coingecko` | Bitcoin spot price via CoinGecko | — |
| `euribor_rate_checker` | Latest Euribor reference rates across maturities | — |
| `openai_api_usage_status` | OpenAI API usage & cost for a natural-language period | `OPENAI_ADMIN_API_KEY` |

### Shopping & retail
| Tool | What it does | Auth |
| --- | --- | --- |
| `amazon_search` | Amazon.de product search with prices, ratings, links | `SERPAPI_KEY` |
| `google_shopping_search` | Google Shopping product prices in Estonia | `SERPAPI_KEY` |
| `ikea_estonia_product_search` | IKEA Estonia search, falls back to other IKEA sites | — |
| `jysk_estonia_products` | JYSK products with price & color filters | — |
| `home4you_scraper` | Home4You.ee furniture catalog (Playwright) | — |
| `estonia_cosmetics_lookup` | Cosmetics prices on Douglas.ee and other EE stores | — |

### Travel
| Tool | What it does | Auth |
| --- | --- | --- |
| `booking_com_search` | Booking.com hotel / apartment / hostel search | — |

### Fuel prices
| Tool | What it does | Auth |
| --- | --- | --- |
| `estonia_fuel_prices` | Estonia / Latvia petrol & diesel with EU comparison | — |
| `stavanger_fuel_prices` | Stavanger area diesel & 95, falls back to NO average | — |

### Alcohol prices
| Tool | What it does | Auth |
| --- | --- | --- |
| `tallinn_alcohol_prices` | Tallinn alcohol (Rimi.ee + Numbeo) | — |
| `riga_alcohol_prices` | Riga alcohol (Rimi.lv + Numbeo) | — |
| `stavanger_alcohol_prices` | Stavanger averages (Vinmonopolet + Numbeo) | — |

### Weather & environment
| Tool | What it does | Auth |
| --- | --- | --- |
| `tallinn_riga_weather` | Current weather for Tallinn & Riga | — |
| `kolga_laht_tide_tool` | Kolga Bay sea level / tide & temps (Ilmateenistus) | — |

### Sports
| Tool | What it does | Auth |
| --- | --- | --- |
| `fifa_worldcup_status` | FIFA World Cup 2026: standings, results, lookups | — |
| `snooker_championship_status` | Ongoing snooker tournament status | — |

### Entertainment & media
| Tool | What it does | Auth |
| --- | --- | --- |
| `anekdot_ru_fetcher` | Anecdote from anekdot.ru (by topic or random) | — |
| `movie_search` | TMDb info + full movies on YouTube with AI analysis | `TMDB_API_KEY` (+ YT cookies) |
| `spotify_lookup_tool` | Search Spotify songs / artists / albums / genres | Spotify OAuth |
| `media_creator` | Generate images (`gpt-image-1`) or video (`sora`) | `OPENAI_API_KEY` (+ optional `ELEVENLABS_API_KEY`) |

### Learning
| Tool | What it does | Auth |
| --- | --- | --- |
| `duolingo_stats_tool` | Duolingo profile & learning stats (public API) | — |

### Smart home & IoT
| Tool | What it does | Auth |
| --- | --- | --- |
| `ewelink_smart_home` | eWeLink Zigbee/WiFi devices, natural-language commands | `EWELINK_EMAIL` / `EWELINK_PASSWORD` / `EWELINK_REGION` |
| `huum_sauna` | Huum electric sauna (Drop + UKU Wi-Fi) status & control | `HUUM_USERNAME` / `HUUM_PASSWORD` |
| `cozytouch_heatpump` | Atlantic Cozytouch heat pump & DHW status / control | `COZYTOUCH_USERNAME` / `COZYTOUCH_PASSWORD` |

### Utilities
| Tool | What it does | Auth |
| --- | --- | --- |
| `stopwatch_timer_manager` | Per-user stopwatches & countdown timers | — |
| `tldr_summary_tool` | Five-bullet LLM summary of a URL or text | — |

## Adding your own

From inside Telegram (admin role):

```
>> fetch the current Bitcoin price from CoinGecko
```

The factory will draft code, run a security review, and (after admin
approval for non-admin users) drop the new `<name>.py` + `<name>.json`
into this folder. See the top-level README for details.
