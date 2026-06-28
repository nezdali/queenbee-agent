async def run(context: dict) -> str:
    import aiohttp
    import os
    import json
    from html import escape as html_escape
    from urllib.parse import quote

    try:
        args = context.get("args", []) or []
        if not args:
            return (
                "Usage: `shopping <query>` [--max N]\n\n"
                "Examples:\n"
                "  `shopping iPhone 15 Pro`\n"
                "  `shopping running shoes --max 10`"
            )

        # Parse --max flag
        max_results = 5
        filtered_args = []
        skip_next = False
        for i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if arg == "--max" and i + 1 < len(args):
                try:
                    max_results = min(int(args[i + 1]), 20)
                except ValueError:
                    pass
                skip_next = True
            else:
                filtered_args.append(arg)

        query = " ".join(filtered_args).strip()
        if not query:
            return "Please provide a search query. Example: `shopping laptop stand`"

        api_key = os.getenv("SERPAPI_KEY", "")
        if not api_key:
            return "SERPAPI_KEY is not configured."

        url = "https://serpapi.com/search.json"
        params = {
            "engine": "google_shopping",
            "q": query,
            "gl": "de",
            "hl": "en",
            "api_key": api_key,
        }

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return f"SerpApi returned HTTP {resp.status}: {text[:200]}"
                data = json.loads(await resp.text())

        results = data.get("shopping_results") or data.get("inline_shopping_results") or []
        if not results:
            return f"No shopping results found for *{query}*."

        results = results[:max_results]
        lines = [f"🛒 Google Shopping results for: {html_escape(query)}\n"]
        for i, item in enumerate(results, 1):
            title = html_escape(item.get("title", "N/A"))
            price = html_escape(item.get("price", "N/A"))
            source = html_escape(item.get("source", ""))
            rating = item.get("rating")
            reviews = item.get("reviews")
            delivery = html_escape(item.get("delivery", ""))

            line = f"{i}. {title}\n   💰 {price}"
            if source:
                line += f"  —  {source}"
            if rating:
                line += f"\n   ⭐ {rating}"
                if reviews:
                    line += f" ({reviews} reviews)"
            if delivery:
                line += f"\n   🚚 {delivery}"
            product_url = "https://www.google.com/search?tbm=shop&gl=de&num=5&q=" + quote(item.get("title", ""))
            line += f'\n   🔗 <a href="{product_url}">Search this product</a>'
            lines.append(line)

        search_url = "https://www.google.com/search?tbm=shop&gl=de&num=5&q=" + quote(query)
        lines.append(f'\n🔗 <a href="{search_url}">View all on Google Shopping</a>')

        return "\n\n".join(lines)

    except Exception as e:
        return f"Google Shopping tool error: {e}"
