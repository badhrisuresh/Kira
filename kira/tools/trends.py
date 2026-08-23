import time

# Rotate through these in small batches to avoid rate limits.
# These are the highest-signal seed keywords for space content on YouTube.
SEED_KEYWORDS = [
    "black hole",
    "asteroid",
    "rocket launch",
    "james webb",
    "supernova",
    "exoplanet",
    "mars planet",
    "meteorite",
    "nebula",
    "dark matter",
    "space exploration",
    "moon landing",
    "solar system",
]

# Max keywords to query per call (keeps us under rate limits)
MAX_KEYWORDS_PER_CALL = 4

# Noise terms to filter out non-space results
NOISE_TERMS = {
    # Entertainment / music
    "samsung", "mario", "guardians", "animal planet", "fishing",
    "boboiboy", "fortnite", "minecraft", "roblox", "dj", "song",
    "lyrics", "music", "karaoke", "champagne", "oasis", "remix",
    "porcupine tree", "taylor swift", "soundgarden", "muse",
    "sivert", "concert", "album", "band", "playlist",
    "killing joke", "sun ra", "fang",
    # Games / gaming
    "game", "palworld", "factorio", "kerbal", "stellaris",
    "no man", "destiny 2", "starfield", "warframe", "gacha",
    "mini game", "beat phin", "blox fruit", "elden ring", "rpg",
    "staff in", "koisuru", "anime",
    # Products / brands
    "soundcore", "projector", "honda", "tesla car", "toy", "toys",
    "price", "buy", "review", "unboxing", "marker",
    # Movies / TV shows
    "trailer", "movie", "film", "series", "season", "episode",
    "explained", "recap", "netflix", "disney", "marvel",
    "asteroid city", "dark matter apple", "dark matter tv",
    # Education / school
    "powerpoint", "project", "school", "kid", "kids", "class",
    "drawing",
    # Non-English noise
    "imagens", "saurini", "viaggi",
}


def _is_noise(query: str) -> bool:
    q = query.lower()
    if any(noise in q for noise in NOISE_TERMS):
        return True
    ascii_chars = sum(1 for c in q if ord(c) < 128)
    if len(q) > 0 and ascii_chars / len(q) < 0.5:
        return True
    return False


def _pick_keywords() -> list[str]:
    """Pick a rotating subset of seed keywords based on current hour."""
    hour = int(time.time() // 3600)
    start = (hour * MAX_KEYWORDS_PER_CALL) % len(SEED_KEYWORDS)
    picked = []
    for i in range(MAX_KEYWORDS_PER_CALL):
        picked.append(SEED_KEYWORDS[(start + i) % len(SEED_KEYWORDS)])
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
    """Search YouTube for rising/top queries related to space and cosmos
    keywords (black holes, asteroids, rocket launches, etc.) in the past
    24 hours. This is the primary, niche-specific trend signal.

    This can return empty or rate-limited on some calls since it depends
    on an unofficial Google Trends endpoint. If this happens, call
    web_trends_search() instead to get current space news via live web
    search."""
    keywords = _pick_keywords()
    try:
        rising, top = _fetch_pytrends(keywords)
    except Exception as e:
        return (
            f"YouTube trends unavailable right now ({e}). "
            "Call web_trends_search() to search the web for current "
            "trending space news instead."
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

    if not rising and not top:
        return (
            f"No YouTube trend signal found for {', '.join(keywords)} "
            "right now (likely rate-limited). Call web_trends_search() "
            "to search the web for current trending space news instead."
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
