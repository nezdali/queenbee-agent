"""
Pre-LLM keyword auto-dispatch matcher.

Picks the best tool for a free-text prompt by scoring against tool
trigger_keywords (IDF-style) with description-word overlap as tiebreak.

Used by the local-LLM (`gemma+`/`gemma++`) path to fetch live data up front
and inject it into the conversation so a small model only has to summarize
the answer rather than pick the right tool itself.
"""

from __future__ import annotations

from typing import Iterable, Sequence


# Common English stopwords + question/connector words that should not count as
# evidence for or against any particular tool.
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
    "and", "or", "but", "if", "then", "so", "than", "that", "this",
    "it", "its", "i", "you", "he", "she", "we", "they", "me", "us",
    "my", "your", "his", "her", "our", "their",
    "what", "whats", "what's", "whose", "which", "who", "whom",
    "when", "where", "why", "how", "do", "does", "did", "have", "has", "had",
    "can", "could", "would", "should", "will", "shall", "may", "might",
    "please", "tell", "show", "give", "get", "find", "search",
    "today", "now",
})


def tokenize(text: str) -> set[str]:
    """Lowercase, strip punctuation, drop stopwords and empties."""
    raw = {w.lower().strip(".,!?:;\"'`") for w in (text or "").split()}
    return {w for w in raw if w and w not in STOPWORDS}


def _description_overlap(prompt_words: set[str], description: str, hits: set[str]) -> int:
    """Count distinct prompt words (excluding already-matched triggers) that
    appear in the description, allowing prefix/substring matches so plurals
    and stems work (e.g. 'price' matches 'prices').
    """
    desc_words = {w.lower().strip(".,!?:;\"'`") for w in (description or "").split()}
    extra = (prompt_words - hits) - STOPWORDS
    overlap = 0
    for pw in extra:
        if len(pw) < 3:
            continue
        for dw in desc_words:
            if not dw or len(dw) < 3:
                continue
            if pw == dw or pw.startswith(dw) or dw.startswith(pw):
                overlap += 1
                break
    return overlap


def score_tools(prompt: str, tools: Sequence) -> list[tuple]:
    """Return scored candidates as a list of tuples sorted best-first.

    Each tuple is `(score, desc_overlap, hit_count, tool)`. `tools` items
    must expose `trigger_keywords: Iterable[str]` and `description: str`
    attributes (duck-typed; works with `ToolMeta` and test stubs).
    """
    prompt_words = tokenize(prompt)
    if not prompt_words or not tools:
        return []

    # IDF-style frequency: rare keywords win over generic ones.
    kw_freq: dict[str, int] = {}
    for a in tools:
        for kw in {k.lower() for k in (getattr(a, "trigger_keywords", None) or [])}:
            kw_freq[kw] = kw_freq.get(kw, 0) + 1

    scored: list[tuple] = []
    for a in tools:
        kws = {k.lower() for k in (getattr(a, "trigger_keywords", None) or [])}
        hits = kws & prompt_words
        if not hits:
            continue
        score = sum(1.0 / max(kw_freq.get(k, 1), 1) for k in hits)
        overlap = _description_overlap(prompt_words, getattr(a, "description", "") or "", hits)
        scored.append((score, overlap, len(hits), a))

    scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    return scored


def pick_best_tool(prompt: str, tools: Sequence):
    """Return the single best-matching tool, or None."""
    scored = score_tools(prompt, tools)
    return scored[0][3] if scored else None
