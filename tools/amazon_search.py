async def run(context: dict) -> str:
    import aiohttp
    import os
    import json
    import re
    from html import escape as html_escape

    try:
        args = context.get("args", []) or []
        if not args:
            return (
                "Usage: <code>amazon &lt;query&gt;</code> [--max N] [--sort price]\n\n"
                "Examples:\n"
                "  <code>amazon iPhone 15 Pro case</code>\n"
                "  <code>amazon running shoes --max 10</code>\n"
                "  <code>amazon monitor arm --sort price</code>"
            )

        # Parse flags
        max_results = 5
        sort_mode = ""
        filtered_args = []
        skip_next = False
        for i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if arg == "--max" and i + 1 < len(args):
                try:
                    max_results = min(max(int(args[i + 1]), 1), 20)
                except ValueError:
                    pass
                skip_next = True
            elif arg == "--sort" and i + 1 < len(args):
                sort_mode = str(args[i + 1]).strip().lower()
                skip_next = True
            else:
                filtered_args.append(arg)

        query = " ".join(filtered_args).strip()
        if not query:
            return "Please provide a search query. Example: <code>amazon laptop stand</code>"

        if sort_mode and sort_mode != "price":
            return "Unsupported sort option. Currently supported: <code>--sort price</code>"

        api_key = os.getenv("SERPAPI_KEY", "")
        if not api_key:
            return "SERPAPI_KEY is not configured."

        url = "https://serpapi.com/search.json"
        params = {
            "engine": "amazon",
            "amazon_domain": "amazon.de",
            "k": query,
            "language": "en_GB",
            "api_key": api_key,
        }

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return f"SerpApi returned HTTP {resp.status}: {html_escape(text[:200])}"
                data = json.loads(await resp.text())

        results = data.get("organic_results", [])
        if not results:
            return f"No Amazon results found for {html_escape(query)}."

        def parse_price(value):
            try:
                if value is None:
                    return None
                if isinstance(value, (int, float)):
                    return float(value)
                text = str(value).strip()
                if not text:
                    return None
                text = text.replace("€", "").replace("EUR", "").replace("\xa0", " ").strip()
                match = re.search(r"(\d+[\d\.,]*)", text)
                if not match:
                    return None
                num = match.group(1)
                if "," in num and "." in num:
                    if num.rfind(",") > num.rfind("."):
                        num = num.replace(".", "").replace(",", ".")
                    else:
                        num = num.replace(",", "")
                elif "," in num:
                    parts = num.split(",")
                    if len(parts) == 2 and len(parts[1]) in (1, 2):
                        num = num.replace(".", "").replace(",", ".")
                    else:
                        num = num.replace(",", "")
                else:
                    num = num.replace(",", "")
                return float(num)
            except Exception:
                return None

        if sort_mode == "price":
            results = sorted(
                results,
                key=lambda item: (
                    parse_price(item.get("price")) is None,
                    parse_price(item.get("price")) if parse_price(item.get("price")) is not None else float("inf")
                )
            )

        results = results[:max_results]
        header = f"📦 Amazon.de results for: {html_escape(query)}"
        if sort_mode == "price":
            header += " (sorted by price)"
        lines = [header + "\n"]

        for i, item in enumerate(results, 1):
            title = html_escape(item.get("title", "N/A"))
            price = html_escape(str(item.get("price", "N/A")))
            old_price = item.get("old_price", "")
            rating = item.get("rating")
            reviews = item.get("reviews")
            delivery_list = item.get("delivery", [])
            delivery = html_escape(str(delivery_list[0])) if delivery_list else ""
            link = item.get("link_clean") or item.get("link", "")

            line = f"{i}. {title}"
            if old_price:
                line += f"\n   💰 {price}  <s>{html_escape(str(old_price))}</s>"
            else:
                line += f"\n   💰 {price}"
            if rating:
                line += f"\n   ⭐ {html_escape(str(rating))}"
                if reviews:
                    line += f" ({html_escape(str(reviews))} reviews)"
            if delivery:
                line += f"\n   🚚 {delivery}"
            if link:
                line += f'\n   🔗 <a href="{html_escape(str(link), quote=True)}">View on Amazon</a>'
            lines.append(line)

        return "\n\n".join(lines)

    except Exception as e:
        return f"Amazon search tool error: {e}"
