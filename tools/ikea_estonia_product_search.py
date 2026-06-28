async def run(context: dict) -> str:
    try:
        import aiohttp
        import json
        from html import escape as esc
        from urllib.parse import quote

        args = context.get('args', []) or []
        query = ' '.join(args).strip()
        if not query:
            return 'Usage: <code>ikea sofa</code> or <code>ikea shelf latvia</code>'

        # Parse country filter from args
        COUNTRY_MAP = {
            "estonia": "ee", "eesti": "ee", "эстония": "ee",
            "latvia": "lv", "latvija": "lv", "латвия": "lv",
            "lithuania": "lt", "lietuva": "lt", "литва": "lt",
            "finland": "fi", "suomi": "fi", "финляндия": "fi",
            "sweden": "se", "sverige": "se", "швеция": "se",
            "poland": "pl", "polska": "pl", "польша": "pl",
        }
        COUNTRY_NAMES = {"ee": "Estonia", "lv": "Latvia", "lt": "Lithuania", "fi": "Finland", "se": "Sweden", "pl": "Poland"}
        COUNTRY_FLAGS = {"ee": "🇪🇪", "lv": "🇱🇻", "lt": "🇱🇹", "fi": "🇫🇮", "se": "🇸🇪", "pl": "🇵🇱"}
        COUNTRY_LANG = {"ee": "et", "lv": "lv", "lt": "lt", "fi": "fi", "se": "sv", "pl": "pl"}
        # Also search in English as fallback for each country
        COUNTRY_LANG_FALLBACK = {"ee": "en", "lv": "en", "lt": "en", "fi": "en", "se": "en", "pl": "en"}

        country_code = "ee"  # default
        query_words = []
        for w in query.split():
            if w.lower() in COUNTRY_MAP:
                country_code = COUNTRY_MAP[w.lower()]
            else:
                query_words.append(w)
        query = " ".join(query_words).strip()
        if not query:
            return 'Please provide a product name after the country.'

        country_name = COUNTRY_NAMES.get(country_code, "Estonia")
        flag = COUNTRY_FLAGS.get(country_code, "")
        lang = COUNTRY_LANG.get(country_code, "en")
        lang_fb = COUNTRY_LANG_FALLBACK.get(country_code, "en")
        max_results = 8

        UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

        async with aiohttp.ClientSession() as session:
            items = []
            # Try local language first, then English fallback
            for try_lang in ([lang, lang_fb] if lang != lang_fb else [lang]):
                api_url = f"https://sik.search.blue.cdtapps.com/{country_code}/{try_lang}/search-result-page?q={quote(query)}&size={max_results}"
                async with session.get(api_url, headers={"User-Agent": UA, "Accept": "*/*"},
                                       timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json(content_type=None)
                items = (data.get("searchResultPage", {})
                             .get("products", {})
                             .get("main", {})
                             .get("items", []))
                if items:
                    break

        if not items:
            return f"No IKEA products found for: {esc(query)} in {country_name}"

        lines = [f"<b>{flag} IKEA {esc(country_name)}</b> — \"{esc(query)}\"\n"]

        for item in items[:max_results]:
            p = item.get("product", {})
            name = p.get("name", "")
            type_name = p.get("typeName", "")
            pip_url = p.get("pipUrl", "")
            sales = p.get("salesPrice", {})
            price_num = sales.get("numeral")
            currency = sales.get("currencyCode", "EUR")

            if not name:
                continue

            display = f"<b>{esc(name)}</b>"
            if type_name:
                display += f" — {esc(type_name)}"

            price_str = ""
            if price_num is not None:
                price_str = f" €{price_num:.0f}" if price_num == int(price_num) else f" €{price_num:.2f}"

            if pip_url:
                lines.append(f'  <a href="{pip_url}">{display}</a>{price_str}')
            else:
                lines.append(f"  {display}{price_str}")

        lines.append(f"\nSource: IKEA {esc(country_name)}")
        return "\n".join(lines)
    except Exception as e:
        return f'Error searching IKEA: {str(e)}'