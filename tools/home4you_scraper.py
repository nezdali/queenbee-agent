async def run(context: dict) -> str:
    from tool_utils import parse_args, fetch_rendered
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse
    import json
    import re
    import asyncio

    try:
        if context is None:
            context = {}
        try:
            args = parse_args(context)
        except Exception:
            args = context.get('args', []) if isinstance(context, dict) else []
        if not isinstance(args, list):
            args = list(args) if args else []
        category_path = args[0].strip() if args else ''
        max_pages = 3
        if len(args) > 1:
            try:
                parsed_pages = int(float(args[1]))
                if parsed_pages < 1:
                    parsed_pages = 1
                if parsed_pages > 10:
                    parsed_pages = 10
                max_pages = parsed_pages
            except Exception:
                max_pages = 3

        base_url = 'https://home4you.ee/'
        if category_path:
            if category_path.startswith('http'):
                base_url = category_path
            else:
                base_url = urljoin('https://home4you.ee/', category_path.lstrip('/'))
        base_url = base_url.strip()
        if not base_url:
            base_url = 'https://home4you.ee/'

        parsed_base = urlparse(base_url)
        root_url = f'{parsed_base.scheme}://{parsed_base.netloc}' if parsed_base.netloc else 'https://home4you.ee'

        wait_selector = '.product-card, .product-item, .catalog-grid__item, .product-grid__item'
        card_selectors = [
            '.product-card',
            '.catalog-grid__item',
            '.product-grid__item',
            '.product-item',
            'li.product',
            'div[data-product-id]',
            'div.product',
        ]

        def collapse_spaces(text: str) -> str:
            return re.sub(r'\s+', ' ', text).strip() if text else ''

        def escape_md(text: str) -> str:
            if not text:
                return ''
            return re.sub(r'([_*\[\]()~`>#+=|{}.!-])', r'\\\1', text)

        def clean_price(text: str) -> str:
            if not text:
                return ''
            text = collapse_spaces(text)
            match = re.search(r'(\d[\d\s.,]*)', text)
            if match:
                numeric = match.group(1).replace(' ', '').replace(',', '.')
                try:
                    value = float(numeric)
                    currency_match = re.search(r'[€$£]', text)
                    currency = currency_match.group(0) if currency_match else '€'
                    return f'{value:.2f} {currency}'
                except Exception:
                    pass
            return text

        def extract_text_by_selectors(node, selectors):
            for sel in selectors:
                el = node.select_one(sel)
                if el:
                    text_value = collapse_spaces(' '.join(el.stripped_strings))
                    if text_value:
                        return text_value
            return ''

        def extract_dimensions(card):
            dim_selectors = [
                '.product-card__dimensions',
                '.product-card__attribute--dimensions',
                '.product-dimensions',
                '.attribute-dimensions',
                '.specifications__item--dimensions',
                '.product-list-item__dimensions',
            ]
            for sel in dim_selectors:
                el = card.select_one(sel)
                if el:
                    value = collapse_spaces(' '.join(el.stripped_strings))
                    if value:
                        return value
            candidate_texts = []
            for li in card.select('li'):
                li_text = collapse_spaces(' '.join(li.stripped_strings))
                if li_text and re.search(r'\b\d+(?:[.,]\d+)?\s*(?:cm|mm|m)\b', li_text.lower()):
                    candidate_texts.append(li_text)
            if candidate_texts:
                unique = []
                seen = set()
                for item in candidate_texts:
                    if item not in seen:
                        unique.append(item)
                        seen.add(item)
                return '; '.join(unique)
            for text in card.stripped_strings:
                t = text.strip()
                if re.search(r'\b\d+(?:[.,]\d+)?\s*(?:cm|mm|m)\b', t.lower()):
                    return collapse_spaces(t)
            return ''

        def infer_stock(card):
            stock_text = extract_text_by_selectors(
                card,
                [
                    '.stock-status',
                    '.availability',
                    '.product-card__stock',
                    '.product-card__availability',
                    '.product-item-availability',
                    '.status',
                ],
            )
            if not stock_text:
                for attr in ['data-stock', 'data-in-stock', 'data-availability', 'data-stock-status']:
                    val = card.attrs.get(attr)
                    if val:
                        stock_text = str(val)
                        break
            if not stock_text:
                return None
            text = stock_text.lower()
            if any(keyword in text for keyword in ['out of stock', 'not available', 'otsas', 'lopp', 'preorder', 'pre-order', 'tellimisel', 'backorder']):
                return False
            if any(keyword in text for keyword in ['in stock', 'laos', 'available', 'varast']):
                return True
            return None

        def extract_image(card):
            img = card.select_one('img')
            if img:
                for attr in ['data-src', 'data-srcset', 'srcset', 'data-original', 'src']:
                    val = img.get(attr)
                    if val:
                        val = val.strip()
                        if not val:
                            continue
                        if ' ' in val and (attr == 'srcset' or attr.endswith('srcset')):
                            val = val.split(',')[0].strip().split(' ')[0]
                        return urljoin(root_url, val)
            return ''

        def normalize_url(raw_url: str) -> str:
            if not raw_url:
                return ''
            try:
                parsed = urlparse(raw_url)
                normalized = parsed._replace(fragment='', query='')
                cleaned = urlunparse(normalized).rstrip('/')
                if not cleaned:
                    cleaned = raw_url
                return cleaned
            except Exception:
                return raw_url

        def extract_product(card):
            link_candidates = card.select('a')
            product_url = ''
            for link in link_candidates:
                href = link.get('href')
                if not href or href.strip() == '#':
                    continue
                product_url = urljoin(root_url, href)
                break
            title = extract_text_by_selectors(
                card,
                [
                    '.product-card__title a',
                    '.product-card__title',
                    '.product-title a',
                    '.product-title',
                    '.product-name a',
                    '.product-name',
                    '.product-item-link',
                    '.title a',
                ],
            )
            if not title and link_candidates:
                title = collapse_spaces(' '.join(link_candidates[0].stripped_strings))
            if not title and product_url:
                slug = product_url.rstrip('/').split('/')[-1]
                title = collapse_spaces(re.sub(r'[-_]+', ' ', slug)).title()
            price = clean_price(
                extract_text_by_selectors(
                    card,
                    [
                        '.price--current',
                        '.price-wrapper .price',
                        '.price .price',
                        '.price',
                        '.product-price',
                        '.special-price .price',
                    ],
                )
            )
            if not price:
                for attr in ['data-price', 'data-price-amount', 'data-product-price', 'data-price-final']:
                    val = card.attrs.get(attr)
                    if val:
                        price = clean_price(str(val))
                        if price:
                            break
            discount_price = clean_price(
                extract_text_by_selectors(
                    card,
                    [
                        '.price--old',
                        '.old-price',
                        '.price-old',
                        '.price__old',
                        '.was-price',
                        '.price-regular',
                    ],
                )
            )
            if not discount_price:
                for attr in ['data-old-price', 'data-base-price']:
                    val = card.attrs.get(attr)
                    if val:
                        discount_price = clean_price(str(val))
                        if discount_price:
                            break
            if discount_price and price and discount_price == price:
                discount_price = ''
            dimensions = extract_dimensions(card)
            in_stock = infer_stock(card)
            image_url = extract_image(card)
            return {
                'title': title,
                'price': price,
                'discount_price': discount_price,
                'dimensions': dimensions,
                'in_stock': in_stock,
                'product_url': product_url,
                'image_url': image_url,
            }

        def build_page_url(base: str, page_number: int) -> str:
            if page_number <= 1:
                return base
            parsed = urlparse(base)
            query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
            query_dict = {}
            for key, value in query_pairs:
                query_dict.setdefault(key, []).append(value)
            query_dict['page'] = [str(page_number)]
            new_query = urlencode(query_dict, doseq=True)
            return urlunparse(parsed._replace(query=new_query))

        async def get_page_soup(url: str):
            last_error = ''
            for attempt in range(1, 4):
                try:
                    result = await fetch_rendered(
                        url,
                        wait_for=wait_selector,
                        return_html=True,
                        stealth=True,
                        timeout=25,
                    )
                except Exception as exc:
                    last_error = f'{exc}'
                    await asyncio.sleep(1)
                    continue
                if not result:
                    last_error = 'Empty response'
                    await asyncio.sleep(1)
                    continue
                status = result.get('status', 200)
                if status and status >= 400:
                    last_error = f'HTTP {status}'
                    await asyncio.sleep(1)
                    continue
                if result.get('error'):
                    last_error = result.get('error')
                    await asyncio.sleep(1)
                    continue
                html = result.get('html', '')
                if not html:
                    last_error = 'No HTML returned'
                    await asyncio.sleep(1)
                    continue
                soup = BeautifulSoup(html, 'html.parser')
                return soup, ''
            return None, last_error or 'Unknown error'

        products = []
        seen_urls = set()
        page_errors = []
        pages_processed = 0
        consecutive_empty = 0

        for page_number in range(1, max_pages + 1):
            page_url = build_page_url(base_url, page_number)
            soup, error_message = await get_page_soup(page_url)
            if soup is None:
                fallback_error = error_message or 'Failed to load'
                page_errors.append(f'Page {page_number}: {fallback_error}')
                if page_number == 1:
                    first_fail = error_message or 'Unknown error'
                    return f'Failed to load {page_url}: {first_fail}'
                continue
            cards = []
            for sel in card_selectors:
                found = soup.select(sel)
                if found:
                    cards = found
                    break
            pages_processed += 1
            if not cards:
                consecutive_empty += 1
                selectors_tried = ', '.join(card_selectors)
                page_errors.append(f'Page {page_number}: No product cards found (selectors tried: {selectors_tried})')
                if consecutive_empty >= 2:
                    break
                continue
            else:
                consecutive_empty = 0
            before_count = len(seen_urls)
            for card in cards:
                try:
                    product = extract_product(card)
                except Exception:
                    continue
                product_url = product.get('product_url', '').strip()
                if not product_url:
                    continue
                dedupe_key = normalize_url(product_url) or product_url
                if dedupe_key in seen_urls:
                    continue
                seen_urls.add(dedupe_key)
                products.append(product)
            if len(seen_urls) == before_count and page_number > 1:
                break
            await asyncio.sleep(0.3)

        if not products:
            if page_errors:
                error_lines = '\n'.join(page_errors)
                return f'No products were found. Issues encountered:\n{error_lines}'
            return 'No products were found for the specified category.'

        total_products = len(products)
        sample_products = products[:20]

        lines = []
        lines.append('*Home4you.ee Product Scrape*')
        lines.append(f'URL: {escape_md(base_url)}')
        lines.append(f'Pages processed: {pages_processed}')
        lines.append(f'Products found: {total_products}')
        if page_errors:
            lines.append('_Warnings:_')
            for err in page_errors:
                lines.append(f' - {escape_md(err)}')
        if sample_products:
            lines.append('')
            lines.append('*Sample (first 20 products)*')
            for idx, item in enumerate(sample_products, start=1):
                title_text = escape_md(item.get('title') or 'Untitled product')
                price_text = escape_md(item.get('price') or 'N/A')
                discount_text = escape_md(item.get('discount_price') or '')
                dims_text = escape_md(item.get('dimensions') or 'N/A')
                stock_value = item.get('in_stock')
                if stock_value is True:
                    stock_text = 'In stock'
                elif stock_value is False:
                    stock_text = 'Out of stock'
                else:
                    stock_text = 'Unknown'
                stock_text = escape_md(stock_text)
                lines.append(f'{idx}. {title_text}')
                detail_parts = [f'Price: {price_text}', f'Stock: {stock_text}']
                if discount_text:
                    detail_parts.append(f'Discount: {discount_text}')
                if dims_text and dims_text != 'N/A':
                    detail_parts.append(f'Dimensions: {dims_text}')
                lines.append('   ' + '; '.join(detail_parts))
                product_link = item.get('product_url')
                if product_link:
                    lines.append(f'   {product_link}')
                image_link = item.get('image_url')
                if image_link:
                    lines.append(f'   Image: {image_link}')

        full_json = json.dumps(products, ensure_ascii=False, indent=2)
        lines.append('')
        lines.append('```json')
        lines.append(full_json)
        lines.append('```')
        return '\n'.join(lines)
    except Exception as exc:
        return f'Scrape failed: {exc}'
