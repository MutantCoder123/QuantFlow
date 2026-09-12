"""Screener scoring fixes (Task 5.5, fixes §A-15) and the bounded watchlist
selection (improved §4.9).

No live API access needed: these exercise the pure scoring/selection
functions directly against synthetic inputs.
"""
import pandas as pd

from screener_engine import _score_volume_shock, _zscale, select_bounded_watchlist


def _flat_volume_df(last_volume, n=30, base_volume=1000):
    """A synthetic daily-bar frame with a flat volume history except the
    last row, which is set to `last_volume`.

    NOTE: pandas' trailing `rolling(20).mean()` INCLUDES the current (last)
    row in its own window, so the effective ratio is not simply
    `last_volume / base_volume` -- the last row pulls its own average up or
    down. Callers that need an exact ratio should use `_df_with_ratio`
    instead; this helper is only exact when `last_volume == base_volume`
    (ratio == 1.0) or when only the sign of `ratio - 1` matters.
    """
    volumes = [base_volume] * (n - 1) + [last_volume]
    return pd.DataFrame({"volume": volumes})


def _df_with_ratio(ratio, pad=10):
    """A synthetic volume frame whose last-20-row rolling mean (which
    includes the last row itself) produces EXACTLY `ratio` at the last row,
    using integer volumes chosen to divide evenly.

    Derivation: with 19 rows of `a` followed by one row of `x`, the trailing
    mean of the last 20 rows is (19a + x) / 20, and x / mean == ratio
    resolves to x = 19*ratio*a / (20 - ratio). Choosing a = (20 - ratio) and
    x = 19*ratio keeps both integers whenever `ratio` is one.
    """
    a = 20 - ratio
    x = 19 * ratio
    last20 = [a] * 19 + [x]
    volumes = [a] * pad + last20
    return pd.DataFrame({"volume": volumes})


# ---------------------------------------------------------------------------
# Steps 1-2: volume-shock term
# ---------------------------------------------------------------------------

def test_average_volume_scores_zero_shock():
    df = _flat_volume_df(last_volume=1000)  # ratio == 1.0
    assert _score_volume_shock(df) == 0


def test_three_x_average_volume_scores_full_marks():
    df = _df_with_ratio(3.0)
    assert _score_volume_shock(df) == 35


def test_below_average_volume_never_scores_negative():
    df = _flat_volume_df(last_volume=200)  # ratio well below 1.0
    assert _score_volume_shock(df) == 0


def test_too_few_bars_scores_zero():
    df = _flat_volume_df(last_volume=5000, n=10)  # < 26 rows
    assert _score_volume_shock(df) == 0


def test_ratio_between_one_and_three_scales_linearly():
    df = _df_with_ratio(2.0)  # halfway to 3x -> half credit
    assert _score_volume_shock(df) == 17.5


# ---------------------------------------------------------------------------
# Step 3: common z-scale for the three polarity contributions
# ---------------------------------------------------------------------------

def test_zscale_at_exactly_its_own_threshold_is_one():
    assert _zscale(0.005, 0.005) == 1.0
    assert _zscale(-0.002, 0.002) == -1.0


def test_zscale_at_three_times_threshold_is_three():
    assert _zscale(0.015, 0.005) == 3.0
    assert _zscale(15.0, 5.0) == 3.0


def test_zscale_beyond_three_times_threshold_clips_at_three():
    assert _zscale(0.05, 0.005) == 3.0
    assert _zscale(-0.05, 0.005) == -3.0


def test_zscale_puts_the_three_terms_on_a_comparable_range():
    # Old formula: trend_dist*100, macd_dist*1000, raw comp_rs -- macd
    # dwarfs the other two by roughly 10x for realistic inputs. New formula:
    # each expressed as multiples of its own significance threshold, so a
    # merely-significant MACD reading no longer swamps a merely-significant
    # trend/RS reading.
    trend_z = _zscale(0.006, 0.005)   # just past its own 0.5% threshold
    macd_z = _zscale(0.0025, 0.002)   # just past its own 0.2% threshold
    rs_z = _zscale(5.5, 5.0)          # just past its own 5.0 threshold
    for z in (trend_z, macd_z, rs_z):
        assert 1.0 <= z <= 1.5


# ---------------------------------------------------------------------------
# Step 5: bounded watchlist selection
# ---------------------------------------------------------------------------

def _candidate(symbol, score, adv_crore=100.0, token=None):
    return {
        "token": token or f"TOK-{symbol}",
        "symbol": symbol,
        "exchange": "NSE",
        "score": score,
        "adv_crore": adv_crore,
    }


BASE_POLICY = {
    "core": ["RELIANCE", "INFY", "KOTAKBANK"],
    "dynamic_slots": 3,
    "cluster_cap": 2,
    "min_adv_crore": 50,
    "churn_cap": 4,
}

# Every symbol its own cluster unless stated otherwise.
NO_CLUSTERS = {}


def _fake_resolver(symbol):
    return f"RESOLVED-{symbol}"


def test_core_symbols_are_always_included():
    candidates = [_candidate("SAIL", 80)]
    out = select_bounded_watchlist(candidates, [], BASE_POLICY, NO_CLUSTERS,
                                    token_resolver=_fake_resolver)
    out_symbols = {c["symbol"] for c in out}
    assert {"RELIANCE", "INFY", "KOTAKBANK"}.issubset(out_symbols)


def test_core_symbol_not_in_candidates_is_resolved_via_fallback():
    out = select_bounded_watchlist([], [], BASE_POLICY, NO_CLUSTERS,
                                    token_resolver=_fake_resolver)
    reliance = next(c for c in out if c["symbol"] == "RELIANCE")
    assert reliance["token"] == "RESOLVED-RELIANCE"


def test_min_adv_crore_filters_illiquid_candidates():
    candidates = [
        _candidate("SAIL", 90, adv_crore=10),   # below floor of 50
        _candidate("NMDC", 80, adv_crore=60),   # above floor
    ]
    out = select_bounded_watchlist(candidates, [], BASE_POLICY, NO_CLUSTERS,
                                    token_resolver=_fake_resolver)
    dynamic_symbols = {c["symbol"] for c in out} - {"RELIANCE", "INFY", "KOTAKBANK"}
    assert dynamic_symbols == {"NMDC"}


def test_cluster_cap_limits_members_per_cluster():
    candidates = [
        _candidate("ADANIENT", 90),
        _candidate("ADANIGREEN", 85),
        _candidate("ADANIPORTS", 80),   # 3rd Adani name -- should be skipped
        _candidate("SAIL", 70),
    ]
    clusters = {"ADANIENT": "ADANI", "ADANIGREEN": "ADANI",
                "ADANIPORTS": "ADANI", "SAIL": "SAIL"}
    policy = dict(BASE_POLICY, dynamic_slots=3, cluster_cap=2)
    out = select_bounded_watchlist(candidates, [], policy, clusters,
                                    token_resolver=_fake_resolver)
    dynamic_symbols = [c["symbol"] for c in out
                       if c["symbol"] not in ("RELIANCE", "INFY", "KOTAKBANK")]
    assert dynamic_symbols.count("ADANIENT") + dynamic_symbols.count("ADANIGREEN") \
        + dynamic_symbols.count("ADANIPORTS") == 2
    assert "SAIL" in dynamic_symbols   # the cluster cap made room for it


def test_churn_cap_reverts_excess_swaps_to_previous_watchlist():
    # Previous dynamic watchlist (core excluded): 3 symbols.
    previous_symbols = ["OLD1", "OLD2", "OLD3"]
    # This run's candidates propose replacing ALL three with new names --
    # a churn of 6 (3 adds + 3 drops), but churn_cap only allows 1.
    candidates = [
        _candidate("NEW1", 100),
        _candidate("NEW2", 90),
        _candidate("NEW3", 80),
    ]
    policy = dict(BASE_POLICY, dynamic_slots=3, cluster_cap=None, churn_cap=1)
    out = select_bounded_watchlist(candidates, previous_symbols, policy, NO_CLUSTERS,
                                    token_resolver=_fake_resolver)
    dynamic_symbols = {c["symbol"] for c in out
                       if c["symbol"] not in ("RELIANCE", "INFY", "KOTAKBANK")}
    added = dynamic_symbols - set(previous_symbols)
    dropped = set(previous_symbols) - dynamic_symbols
    # Exactly one swap (one add + one drop) is allowed through.
    assert len(added) == 1
    assert len(dropped) == 1
    # The single add let through must be the highest-scored candidate.
    assert added == {"NEW1"}


def test_churn_within_cap_is_not_reverted():
    previous_symbols = ["OLD1"]
    candidates = [_candidate("NEW1", 100)]
    policy = dict(BASE_POLICY, dynamic_slots=1, cluster_cap=None, churn_cap=4)
    out = select_bounded_watchlist(candidates, previous_symbols, policy, NO_CLUSTERS,
                                    token_resolver=_fake_resolver)
    dynamic_symbols = {c["symbol"] for c in out
                       if c["symbol"] not in ("RELIANCE", "INFY", "KOTAKBANK")}
    assert dynamic_symbols == {"NEW1"}
