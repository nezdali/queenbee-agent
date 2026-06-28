async def run(context: dict) -> str:
    """Search JYSK products via Google Shopping (SerpAPI).
    JYSK sites are behind Cloudflare, so direct scraping is not possible."""
    try:
        import aiohttp
        import os
        import re
        import json
        from html import escape as esc

        args = context.get("args", []) or []
        if not args:
            return (
                "<b>🛋️ JYSK Product Search</b>\n\n"
                "Usage: <code>jysk pillow</code>\n"
                "Filters: <code>jysk sofa max=200 limit=10 country=ee</code>\n\n"
                "Supported countries: ee, lv, lt, fi, se, no, dk, pl"
            )

        COUNTRY_DOMAINS = {
            "ee": "jysk.ee", "estonia": "jysk.ee",
            "lv": "jysk.lv", "latvia": "jysk.lv",
            "lt": "jysk.lt", "lithuania": "jysk.lt",
            "fi": "jysk.fi", "finland": "jysk.fi",
            "se": "jysk.se", "sweden": "jysk.se",
            "no": "jysk.no", "norway": "jysk.no",
            "dk": "jysk.dk", "denmark": "jysk.dk",
            "pl": "jysk.pl", "poland": "jysk.pl",
        }
        COUNTRY_NAMES = {
            "jysk.ee": "🇪🇪 Estonia", "jysk.lv": "🇱🇻 Latvia",
            "jysk.lt": "🇱🇹 Lithuania", "jysk.fi": "🇫🇮 Finland",
            "jysk.se": "🇸🇪 Sweden", "jysk.no": "🇳🇴 Norway",
            "jysk.dk": "🇩🇰 Denmark", "jysk.pl": "🇵🇱 Poland",
        }
        GL_MAP = {
            "jysk.ee": "ee", "jysk.lv": "lv", "jysk.lt": "lt",
            "jysk.fi": "fi", "jysk.se": "se", "jysk.no": "no",
            "jysk.dk": "dk", "jysk.pl": "pl",
        }

        query_parts = []
        min_price = None
        max_price = None
        limit = 8
        site_domain = "jysk.ee"  # default Estonia

        for arg in args:
            a = str(arg).strip()
            low = a.lower()
            if low.startswith("min="):
                try:
                    min_price = float(a.split("=", 1)[1].replace(",", "."))
                except Exception:
                    pass
            elif low.startswith("max="):
                try:
                    max_price = float(a.split("=", 1)[1].replace(",", "."))
                except Exception:
                    pass
            elif low.startswith("limit="):
                try:
                    limit = max(1, min(20, int(a.split("=", 1)[1])))
                except Exception:
                    pass
            elif low.startswith("country="):
                raw = a.split("=", 1)[1].strip().lower()
                if raw in COUNTRY_DOMAINS:
                    site_domain = COUNTRY_DOMAINS[raw]
                else:
                    return f"Unknown country '{esc(raw)}'. Supported: ee, lv, lt, fi, se, no, dk, pl"
            elif low in COUNTRY_DOMAINS:
                site_domain = COUNTRY_DOMAINS[low]
            else:
                query_parts.append(a)

        if not query_parts:
            return "Please provide a product search term, e.g.: <code>jysk pillow</code>"

        query = " ".join(query_parts).strip()
        country_label = COUNTRY_NAMES.get(site_domain, site_domain)
        gl = GL_MAP.get(site_domain, "ee")

        api_key = os.getenv("SERPAPI_KEY", "")
        if not api_key:
            return "⚠️ SERPAPI_KEY is not configured. Cannot search JYSK products."

        # Use Google search with site: filter (Google Shopping doesn't support all gl codes)
        search_query = f"site:{site_domain} {query}"

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            results = []

            # Try Google Shopping first (without gl for small countries)
            shop_params = {
                "engine": "google_shopping",
                "q": f"JYSK {query}",
                "hl": "en",
                "num": str(min(limit + 5, 25)),
                "api_key": api_key,
            }
            # Only add gl for countries Google Shopping supports well
            if gl in ("dk", "fi", "se", "no", "pl", "de", "fr", "uk", "us"):
                shop_params["gl"] = gl

            async with session.get("https://serpapi.com/search.json", params=shop_params) as resp:
                if resp.status == 200:
                    data = json.loads(await resp.text())
                    shop_results = data.get("shopping_results") or data.get("inline_shopping_results") or []
                    # Filter to keep only results from the target JYSK domain
                    for item in shop_results:
                        src = (item.get("source") or "").lower()
                        link = (item.get("link") or "").lower()
                        if "jysk" in src or site_domain in link:
                            results.append(item)

            # Always also try regular Google search with site: filter
            params2 = {
                "engine": "google",
                "q": search_query,
                "gl": gl,
                "hl": "en",
                "num": str(min(limit + 5, 25)),
                "api_key": api_key,
            }
            async with session.get("https://serpapi.com/search.json", params=params2) as resp:
                if resp.status == 200:
                    data2 = json.loads(await resp.text())
                    # Also grab inline shopping from regular search
                    for item in (data2.get("inline_shopping") or data2.get("shopping_results") or []):
                        src = (item.get("source") or "").lower()
                        link = (item.get("link") or "").lower()
                        if "jysk" in src or site_domain in link:
                            results.append(item)
                    # Convert organic results
                    for r in data2.get("organic_results", []):
                        link = r.get("link", "")
                        if site_domain not in link:
                            continue
                        title = r.get("title", "")
                        snippet = r.get("snippet", "")
                        price_str = ""
                        price_match = re.search(r"(\d+[\.,]?\d*)\s*€", snippet)
                        if price_match:
                            price_str = price_match.group(0)
                        results.append({
                            "title": title,
                            "link": link,
                            "price": price_str,
                            "extracted_price": float(price_match.group(1).replace(",", ".")) if price_match else None,
                            "source": site_domain,
                            "snippet": snippet,
                        })

        if not results:
            return f"No JYSK products found for '<b>{esc(query)}</b>' on {country_label}."

        # Deduplicate by URL
        seen_urls = set()
        deduped = []
        for item in results:
            url = (item.get("link") or "").rstrip("/").lower()
            if url and url not in seen_urls:
                seen_urls.add(url)
                deduped.append(item)
        results = deduped

        # Apply price filters
        filtered = []
        for item in results:
            ep = item.get("extracted_price")
            if ep is None:
                # Try parsing from price string
                ps = item.get("price", "")
                if ps:
                    m = re.search(r"[\d,]+\.?\d*", str(ps).replace(",", ""))
                    if m:
                        try:
                            ep = float(m.group(0))
                        except Exception:
                            pass
            item["_price_num"] = ep
            if min_price is not None and ep is not None and ep < min_price:
                continue
            if max_price is not None and ep is not None and ep > max_price:
                continue
            filtered.append(item)

        # Sort by price
        filtered.sort(key=lambda p: (p["_price_num"] is None, p["_price_num"] or 10**9))
        filtered = filtered[:limit]

        if not filtered:
            extra = []
            if min_price is not None:
                extra.append(f"min €{min_price:.2f}")
            if max_price is not None:
                extra.append(f"max €{max_price:.2f}")
            ftxt = ", ".join(extra) if extra else "given filters"
            return f"No JYSK products found for '<b>{esc(query)}</b>' with {ftxt}."

        lines = [f"<b>🛋️ JYSK {esc(country_label)}</b> — \"{esc(query)}\"\n"]

        extra_filters = []
        if min_price is not None:
            extra_filters.append(f"min €{min_price:.2f}")
        if max_price is not None:
            extra_filters.append(f"max €{max_price:.2f}")
        if extra_filters:
            lines.append(f"Filters: {', '.join(extra_filters)}\n")

        for i, item in enumerate(filtered, 1):
            title = esc(item.get("title", "N/A"))
            price = item.get("price", "")
            link = item.get("link", "")
            source = item.get("source", "")
            snippet = item.get("snippet", "")
            rating = item.get("rating")
            reviews = item.get("reviews")

            if link:
                line = f"{i}. <a href=\"{link}\">{title}</a>"
            else:
                line = f"{i}. {title}"

            if price:
                line += f"\n   💰 {esc(str(price))}"
            if source and source != site_domain:
                line += f"  —  {esc(source)}"
            if rating:
                line += f"\n   ⭐ {rating}"
                if reviews:
                    line += f" ({reviews} reviews)"
            if snippet and not price:
                line += f"\n   {esc(snippet[:120])}"

            lines.append(line)

        lines.append(f"\nSource: Google Shopping → {esc(site_domain)}")
        return "\n".join(lines)

    except Exception as e:
        return f"Error searching JYSK products: {str(e)}"