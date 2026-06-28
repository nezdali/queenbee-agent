async def run(context: dict) -> str:
    try:
        import aiohttp
        import re
        from bs4 import BeautifulSoup

        urls = [
            "https://www.humalakoda.ee/",
            "https://www.humalakoda.ee/menuu/",
            "https://www.humalakoda.ee/menyy/",
            "https://www.humalakoda.ee/en/",
        ]

        headers = {
            "User-Tool": "Mozilla/5.0 (compatible; TelegramBot/1.0; +https://example.com)"
        }

        async def fetch_text(session, url):
            try:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20), ssl=False) as resp:
                    if resp.status == 200:
                        return await resp.text()
            except Exception:
                return None
            return None

        async with aiohttp.ClientSession() as session:
            html_pages = []
            for url in urls:
                html = await fetch_text(session, url)
                if html:
                    html_pages.append((url, html))

            if not html_pages:
                return "Couldn't fetch Humalakoda website right now."

            found_links = set(urls)
            for base_url, html in list(html_pages):
                try:
                    soup = BeautifulSoup(html, "html.parser")
                    for a in soup.find_all("a", href=True):
                        href = a.get("href", "").strip()
                        text = a.get_text(" ", strip=True).lower()
                        if any(k in href.lower() for k in ["menu", "men", "drink", "food", "cocktail", "beer", "wine"]):
                            if href.startswith("http"):
                                found_links.add(href)
                            elif href.startswith("/"):
                                found_links.add("https://www.humalakoda.ee" + href)
                        elif any(k in text for k in ["menu", "drinks", "food", "cocktails", "beers", "wine"]):
                            if href.startswith("http"):
                                found_links.add(href)
                            elif href.startswith("/"):
                                found_links.add("https://www.humalakoda.ee" + href)
                except Exception:
                    pass

            for url in list(found_links):
                if any(url == u for u, _ in html_pages):
                    continue
                html = await fetch_text(session, url)
                if html:
                    html_pages.append((url, html))

        price_pattern = re.compile(r"(?<!\d)(\d{1,3}[\.,]\d{1,2}|\d{1,3})\s?(?:€|eur)(?!\w)", re.I)
        text_price_pattern = re.compile(r"([A-ZÀ-ÿ0-9][^\n\r]{1,120}?)\s+(\d{1,3}[\.,]\d{1,2}|\d{1,3})\s?(€|eur)", re.I)

        items = []
        seen = set()

        def clean_name(name):
            name = re.sub(r"\s+", " ", name).strip(" -•|:\t")
            return name

        for url, html in html_pages:
            try:
                soup = BeautifulSoup(html, "html.parser")

                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()

                for table in soup.find_all("table"):
                    for tr in table.find_all("tr"):
                        cells = tr.find_all(["td", "th"])
                        if len(cells) >= 2:
                            name = clean_name(cells[0].get_text(" ", strip=True))
                            value = clean_name(cells[1].get_text(" ", strip=True))
                            m = price_pattern.search(value)
                            if name and m:
                                price = m.group(0).replace("EUR", "€").replace("eur", "€")
                                key = (name.lower(), price)
                                if key not in seen and len(name) > 1:
                                    seen.add(key)
                                    items.append((name, price, url))

                candidates = soup.find_all(["li", "p", "div", "span", "h2", "h3", "h4"])
                for el in candidates:
                    txt = clean_name(el.get_text(" ", strip=True))
                    if not txt or len(txt) < 4:
                        continue
                    if "€" in txt.lower() or "eur" in txt.lower():
                        m = text_price_pattern.search(txt)
                        if m:
                            name = clean_name(m.group(1))
                            price = (m.group(2).replace(",", ".") + " €")
                            bad = ["cookie", "privacy", "accept", "cart", "login"]
                            if any(b in name.lower() for b in bad):
                                continue
                            key = (name.lower(), price)
                            if key not in seen:
                                seen.add(key)
                                items.append((name, price, url))
            except Exception:
                continue

        if not items:
            page_summaries = []
            for url, html in html_pages[:3]:
                try:
                    soup = BeautifulSoup(html, "html.parser")
                    text = soup.get_text(" ", strip=True)
                    prices = sorted(set(m.group(0) for m in price_pattern.finditer(text)))
                    if prices:
                        page_summaries.append(f"{url}: {', '.join(prices[:15])}")
                except Exception:
                    pass
            if page_summaries:
                return "I found Humalakoda pages but couldn't reliably match item names to prices. Visible prices found:\n\n" + "\n".join(page_summaries)
            return "I couldn't find any food or drink prices on Humalakoda's public pages."

        def category(name):
            n = name.lower()
            if any(k in n for k in ["beer", "lager", "ale", "ipa", "stout", "porter", "pils", "siider", "cider"]):
                return 0
            if any(k in n for k in ["wine", "prosecco", "champagne"]):
                return 1
            if any(k in n for k in ["cocktail", "spritz", "gin", "tonic", "mojito", "margarita", "negroni"]):
                return 2
            if any(k in n for k in ["coffee", "tea", "juice", "water", "cola", "fanta", "sprite"]):
                return 3
            return 4

        items.sort(key=lambda x: (category(x[0]), x[0].lower()))
        lines = []
        for name, price, _ in items[:40]:
            lines.append(f"- {name} — {price}")

        source_urls = []
        for _, _, url in items[:10]:
            if url not in source_urls:
                source_urls.append(url)

        result = "Humalakoda Tallinn prices found online:\n\n" + "\n".join(lines)
        if source_urls:
            result += "\n\nSources:\n" + "\n".join(f"- {u}" for u in source_urls[:5])
        if len(items) > 40:
            result += f"\n\nShowing 40 of {len(items)} matched items."
        return result
    except Exception as e:
        return f"Error fetching Humalakoda prices: {str(e)}"