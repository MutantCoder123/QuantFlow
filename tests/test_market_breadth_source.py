"""Market-breadth source selection (Task 5.6, Step 4).

The card used to be labelled "NSE A/D Ratio" while showing advances/declines
over ~27 self-selected watchlist symbols. Now the real NIFTY-50 value wins
when it is fresh, the proxy is the fallback, and the payload always says
which one is on screen.
"""
import datetime
from zoneinfo import ZoneInfo

from api_server import AD_RATIO_MAX_AGE_S, select_ad_ratio

_IST = ZoneInfo("Asia/Kolkata")


def _ts(seconds_ago: float) -> str:
    return (datetime.datetime.now(_IST)
            - datetime.timedelta(seconds=seconds_ago)).isoformat()


def test_fresh_real_value_wins():
    val, src = select_ad_ratio({"ad_ratio": 1.42, "ad_ratio_ts": _ts(60)}, 0.8)
    assert (val, src) == (1.42, "NIFTY_50")


def test_stale_real_value_falls_back_to_the_proxy():
    val, src = select_ad_ratio(
        {"ad_ratio": 1.42, "ad_ratio_ts": _ts(AD_RATIO_MAX_AGE_S + 60)}, 0.8)
    assert (val, src) == (0.8, "WATCHLIST_PROXY")


def test_missing_timestamp_falls_back_even_when_a_value_exists():
    """A stored ad_ratio with no fetch time cannot be shown as today's NSE breadth."""
    val, src = select_ad_ratio({"ad_ratio": 1.42}, 0.8)
    assert src == "WATCHLIST_PROXY"


def test_absent_real_value_falls_back():
    val, src = select_ad_ratio({}, 0.8)
    assert (val, src) == (0.8, "WATCHLIST_PROXY")


def test_unparseable_timestamp_falls_back():
    val, src = select_ad_ratio({"ad_ratio": 1.42, "ad_ratio_ts": "not-a-time"}, 0.8)
    assert src == "WATCHLIST_PROXY"


def test_naive_timestamp_is_read_as_ist():
    naive = datetime.datetime.now(_IST).replace(tzinfo=None).isoformat()
    val, src = select_ad_ratio({"ad_ratio": 1.1, "ad_ratio_ts": naive}, 0.8)
    assert (val, src) == (1.1, "NIFTY_50")
