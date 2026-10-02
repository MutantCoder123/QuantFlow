"""A stock's news, as one number the conviction score can read.

Each headline about the stock is rated once by the local model --
BULLISH (+1), BEARISH (-1) or NEUTRAL (0) for that company specifically;
market-wide or unrelated news is NEUTRAL -- and the ratings are combined,
newer headlines counting more (half-life NEWS_HALF_LIFE_H hours).

    news_score(articles) -> float in [-1, 1], or None when there is no rated news.

Until 2026-10-01 the score's news part was a hard-coded 0 shown as "no input":
headlines reached only the AI judge, as text. This makes news measurable
first; whether it COUNTS in the composite is a policy decision
(conviction.news_in_score), taken once scripts/fit_score.py shows it predicts.
"""
from __future__ import annotations

import re
import time

NEWS_HALF_LIFE_H = 24.0
_WORD = re.compile(r"\b(BULLISH|BEARISH|NEUTRAL)\b", re.IGNORECASE)
_VALUE = {"BULLISH": 1, "BEARISH": -1, "NEUTRAL": 0}


def parse_sentiment(text: str | None) -> int | None:
    """+1 / -1 / 0 from the model's reply; None if it gave no rating."""
    m = _WORD.search(str(text or ""))
    return _VALUE[m.group(1).upper()] if m else None


def news_score(articles: list | None, now: float | None = None,
               half_life_h: float = NEWS_HALF_LIFE_H) -> float | None:
    """Recency-weighted mean of the rated headlines. Articles without a
    rating are skipped; with no rated article at all the answer is None
    (unknown), never 0 (which would claim the news was read as neutral)."""
    now = time.time() if now is None else now
    num = den = 0.0
    for a in articles or []:
        s = a.get("sentiment") if isinstance(a, dict) else None
        if s not in (-1, 0, 1):
            continue
        ts = a.get("published_ts")
        age_h = max(0.0, (now - float(ts)) / 3600.0) if isinstance(ts, (int, float)) else 0.0
        w = 0.5 ** (age_h / half_life_h)
        num += w * s
        den += w
    return round(num / den, 3) if den > 0 else None
