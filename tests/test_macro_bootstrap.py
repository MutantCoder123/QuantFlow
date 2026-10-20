"""52-week options baselines: days without options are missing, not zero,
and are never filled backward from the first real day."""
import numpy as np
import pandas as pd
import pytest

from scripts import macro_bootstrap as mb


def daily(n_days, options_from, rng=np.random.default_rng(1)):
    """n_days of prices; options figures only from row `options_from` on."""
    d = pd.DataFrame({"Date": pd.bdate_range("2025-06-02", periods=n_days),
                      "Open": 100.0, "High": 101.0, "Low": 99.0,
                      "Close": 100 + rng.normal(0, 1, n_days).cumsum(), "Volume": 1e6,
                      "EOD_PCR": 0.0, "EOD_MAX_PAIN": 0.0, "EOD_IV": np.nan, "Total_OI": 0.0})
    k = slice(options_from, None)
    m = n_days - options_from
    d.loc[k, "EOD_PCR"] = np.linspace(0.5, 1.5, m)
    d.loc[k, "EOD_IV"] = np.linspace(20, 40, m)
    d.loc[k, "EOD_MAX_PAIN"] = 100.0
    d.loc[k, "Total_OI"] = np.linspace(1e6, 2e6, m)
    return d


def run(df, tmp_path):
    p = tmp_path / "X_1D.parquet"
    df.to_parquet(p, index=False)
    return mb.process_file(str(p))


def test_a_stock_new_to_options_gets_no_52_week_ranking(tmp_path):
    m = run(daily(300, options_from=297), tmp_path)             # 3 options days (UJJIVANSFB 2026-10-05)
    assert m["volatility_edge_52w"]["iv_percentile_52w"] is None
    assert m["options_positioning_52w"]["pcr_percentile_52w"] is None
    assert m["options_positioning_52w"]["oi_volume_shock_52w_z"] is None
    assert m["structural_liquidity_5y"]["volume_poc_price"] > 0          # prices still used


def test_the_ranking_uses_only_real_options_days(tmp_path):
    m = run(daily(300, options_from=300 - mb.MIN_OPTION_DAYS), tmp_path)
    # rising IV/PCR over the real days only: today is the highest of them
    assert m["volatility_edge_52w"]["iv_percentile_52w"] == pytest.approx(100.0)
    assert m["volatility_edge_52w"]["iv_52w_low"] == pytest.approx(20.0)  # not a back-filled copy
    assert m["options_positioning_52w"]["pcr_percentile_52w"] == pytest.approx(100.0)
    z = m["options_positioning_52w"]["oi_volume_shock_52w_z"]
    assert 1.5 < z < 2.0                                                  # a linear ramp, not zeros: ~1.7


def test_a_short_gap_is_carried_forward_a_long_one_is_not(tmp_path):
    df = daily(300, options_from=0)
    df.loc[290:299, ["EOD_PCR", "Total_OI"]] = 0.0                          # last 10 days unfilled
    df.loc[290:299, "EOD_IV"] = np.nan
    m = run(df, tmp_path)
    assert m["options_positioning_52w"]["oi_volume_shock_52w_z"] is None  # today unknown: no shock figure
    df2 = daily(300, options_from=0)
    df2.loc[297:299, ["EOD_PCR", "Total_OI"]] = 0.0                         # 3 days: carried forward
    m2 = run(df2, tmp_path)
    assert m2["options_positioning_52w"]["oi_volume_shock_52w_z"] is not None
