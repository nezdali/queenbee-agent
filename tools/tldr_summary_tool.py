async def run(context: dict) -> str:
    fetch_text = None
    fetch_html = None
    parse_args = None
    try:
        from tool_utils import fetch_text as imported_fetch_text
        fetch_text = imported_fetch_text
    except Exception as utility_error:
        return f"Unable to load required fetch utilities: {utility_error}"
    try:
        from tool_utils import fetch_html as imported_fetch_html
        fetch_html = imported_fetch_html
    except Exception:
        fetch_html = None
    try:
        from tool_utils import parse_args as imported_parse_args
        parse_args = imported_parse_args
    except Exception:
        parse_args = None
    try:
        from services.llm_service import get_llm_response
    except Exception as service_error:
        return f"Failed to load LLM service: {service_error}"
    try:
        from config import LLM_MODEL as CONFIG_LLM_MODEL
    except Exception:
        CONFIG_LLM_MODEL = None
    import json
    import re

    try:
        def normalize_args(ctx):
            aggregated = []
            if parse_args:
                try:
                    parsed = parse_args(ctx)
                except Exception:
                    parsed = None
                else:
                    if isinstance(parsed, dict):
                        candidate = parsed.get("args")
                        if isinstance(candidate, (list, tuple)):
                            aggregated.extend(str(item) for item in candidate if item is not None)
                        elif isinstance(candidate, str):
                            aggregated.append(candidate)
                    elif isinstance(parsed, (list, tuple)):
                        aggregated.extend(str(item) for item in parsed if item is not None)
                    elif isinstance(parsed, str):
                        aggregated.append(parsed)
            ctx_args = ctx.get("args")
            if isinstance(ctx_args, (list, tuple)):
                aggregated.extend(str(item) for item in ctx_args if item is not None)
            elif isinstance(ctx_args, str):
                aggregated.append(ctx_args)
            raw_input = ctx.get("raw_input")
            if isinstance(raw_input, str):
                tokens = [token for token in raw_input.strip().split() if token]
                if tokens:
                    aggregated.extend(tokens)
            cleaned_list = []
            seen = set()
            for item in aggregated:
                if not isinstance(item, str):
                    continue
                trimmed = item.strip()
                if not trimmed:
                    continue
                lower_trimmed = trimmed.lower()
                if lower_trimmed in {"/tldr", "tldr", "qbtest", "/qbtest"}:
                    continue
                if trimmed not in seen:
                    cleaned_list.append(trimmed)
                    seen.add(trimmed)
            return cleaned_list

        args = normalize_args(context)
        if not args:
            return "Please provide a URL to summarize. Example: /tldr https://example.com"

        def extract_url(parts):
            if not parts:
                return None
            for part in parts:
                try:
                    match = re.search(r"https?://[^\s]+", part, flags=re.IGNORECASE)
                except Exception:
                    match = None
                if match:
                    return match.group(0).strip("()[]{}<>.,;'\"")
            joined = " ".join(parts)
            try:
                match_joined = re.search(r"https?://[^\s]+", joined, flags=re.IGNORECASE)
            except Exception:
                match_joined = None
            if match_joined:
                return match_joined.group(0).strip("()[]{}<>.,;'\"")
            for part in parts:
                cleaned = part.strip("()[]{}<>.,;'\"")
                if re.match(r"^[\w\-\.]+\.[a-z]{2,}(/\S*)?$", cleaned, flags=re.IGNORECASE):
                    return cleaned
            try:
                match_domain = re.search(r"([\w\-]+\.)+[a-z]{2,}(/[^\s]*)?", joined, flags=re.IGNORECASE)
            except Exception:
                match_domain = None
            if match_domain:
                return match_domain.group(0).strip("()[]{}<>.,;'\"")
            return None

        url = extract_url(args)
        if not url:
            return "Please provide a valid URL to summarize. Include the full link (e.g., https://example.com/article)."

        if not re.match(r"^https?://", url, re.IGNORECASE):
            url = "https://" + url

        text_content = ""
        status = 0

        try:
            text_result = await fetch_text(url, timeout=20)
        except Exception:
            text_result = None

        if isinstance(text_result, tuple) and len(text_result) >= 2:
            possible_text, possible_status = text_result[0], text_result[1]
            if possible_text:
                text_content = possible_text
            if possible_status:
                status = possible_status
        elif isinstance(text_result, dict):
            possible_text = text_result.get("text") or text_result.get("content") or ""
            if possible_text:
                text_content = possible_text
            status = text_result.get("status") or status
        elif isinstance(text_result, str):
            text_content = text_result

        if (not text_content or not text_content.strip()) and fetch_html:
            try:
                html_result = await fetch_html(url, timeout=25)
            except Exception:
                html_result = None
            html_text = ""
            html_status = None
            if isinstance(html_result, tuple) and len(html_result) >= 2:
                html_text = html_result[0] or ""
                html_status = html_result[1]
            elif isinstance(html_result, dict):
                html_text = html_result.get("html") or html_result.get("text") or ""
                html_status = html_result.get("status")
            elif isinstance(html_result, str):
                html_text = html_result
            if html_status:
                status = html_status
            if html_text:
                text_content = re.sub(r"<[^>]+>", " ", html_text)

        if not text_content or not text_content.strip():
            if status and status != 200:
                return f"Failed to retrieve content from the URL (status {status})."
            return "Failed to retrieve content from the URL."

        cleaned = re.sub(r"\s+", " ", text_content).strip()
        if not cleaned:
            return "The fetched page did not contain readable text to summarize."

        snippet = cleaned[:6000]

        model = CONFIG_LLM_MODEL or None

        history = [
            {"role": "system", "content": "You are a helpful assistant that summarizes webpage content."},
            {"role": "user", "content": "Summarize the following webpage text into a JSON array of exactly five concise bullet strings. Each string must be under 25 words, focus on distinct key points, and avoid Markdown or numbering. Respond with the JSON array only.\n\nWebpage text:\n" + snippet}
        ]

        try:
            response = await get_llm_response(history, model=model)
        except Exception as llm_error:
            return f"Failed to generate summary: {llm_error}"

        def parse_bullets(text):
            items = []
            if not isinstance(text, str):
                return items
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    for item in parsed:
                        item_str = str(item).strip()
                        if item_str:
                            items.append(item_str)
            except Exception:
                pass
            if items:
                return items
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            cleaned_lines = []
            for line in lines:
                cleaned_line = re.sub(r"^[\-\*\d\.\)\(]+\s*", "", line)
                if cleaned_line:
                    cleaned_lines.append(cleaned_line)
            return cleaned_lines

        bullets = parse_bullets(response)

        if len(bullets) != 5:
            retry_history = history + [
                {"role": "assistant", "content": response},
                {"role": "user", "content": "Return a JSON array of exactly five concise, distinct bullet strings summarizing the same webpage text. Reply with the JSON array only."}
            ]
            try:
                retry_response = await get_llm_response(retry_history, model=model)
            except Exception:
                retry_response = ""
            if retry_response:
                bullets = parse_bullets(retry_response)

        bullets = [b for b in bullets if b]
        if not bullets:
            return "Failed to generate summary."
        if len(bullets) < 5:
            last_bullet = bullets[-1]
            while len(bullets) < 5:
                bullets.append(last_bullet)
        elif len(bullets) > 5:
            bullets = bullets[:5]

        formatted = "\n".join(f"- {bullet}" for bullet in bullets)

        return f"Summary for {url}\n{formatted}"
    except Exception as error:
        return f"An unexpected error occurred: {error}"