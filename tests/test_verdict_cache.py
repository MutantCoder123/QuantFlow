"""Judge verdict reuse: when the autonomous loop asks the AI again (FUTURE_UPDATES 2.7 A)."""
from core.verdict_cache import MAX_SCORE_MOVE, REUSE_S, VerdictCache


def _c():
    c = VerdictCache()
    c.put("SAIL", "LONG", 0.40, "TREND_EXPANSION", 0, "{verdict}", now=1000.0)
    return c


def test_the_same_setup_within_30_min_is_reused():
    assert _c().get("SAIL", "LONG", 0.45, "TREND_EXPANSION", 0, now=1000.0 + REUSE_S - 1) == "{verdict}"


def test_asks_again_after_30_min():
    assert _c().get("SAIL", "LONG", 0.40, "TREND_EXPANSION", 0, now=1000.0 + REUSE_S) is None


def test_asks_again_when_the_score_moved():
    assert _c().get("SAIL", "LONG", 0.40 + MAX_SCORE_MOVE, "TREND_EXPANSION", 0, now=1100.0) is None


def test_asks_again_when_the_regime_changed():
    assert _c().get("SAIL", "LONG", 0.40, "RANGE_BOUND_CHOP", 0, now=1100.0) is None


def test_asks_again_after_a_position_closed():
    assert _c().get("SAIL", "LONG", 0.40, "TREND_EXPANSION", 1, now=1100.0) is None


def test_the_other_side_or_another_stock_is_a_new_question():
    c = _c()
    assert c.get("SAIL", "SHORT", 0.40, "TREND_EXPANSION", 0, now=1100.0) is None
    assert c.get("NMDC", "LONG", 0.40, "TREND_EXPANSION", 0, now=1100.0) is None
