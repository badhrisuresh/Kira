import logging
import re

from .trends import _is_noise as is_noise_filter

logger = logging.getLogger(__name__)

FALLBACK_HASHTAGS = ["#Space", "#Cosmos", "#Universe"]


def _to_hashtag(query: str) -> str:
    words = re.sub(r"[^a-zA-Z0-9\s]", "", query).split()
    if not words:
        return ""
    return "#" + "".join(w.capitalize() for w in words)


def _parse_value(raw) -> int:
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 5000


def get_trending_hashtags(topic: str) -> list[str]:
    """Fetch 3 trending hashtags related to a topic from YouTube trends.

    Returns a list like ['#BlackHole', '#JamesWebb', '#Supernova'].
    Falls back to generic space hashtags on failure or rate-limiting.
    """
    if not topic or not topic.strip():
        return FALLBACK_HASHTAGS

    topic = topic.strip()

    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="en-US", tz=300, timeout=(5, 10))
        pytrends.build_payload(
            kw_list=[topic],
            timeframe="now 7-d",
            gprop="youtube",
        )
        related = pytrends.related_queries()
        data = related.get(topic)
        if data is None:
            return FALLBACK_HASHTAGS

        candidates = []

        for kind in ("rising", "top"):
            if data.get(kind) is not None and len(data[kind]) > 0:
                for _, row in data[kind].iterrows():
                    query = row["query"]
                    value = _parse_value(row["value"])
                    if not is_noise_filter(query) and query.lower() != topic.lower():
                        candidates.append((query, value))

        candidates.sort(key=lambda x: x[1], reverse=True)

        seen = set()
        hashtags = []
        for query, _ in candidates:
            tag = _to_hashtag(query)
            if tag and tag != "#" and tag.lower() not in seen:
                seen.add(tag.lower())
                hashtags.append(tag)
            if len(hashtags) == 3:
                break

        if hashtags:
            return hashtags

    except Exception:
        logger.warning("Trending hashtag fetch failed for topic=%r", topic, exc_info=True)

    return FALLBACK_HASHTAGS
