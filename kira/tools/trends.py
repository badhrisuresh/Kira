from __future__ import annotations

import logging
import random
import time

import google.genai as genai
from google.genai import types as genai_types

log = logging.getLogger(__name__)

_config: dict = {
    "seed_keywords": [],
    "noise_terms": set(),
    "niche_description": "",
    "enabled": True,
}

_genai_client: genai.Client | None = None

MAX_RETRIES = 3


def configure(
    seed_keywords: list[str],
    noise_terms: list[str],
    niche_description: str = "",
    enabled: bool = True,
):
    _config["seed_keywords"] = list(seed_keywords)
    _config["noise_terms"] = {t.lower() for t in noise_terms}
    _config["niche_description"] = niche_description
    _config["enabled"] = enabled

    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client()


# ── Helpers ──────────────────────────────────────────────────────────


def _is_noise(query: str) -> bool:
    q = query.lower()
    if any(noise in q for noise in _config["noise_terms"]):
        return True
    ascii_chars = sum(1 for c in q if ord(c) < 128)
    if len(q) > 0 and ascii_chars / len(q) < 0.5:
        return True
    return False


def _dedup(items: list[tuple]) -> list[tuple]:
    """Deduplicate (query, value, seed) tuples, keeping highest value."""
    seen: dict = {}
    for query, value, seed in items:
        if query not in seen or value > seen[query][0]:
            seen[query] = (value, seed)
    result = [(q, v, s) for q, (v, s) in seen.items()]
    result.sort(key=lambda x: x[1], reverse=True)
    return result


def _fetch_pytrends_keyword(keyword: str) -> tuple[list, list]:
    """Query pytrends for a single keyword with exponential backoff."""
    from pytrends.request import TrendReq

    for attempt in range(MAX_RETRIES):
        try:
            pytrends = TrendReq(hl="en-US", tz=300)
            pytrends.build_payload(
                kw_list=[keyword],
                timeframe="now 1-d",
                gprop="youtube",
            )
            related = pytrends.related_queries()
            data = related[keyword]

            rising = []
            top = []

            if data["rising"] is not None and len(data["rising"]) > 0:
                for _, row in data["rising"].iterrows():
                    q = row["query"]
                    v = int(row["value"])
                    if not _is_noise(q):
                        rising.append((q, v, keyword))

            if data["top"] is not None and len(data["top"]) > 0:
                for _, row in data["top"].iterrows():
                    q = row["query"]
                    v = int(row["value"])
                    if not _is_noise(q):
                        top.append((q, v, keyword))

            return rising, top

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)
                log.warning(
                    "[GOOGLE_TRENDS] Retry %d/%d for '%s': %s (backoff %.1fs)",
                    attempt + 1, MAX_RETRIES, keyword, e, wait,
                )
                time.sleep(wait)
            else:
                log.warning(
                    "[GOOGLE_TRENDS] All %d attempts failed for '%s': %s",
                    MAX_RETRIES, keyword, e,
                )
                return [], []


# ── Tool 1: search_youtube_trends ────────────────────────────────────


def search_youtube_trends() -> str:
    """Search for currently trending and viral YouTube videos in this
    content niche using live web search. Returns titles, channels,
    view counts, and why each video is trending.

    Call this FIRST to discover what topics are hot right now.
    Use the results to form keywords for search_google_trends()."""
    niche = _config["niche_description"] or "space, astronomy, and cosmos"
    seeds = _config["seed_keywords"]
    seed_hint = f" Focus on topics like: {', '.join(seeds[:6])}." if seeds else ""

    prompt = (
        f"What are the most trending and viral YouTube videos about "
        f"{niche} right now (today)? List the top 5-8 with video titles, "
        f"channel names, approximate view counts, and a one-line reason "
        f"why each is trending.{seed_hint} Only include videos published "
        f"in the last few days."
    )

    log.info("[YT_TRENDS] Searching YouTube trends via Gemini+Search | niche=%s", niche)
    t0 = time.time()

    try:
        response = _genai_client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                temperature=0.3,
            ),
        )
        result = response.text.strip()
        log.info(
            "[YT_TRENDS] Success | len=%d | elapsed=%.1fs",
            len(result), time.time() - t0,
        )
        return result
    except Exception as e:
        log.error("[YT_TRENDS] Failed | error=%s | elapsed=%.1fs", e, time.time() - t0)
        return f"YouTube trends search failed: {e}"


# ── Tool 2: search_google_trends ─────────────────────────────────────


def search_google_trends(keywords: list[str]) -> str:
    """Check Google Trends for rising and top YouTube search queries
    related to the given keywords (last 24 hours).

    Call this AFTER search_youtube_trends() — use topics you discovered
    there to form targeted keywords (1-4 keywords, each 1-3 words).

    Args:
        keywords: 1-4 search terms to check trends for, e.g.
                  ["roman telescope", "asteroid impact", "black hole merger"].

    Returns: Rising queries (with growth %) and top queries from
             Google Trends' YouTube-specific data."""
    if not keywords:
        return "No keywords provided. Pass 1-4 keywords to check trends for."

    keywords = keywords[:4]  # Cap at 4
    log.info("[GOOGLE_TRENDS] Querying pytrends | keywords=%s", keywords)
    t0 = time.time()

    all_rising: list = []
    all_top: list = []

    for kw in keywords:
        rising, top = _fetch_pytrends_keyword(kw)
        all_rising.extend(rising)
        all_top.extend(top)
        if kw != keywords[-1]:
            time.sleep(1.5)  # Gentle delay between keywords

    all_rising = _dedup(all_rising)
    all_top = _dedup(all_top)

    elapsed = time.time() - t0
    log.info(
        "[GOOGLE_TRENDS] Results | rising=%d | top=%d | elapsed=%.1fs",
        len(all_rising), len(all_top), elapsed,
    )

    if not all_rising and not all_top:
        return (
            f"No Google Trends data found for {', '.join(keywords)} "
            f"right now (may be rate-limited or niche terms). "
            f"Try broader keywords, or use web_search() to research "
            f"the topic directly."
        )

    lines = []
    if all_rising:
        lines.append(
            f"RISING on YouTube (searched: {', '.join(keywords)}):"
        )
        for query, value, seed in all_rising[:10]:
            lines.append(f'  +{value}%  "{query}"  (via: {seed})')

    if all_top:
        lines.append("")
        lines.append(
            f"TOP on YouTube (searched: {', '.join(keywords)}):"
        )
        for query, value, seed in all_top[:8]:
            lines.append(f'  [{value}]  "{query}"  (via: {seed})')

    return "\n".join(lines)


# ── Tool 3: web_search ───────────────────────────────────────────────


def web_search(query: str) -> str:
    """Search the live web for current information using Google Search.

    Call this to research a specific topic in depth — news articles,
    recent events, scientific discoveries, or any factual information
    the agent needs to create an informed creative brief.

    Args:
        query: A natural-language search query, e.g.
               "Nancy Grace Roman Space Telescope launch date August 2026"
               or "latest James Webb discoveries this week".

    Returns: Summarized search results with sources."""
    if not query or not query.strip():
        return "No query provided. Pass a search query string."

    log.info("[WEB_SEARCH] Searching | query=%s", query[:100])
    t0 = time.time()

    try:
        response = _genai_client.models.generate_content(
            model="gemini-3.5-flash",
            contents=query,
            config=genai_types.GenerateContentConfig(
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                temperature=0.3,
            ),
        )
        result = response.text.strip()
        log.info(
            "[WEB_SEARCH] Success | len=%d | elapsed=%.1fs",
            len(result), time.time() - t0,
        )
        return result
    except Exception as e:
        log.error("[WEB_SEARCH] Failed | error=%s | elapsed=%.1fs", e, time.time() - t0)
        return f"Web search failed: {e}"
