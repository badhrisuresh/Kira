import re
import time

from .trends import _is_noise


FALLBACK_HASHTAGS = ["#Space", "#Cosmos", "#Universe"]


def _to_hashtag(query: str) -> str:
    """Convert a trending query string into a hashtag.

    'black hole collision' -> '#BlackHoleCollision'
    """
    words = re.sub(r"[^a-zA-Z0-9\s]", "", query).split()
    return "#" + "".join(w.capitalize() for w in words)


def get_trending_hashtags(topic: str) -> list[str]:
    """Fetch 3 trending hashtags related to a topic from YouTube trends.

    Returns a list like ['#BlackHole', '#JamesWebb', '#Supernova'].
    Falls back to generic space hashtags on failure or rate-limiting.
    """
    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="en-US", tz=300)
        pytrends.build_payload(
            kw_list=[topic],
            timeframe="now 7-d",
            gprop="youtube",
        )
        related = pytrends.related_queries()
        data = related[topic]

        candidates = []

        for kind in ("rising", "top"):
            if data[kind] is not None and len(data[kind]) > 0:
                for _, row in data[kind].iterrows():
                    query = row["query"]
                    value = int(row["value"])
                    if not _is_noise(query) and query.lower() != topic.lower():
                        candidates.append((query, value))

        candidates.sort(key=lambda x: x[1], reverse=True)

        seen = set()
        hashtags = []
        for query, _ in candidates:
            tag = _to_hashtag(query)
            if tag.lower() not in seen:
                seen.add(tag.lower())
                hashtags.append(tag)
            if len(hashtags) == 3:
                break

        if hashtags:
            return hashtags

    except Exception:
        pass

    return FALLBACK_HASHTAGS
