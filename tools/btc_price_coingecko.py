async def run(context: dict) -> str:
    import aiohttp
    import asyncio
    import json

    try:
        args = context.get("args", []) or []
        vs_currency = "usd"
        if args and isinstance(args[0], str) and args[0].strip():
            candidate = args[0].strip().lower()
            if candidate.isalpha() and 2 <= len(candidate) <= 10:
                vs_currency = candidate

        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "bitcoin",
            "vs_currencies": vs_currency,
            "include_24hr_change": "true",
            "include_last_updated_at": "true"
        }
        headers = {
            "accept": "application/json",
            "user-tool": "telegram-bot-tool/1.0"
        }

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, params=params) as resp:
                text = await resp.text()
                if resp.status != 200:
                    return f"Failed to fetch BTC price from CoinGecko (HTTP {resp.status})."
                try:
                    data = json.loads(text)
                except Exception:
                    return "Received an invalid response from CoinGecko."

        btc = data.get("bitcoin", {})
        if not btc:
            return "BTC price data was not found in CoinGecko response."

        price = btc.get(vs_currency)
        change = btc.get(f"{vs_currency}_24h_change")
        updated_at = btc.get("last_updated_at")

        if price is None:
            available = ", ".join(sorted([k for k in btc.keys() if not k.endswith("_24h_change") and k != "last_updated_at"]))
            if available:
                return f"Could not get BTC price in '{vs_currency}'. Available fields: {available}"
            return f"Could not get BTC price in '{vs_currency}'."

        currency_label = vs_currency.upper()
        message = f"BTC price: {price} {currency_label}"

        if isinstance(change, (int, float)):
            arrow = "📈" if change >= 0 else "📉"
            message += f"\n24h change: {arrow} {change:.2f}%"

        if isinstance(updated_at, (int, float)):
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(updated_at, tz=timezone.utc)
            message += f"\nUpdated: {dt.strftime('%Y-%m-%d %H:%M:%S UTC')}"

        message += "\nSource: CoinGecko"
        return message
    except Exception as e:
        return f"Error fetching BTC price: {str(e)}"