"""IV rank on the Market board: the ATM IV comes from the nearest strike that
actually traded, and a stock with too little IV history gets no rank."""
from types import SimpleNamespace as C

import numpy as np
import pandas as pd
import pytest

import derivatives_worker as dw

T = 20 / 365


def c(strike, kind, key):
    return C(strike_price=strike, instrument_type=kind, instrument_key=key)


def price(S, K, sigma, kind):
    from scipy.stats import norm
    d1 = (np.log(S / K) + (0.07 + sigma ** 2 / 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if kind == "CE":
        return S * norm.cdf(d1) - K * np.exp(-0.07 * T) * norm.cdf(d2)
    return K * np.exp(-0.07 * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def test_an_untraded_nearest_strike_is_skipped_for_the_next_traded_one():
    """SAIL at 176.95: 177.5 and 175 calls had no trade; 180 did."""
    cs = [c(177.5, "CE", "a"), c(175, "CE", "b"), c(180, "CE", "d")]
    iv = dw.atm_iv_from_quotes(cs, {"a": 0.0, "b": 0.0, "d": price(176.95, 180, 0.40, "CE")}, 176.95, T)
    assert iv == pytest.approx(40.0, abs=0.1)
    assert dw.atm_iv_from_quotes(cs[:2], {"a": 0.0, "b": 0.0}, 176.95, T) == 0.0      # nothing traded


def test_call_and_put_at_the_same_strike_are_averaged():
    cs = [c(100, "CE", "c"), c(100, "PE", "p")]
    prices = {"c": price(100, 100, 0.30, "CE"), "p": price(100, 100, 0.34, "PE")}
    assert dw.atm_iv_from_quotes(cs, prices, 100, T) == pytest.approx(32.0, abs=0.2)


def test_too_little_iv_history_gives_no_rank(tmp_path, monkeypatch):
    def store(n):
        f = tmp_path / f"X{n}_1D.parquet"
        pd.DataFrame({"Date": pd.bdate_range("2026-01-01", periods=n),
                      "Close": 100.0, "EOD_IV": np.linspace(20, 40, n)}).to_parquet(f)
        return f
    files = {"THIN": store(3), "FULL": store(dw.IVR_MIN_DAYS)}
    monkeypatch.setattr(dw, "parquet_path", lambda s: files[s])
    monkeypatch.setattr(dw, "MACRO_BASELINES_PATH", tmp_path / "none.json")
    assert dw.calculate_ivr("THIN", 41.0) is None                        # was 100
    assert dw.calculate_ivr("FULL", 30.0) == pytest.approx(50.0)
    assert dw.last_close("FULL") == 100.0
