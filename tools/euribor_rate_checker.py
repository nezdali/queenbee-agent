async def run(context: dict) -> str:
    from tool_utils import fetch_html, fetch_rendered, parse_args
    from bs4 import BeautifulSoup
    import re
    from datetime import datetime

    def safe_args(ctx):
        try:
            parsed = parse_args(ctx)
            if parsed is None:
                return []
            return [str(arg) for arg in parsed if isinstance(arg, str)]
        except Exception:
            raw = ctx.get('args') or []
            return [str(arg) for arg in raw if isinstance(arg, str)]

    args = safe_args(context)
    args_lower = [arg.lower() for arg in args]
    combined_query = ' '.join(args_lower)

    term_display = {
        'overnight': 'Overnight (O/N)',
        '1w': '1 week',
        '2w': '2 weeks',
        '3w': '3 weeks',
        '4w': '4 weeks',
        '1m': '1 month',
        '2m': '2 months',
        '3m': '3 months',
        '4m': '4 months',
        '5m': '5 months',
        '6m': '6 months',
        '7m': '7 months',
        '8m': '8 months',
        '9m': '9 months',
        '10m': '10 months',
        '11m': '11 months',
        '12m': '12 months'
    }

    term_order = ['overnight', '1w', '2w', '3w', '4w', '1m', '2m', '3m', '4m', '5m', '6m', '7m', '8m', '9m', '10m', '11m', '12m']

    number_words = {
        'one': 1,
        'two': 2,
        'three': 3,
        'four': 4,
        'five': 5,
        'six': 6,
        'seven': 7,
        'eight': 8,
        'nine': 9,
        'ten': 10,
        'eleven': 11,
        'twelve': 12,
        'один': 1,
        'одна': 1,
        'два': 2,
        'две': 2,
        'три': 3,
        'четыре': 4,
        'пять': 5,
        'шесть': 6,
        'семь': 7,
        'восемь': 8,
        'девять': 9,
        'десять': 10,
        'одиннадцать': 11,
        'двенадцать': 12
    }

    def detect_term(text):
        if not text:
            return None
        t = text.lower()
        if any(keyword in t for keyword in ['overnight', 'o/n', 'овернайт']):
            return 'overnight'
        week_match = re.search(r'(\d+)\s*[-\s]?(?:week|weeks|неделя|недели|недель|нед\.?|w\b)', t)
        if week_match:
            return f'{int(week_match.group(1))}w'
        month_match = re.search(r'(\d+)\s*[-\s]?(?:month|months|месяц|месяца|месяцев|мес\.?|m\b)', t)
        if month_match:
            return f'{int(month_match.group(1))}m'
        year_match = re.search(r'(\d+)\s*[-\s]?(?:year|years|год|года|лет|г\.?)', t)
        if year_match:
            try:
                years = int(year_match.group(1))
                if years > 0:
                    return f'{years * 12}m'
            except Exception:
                pass
        direct_match = re.search(r'(\d+)\s*(m|w)\b', t)
        if direct_match:
            return f'{int(direct_match.group(1))}{direct_match.group(2)}'
        for word, num in number_words.items():
            if word in t:
                if any(unit in t for unit in ['month', 'months', 'месяц', 'месяца', 'месяцев', 'мес']):
                    return f'{num}m'
                if any(unit in t for unit in ['week', 'weeks', 'неделя', 'недели', 'недель', 'нед']):
                    return f'{num}w'
        return None

    def parse_rate(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value)
        text = text.replace('%', '').replace(',', '.')
        match = re.search(r'-?\d+(?:\.\d+)?', text)
        if match:
            try:
                return float(match.group())
            except ValueError:
                return None
        return None

    def normalize_date(raw):
        if not raw:
            return None
        raw_clean = str(raw).strip()
        formats = ['%d %B %Y', '%B %d, %Y', '%Y-%m-%d']
        for fmt in formats:
            try:
                dt = datetime.strptime(raw_clean, fmt)
                return dt.strftime('%Y-%m-%d')
            except Exception:
                continue
        match_iso = re.search(r'(\d{4}-\d{2}-\d{2})', raw_clean)
        if match_iso:
            return match_iso.group(1)
        match_eu = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', raw_clean)
        if match_eu:
            try:
                dt = datetime.strptime(match_eu.group(1), '%d/%m/%Y')
                return dt.strftime('%Y-%m-%d')
            except Exception:
                pass
        match_dot = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4})', raw_clean)
        if match_dot:
            try:
                dt = datetime.strptime(match_dot.group(1), '%d.%m.%Y')
                return dt.strftime('%Y-%m-%d')
            except Exception:
                pass
        return raw_clean

    def extract_last_update(soup):
        if soup is None:
            return None
        for time_tag in soup.find_all('time'):
            parent_text = time_tag.parent.get_text(' ', strip=True).lower() if time_tag.parent else ''
            if any(keyword in parent_text for keyword in ['update', 'updated', 'last update', 'last updated']):
                dt_attr = time_tag.get('datetime')
                if dt_attr:
                    normalized = normalize_date(dt_attr)
                    if normalized:
                        return normalized
                text_time = time_tag.get_text(' ', strip=True)
                if text_time:
                    normalized = normalize_date(text_time)
                    if normalized:
                        return normalized
        candidates = soup.find_all(string=re.compile('update', re.I))
        for node in candidates:
            if not node:
                continue
            text = node.strip()
            if text:
                match = re.search(r'(?:last\s*update|updated)\s*[:\-]?\s*(.+)', text, flags=re.I)
                if match:
                    cleaned = match.group(1).strip()
                    cleaned = cleaned.split('|')[0].strip()
                    cleaned = cleaned.split('\n')[0].strip()
                    if cleaned:
                        normalized = normalize_date(cleaned)
                        if normalized:
                            return normalized
            parent_text = node.parent.get_text(' ', strip=True) if node.parent else ''
            if parent_text:
                match_parent = re.search(r'(?:last\s*update|updated)\s*[:\-]?\s*(.+)', parent_text, flags=re.I)
                if match_parent:
                    cleaned = match_parent.group(1).strip()
                    cleaned = cleaned.split('|')[0].strip()
                    cleaned = cleaned.split('\n')[0].strip()
                    if cleaned:
                        normalized = normalize_date(cleaned)
                        if normalized:
                            return normalized
        return None

    def get_sort_key(term):
        if term in term_order:
            return term_order.index(term)
        match = re.match(r'(\d+)([mw])', term or '')
        if match:
            num = int(match.group(1))
            unit = match.group(2)
            base = 100 if unit == 'm' else 0
            return base + num
        return 1000

    def format_change(change):
        if change is None:
            return ''
        direction = '→'
        if change > 0:
            direction = '▲'
        elif change < 0:
            direction = '▼'
        return f' {direction} {abs(change):.3f}pp'

    async def fetch_rates():
        html_url = 'https://www.euribor-rates.eu/en/current-euribor-rates'
        source_url = html_url
        last_update = None

        soup = None
        status_html = 0
        try:
            soup, status_html = await fetch_html(html_url, timeout=20)
        except Exception:
            soup = None
            status_html = 0

        if soup and not hasattr(soup, 'find'):
            try:
                soup = BeautifulSoup(soup, 'html.parser')
            except Exception:
                soup = None

        if (not soup) or status_html != 200:
            rendered = {}
            try:
                rendered = await fetch_rendered(
                    html_url,
                    wait_for='body',
                    timeout=30,
                    return_html=True,
                    stealth=True
                )
            except Exception:
                rendered = {}
            html_status = None
            html_content = ''
            if isinstance(rendered, dict):
                html_status = rendered.get('status')
                html_content = rendered.get('html') or rendered.get('text') or ''
            elif isinstance(rendered, (list, tuple)):
                if len(rendered) > 0:
                    html_content = rendered[0] or ''
                if len(rendered) > 1 and isinstance(rendered[1], int):
                    html_status = rendered[1]
            elif isinstance(rendered, str):
                html_content = rendered
            if not html_content or (html_status is not None and html_status != 200):
                error_status = status_html or html_status or 0
                return [], last_update, source_url, f'Не удалось получить данные по Euribor (HTTP {error_status}).'
            try:
                soup = BeautifulSoup(html_content, 'html.parser')
            except Exception:
                return [], last_update, source_url, 'Не удалось обработать HTML с сайта Euribor.'

        rates = []
        seen_terms = set()
        tables = soup.find_all('table') if soup else []
        for table in tables:
            table_text = table.get_text(' ', strip=True).lower()
            if not any(keyword in table_text for keyword in ['euribor', 'interest', 'rate']):
                continue
            for row in table.find_all('tr'):
                cells = row.find_all(['td', 'th'])
                if len(cells) < 2:
                    continue
                if all(c.name == 'th' for c in cells):
                    continue
                first_text = cells[0].get_text(' ', strip=True)
                first_text_lower = first_text.lower()
                if first_text_lower in {'term', 'tenor', 'period', 'maturity', ''}:
                    continue
                norm_term = detect_term(first_text)
                if not norm_term:
                    norm_term = detect_term(first_text_lower)
                if not norm_term or norm_term in seen_terms:
                    continue
                rate_val = parse_rate(cells[1].get_text(' ', strip=True))
                if rate_val is None:
                    continue
                change_val = None
                if len(cells) > 2:
                    prev_val = parse_rate(cells[2].get_text(' ', strip=True))
                    if prev_val is not None:
                        change_val = rate_val - prev_val
                label = term_display.get(norm_term, first_text.strip() or norm_term.upper())
                rates.append({
                    'term': norm_term,
                    'label': label,
                    'rate': rate_val,
                    'change': change_val,
                    'raw_label': first_text
                })
                seen_terms.add(norm_term)

        if not rates and soup:
            attr_candidates = ['data-term', 'data-tenor', 'data-period', 'data-maturity']
            for tag in soup.find_all(True):
                term_attr = None
                for attr in attr_candidates:
                    attr_value = tag.get(attr)
                    if attr_value:
                        term_attr = str(attr_value)
                        break
                if not term_attr:
                    continue
                norm_term = detect_term(term_attr)
                if not norm_term:
                    norm_term = detect_term(tag.get_text(' ', strip=True))
                if not norm_term or norm_term in seen_terms:
                    continue
                rate_text = tag.get('data-rate') or tag.get('data-value') or tag.get('data-percent')
                if not rate_text:
                    rate_text = tag.get_text(' ', strip=True)
                rate_val = parse_rate(rate_text)
                if rate_val is None:
                    continue
                change_val = None
                change_attr = tag.get('data-change') or tag.get('data-delta')
                if change_attr:
                    change_val = parse_rate(change_attr)
                label = term_display.get(norm_term, term_attr.strip() or norm_term.upper())
                rates.append({
                    'term': norm_term,
                    'label': label,
                    'rate': rate_val,
                    'change': change_val,
                    'raw_label': term_attr
                })
                seen_terms.add(norm_term)

        if soup and not last_update:
            last_update = extract_last_update(soup)

        return rates, last_update, source_url, None

    try:
        selected_term = None
        for token in args_lower:
            term = detect_term(token)
            if term:
                selected_term = term
                break
        if not selected_term:
            selected_term = detect_term(combined_query)
        request_all = any(word in combined_query for word in ['all', 'все', 'полный', 'полностью'])
        rates, last_update, source_url, fetch_error = await fetch_rates()
        if not rates:
            if fetch_error:
                return fetch_error
            return 'Не удалось получить актуальные ставки Euribor. Попробуйте позже.'
        sorted_rates = sorted(rates, key=lambda item: get_sort_key(item.get('term')))
        if selected_term:
            to_show = [item for item in sorted_rates if item.get('term') == selected_term]
            if not to_show:
                available_terms = ', '.join({term_display.get(item.get('term'), item.get('raw_label', item.get('term'))) for item in sorted_rates})
                return f'Указанный срок не найден. Доступные сроки: {available_terms}'
        else:
            if len(sorted_rates) > 8 and not request_all:
                preferred_terms = ['overnight', '1w', '1m', '3m', '6m', '12m']
                to_show = [item for item in sorted_rates if item.get('term') in preferred_terms]
                if not to_show:
                    to_show = sorted_rates[:8]
            else:
                to_show = sorted_rates

        language = (context.get('language') or '').lower()
        is_voice = bool(language)
        if is_voice and to_show:
            is_ru = language.startswith('ru')
            ru_term = {
                'overnight': 'овернайт',
                '1w': '1 неделя', '2w': '2 недели', '3w': '3 недели', '4w': '4 недели',
                '1m': '1 месяц', '2m': '2 месяца', '3m': '3 месяца', '4m': '4 месяца',
                '5m': '5 месяцев', '6m': '6 месяцев', '7m': '7 месяцев', '8m': '8 месяцев',
                '9m': '9 месяцев', '10m': '10 месяцев', '11m': '11 месяцев', '12m': '12 месяцев',
            }
            en_term = {
                'overnight': 'overnight',
                '1w': '1 week', '2w': '2 weeks', '3w': '3 weeks', '4w': '4 weeks',
                '1m': '1 month', '2m': '2 months', '3m': '3 months', '4m': '4 months',
                '5m': '5 months', '6m': '6 months', '7m': '7 months', '8m': '8 months',
                '9m': '9 months', '10m': '10 months', '11m': '11 months', '12m': '12 months',
            }
            def fmt_rate(r):
                return f'{r:.2f}'.replace('.', ',') if is_ru else f'{r:.2f}'
            if selected_term:
                item = to_show[0]
                term_label = ru_term.get(item['term'], item['term']) if is_ru else en_term.get(item['term'], item['term'])
                if is_ru:
                    return f'Еврибор {term_label}: {fmt_rate(item["rate"])} процента.'
                return f'{term_label.capitalize()} Euribor is {fmt_rate(item["rate"])} percent.'
            voice_terms = ['1m', '3m', '6m', '12m']
            picks = [it for it in to_show if it.get('term') in voice_terms]
            if not picks:
                picks = to_show[:4]
            if is_ru:
                parts = [f'{ru_term.get(it["term"], it["term"])} {fmt_rate(it["rate"])}' for it in picks]
                return 'Еврибор: ' + ', '.join(parts) + ' процентов.'
            parts = [f'{en_term.get(it["term"], it["term"])} {fmt_rate(it["rate"])}' for it in picks]
            return 'Euribor rates: ' + ', '.join(parts) + ' percent.'

        header = 'Ставки Euribor / Euribor interest rates'
        if selected_term and to_show:
            label = term_display.get(selected_term, to_show[0].get('raw_label', selected_term))
            header = f'Euribor {label}'
        details = []
        for item in to_show:
            rate_value = item.get('rate')
            change_value = item.get('change')
            label = item.get('label', item.get('raw_label', item.get('term')))
            rate_text = f'{rate_value:.3f}%'
            change_text = format_change(change_value)
            details.append(f'- {label}: {rate_text}{change_text}')
        if not details:
            return 'Доступных данных по Euribor не найдено.'
        lines = [header]
        if last_update:
            lines.append(f'Обновлено / Updated: {last_update}')
        lines.extend(details)
        if fetch_error:
            lines.append(fetch_error)
        lines.append(f'Источник / Source: {source_url}')
        return '\n'.join(lines)
    except Exception as exc:
        return f'Произошла непредвиденная ошибка: {exc}'