from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

_config = {
    "seed_keywords": [],
    "noise_terms": set(),
    "enabled": True,
}

MAX_KEYWORDS_PER_CALL = 4


def configure(seed_keywords: list[str], noise_terms: list[str], enabled: bool = True):
    _config["seed_keywords"] = list(seed_keywords)
    _config["noise_terms"] = set(noise_terms)
    _config["enabled"] = enabled


def _is_noise(query: str) -> bool:
    q = query.lower()
    if any(noise in q for noise in _config["noise_terms"]):
        return True
    ascii_chars = sum(1 for c in q if ord(c) < 128)
    if len(q) > 0 and ascii_chars / len(q) < 0.5:
        return True
    return False


def _pick_keywords() -> list[str]:
    """Pick a rotating subset of seed keywords based on current hour."""
    keywords = _config["seed_keywords"]
    if not keywords:
        return []
    hour = int(time.time() // 3600)
    start = (hour * MAX_KEYWORDS_PER_CALL) % len(keywords)
    picked = []
    for i in range(MAX_KEYWORDS_PER_CALL):
        picked.append(keywords[(start + i) % len(keywords)])
    return picked


def _fetch_pytrends(keywords: list[str]) -> tuple[list, list]:
    """Query YouTube trends via pytrends for given keywords."""
    from pytrends.request import TrendReq

    pytrends = TrendReq(hl="en-US", tz=300)
    rising_all = []
    top_all = []

    for kw in keywords:
        try:
            pytrends.build_payload(
                kw_list=[kw],
                timeframe="now 1-d",
                gprop="youtube",
            )
            related = pytrends.related_queries()
            data = related[kw]

            if data["rising"] is not None and len(data["rising"]) > 0:
                for _, row in data["rising"].iterrows():
                    query = row["query"]
                    value = row["value"]
                    if not _is_noise(query):
                        rising_all.append((query, int(value), kw))

            if data["top"] is not None and len(data["top"]) > 0:
                for _, row in data["top"].iterrows():
                    query = row["query"]
                    value = row["value"]
                    if not _is_noise(query):
                        top_all.append((query, int(value), kw))

            time.sleep(1)  # Gentle delay between requests

        except Exception:
            continue

    return rising_all, top_all


def search_trends() -> str:
    """Search YouTube for rising/top queries related to this block's
    seed keywords in the past 24 hours. This is the primary,
    niche-specific trend signal.

    This can return empty or rate-limited on some calls since it depends
    on an unofficial Google Trends endpoint. If this happens, call
    web_trends_search() instead to search the live web for current
    trending news in your content niche."""
    log.info("[TRENDS] search_trends() called | enabled=%s | seeds=%d",
             _config["enabled"], len(_config["seed_keywords"]))
    if not _config["enabled"] or not _config["seed_keywords"]:
        log.info("[TRENDS] Skipped — trends not configured for this block")
        return (
            "YouTube trends are not configured for this content block. "
            "Call web_trends_search() to search the live web for current "
            "trending topics in your niche instead."
        )
    keywords = _pick_keywords()
    log.info("[TRENDS] Querying pytrends | keywords=%s", keywords)
    t0 = time.time()
    try:
        rising, top = _fetch_pytrends(keywords)
    except Exception as e:
        log.warning("[TRENDS] pytrends failed | error=%s | elapsed=%.1fs", e, time.time() - t0)
        return (
            f"YouTube trends unavailable right now ({e}). "
            "Call web_trends_search() to search the web for current "
            "trending topics in your niche instead."
        )

    # Deduplicate, keeping the highest value per query
    def dedup(items):
        seen = {}
        for query, value, seed in items:
            if query not in seen or value > seen[query][0]:
                seen[query] = (value, seed)
        result = [(q, v, s) for q, (v, s) in seen.items()]
        result.sort(key=lambda x: x[1], reverse=True)
        return result

    rising = dedup(rising)
    top = dedup(top)

    log.info("[TRENDS] Results | rising=%d | top=%d | elapsed=%.1fs",
             len(rising), len(top), time.time() - t0)
    if not rising and not top:
        return (
            f"No YouTube trend signal found for {', '.join(keywords)} "
            "right now (likely rate-limited). Call web_trends_search() "
            "to search the web for current trending topics instead."
        )

    lines = []
    if rising:
        lines.append(
            f"RISING on YouTube right now (searched: {', '.join(keywords)}):"
        )
        for query, value, seed in rising[:10]:
            lines.append(f'  ↑{value}%  "{query}"  (via: {seed})')

    if top:
        lines.append("")
        lines.append(
            f"TOP on YouTube right now (searched: {', '.join(keywords)}):"
        )
        for query, value, seed in top[:8]:
            lines.append(f'  [{value}]  "{query}"  (via: {seed})')

    return "\n".join(lines)
