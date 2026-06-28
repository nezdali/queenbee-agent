_EE_STORES = (
    "notino.ee", "douglas.ee", "ilu.ee", "1a.ee", "kaubamaja.ee",
    "benu.ee", "apotheka.ee", "ideal.ee", "shoppa.ee", "makeup.ee",
    "cosmo.ee", "loverte.ee",
)


async def run(context: dict) -> str:
    """Search cosmetics & beauty products available in Estonia."""
    try:
        from tool_utils import fetch_json, parse_args
        import os, re

        args = parse_args(context)
        query = " ".join(args).strip()
        if not query:
            return (
                "Usage: cosmetics <product>\n\n"
                "Examples:\n"
                "  cosmetics cerave cleanser\n"
                "  cosmetics dior lipstick\n"
                "  cosmetics nivea cream\n"
                "  cosmetics the ordinary niacinamide"
            )

        api_key = os.getenv("SERPAPI_KEY", "")
        if not api_key:
            return "SERPAPI_KEY is not configured."

        # --- 1. Google Shopping (gl=ee) ---
        shop_params = {
            "engine": "google_shopping",
            "q": query,
            "gl": "ee",
            "hl": "en",
            "api_key": api_key,
            "num": 15,
        }
        shop_data, shop_st = await fetch_json(
            "https://serpapi.com/search.json", params=shop_params, timeout=20,
        )
        shop_results = []
        if shop_data and shop_st == 200:
            shop_results = (
                shop_data.get("shopping_results")
                or shop_data.get("inline_shopping_results")
                or []
            )

        # --- 2. Google Search targeting Estonian store domains ---
        site_filter = " OR ".join(f"site:{s}" for s in _EE_STORES)
        web_params = {
            "engine": "google",
            "q": f"{query} ({site_filter})",
            "gl": "ee",
            "hl": "en",
            "api_key": api_key,
            "num": 10,
        }
        web_data, web_st = await fetch_json(
            "https://serpapi.com/search.json", params=web_params, timeout=20,
        )
        web_results = []
        if web_data and web_st == 200:
            web_results = web_data.get("organic_results") or []

        if not shop_results and not web_results:
            return f"No cosmetics results found for: {query}"

        lines = [f"💄 Cosmetics search: {query}\n"]

        # --- Estonian web results first ---
        ee_shown = 0
        if web_results:
            lines.append("🇪🇪 Estonian stores:")
            for r in web_results:
                link = r.get("link", "")
                # only keep results actually on .ee domains
                if not any(s in link for s in _EE_STORES):
                    continue
                title = r.get("title", "?")
                snippet = r.get("snippet", "")
                if len(title) > 80:
                    title = title[:77] + "..."
                # try to extract price from snippet
                price_match = re.search(r'(\d[\d\s,.]*\s*€|€\s*\d[\d\s,.]*)', snippet)
                price_str = f"  💰 {price_match.group(0).strip()}" if price_match else ""
                lines.append(f"  • {title}{price_str}\n    🔗 {link}")
                ee_shown += 1
                if ee_shown >= 8:
                    break
            if ee_shown == 0:
                lines.pop()  # remove header if nothing
            else:
                lines.append("")

        # --- Google Shopping results (international / best price) ---
        if shop_results:
            lines.append("🌍 Best prices (Google Shopping):")
            shown = 0
            for r in shop_results:
                title = r.get("title", "?")
                price = r.get("price", "?")
                store = r.get("source", "?")
                link = (
                    r.get("product_link")
                    or r.get("link")
                    or r.get("serpapi_product_api")
                    or ""
                )
                if len(title) > 80:
                    title = title[:77] + "..."
                lines.append(f"  • {price} — {store}\n    {title}")
                if link and not link.startswith("https://serpapi.com"):
                    lines.append(f"    🔗 {link}")
                shown += 1
                if shown >= 6:
                    break
            lines.append("")

        lines.append(
            f"Found {ee_shown} Estonian store result(s) + "
            f"{len(shop_results)} Shopping result(s)."
        )
        return "\n".join(lines).strip()
    except Exception as e:
        return f"Cosmetics search error: {e}"