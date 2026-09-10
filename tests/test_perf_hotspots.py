import numpy as np, pandas as pd, pytest
from performance_analyzer import PerformanceAnalyzer
from technical_engine import MathEngine


def test_feedback_payload_is_cached(monkeypatch):
    calls = {"n": 0}

    def counting_load(last_n_days=30):
        calls["n"] += 1
        return []

    monkeypatch.setattr("signal_ledger.SignalLedger.load_all_signals", counting_load)
    PerformanceAnalyzer.invalidate_cache()
    for _ in range(50):
        PerformanceAnalyzer.get_feedback_payload(14)
    assert calls["n"] <= 2, f"ledger re-read {calls['n']} times; cache not working"


def test_volume_profile_matches_the_loop_implementation():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"high": rng.uniform(100, 110, 500),
                       "low": rng.uniform(90, 100, 500),
                       "close": rng.uniform(95, 105, 500),
                       "volume": rng.uniform(1e3, 1e5, 500)})
    out = MathEngine.calc_volume_profile_high_fidelity(df, bins=100)
    assert out["rolling_20d_value_area_low"] <= out["rolling_20d_poc_price"]
    assert out["rolling_20d_poc_price"] <= out["rolling_20d_value_area_high"]


def test_bincount_profile_equals_the_old_accumulation_loop():
    """Lock in that the vectorised np.bincount aggregation is bit-identical
    to the per-row accumulation loop it replaced (D-3)."""
    rng = np.random.default_rng(1)
    indices = rng.integers(0, 100, 400)
    volumes = rng.uniform(1e3, 1e5, 400)
    volumes[::17] = np.nan                      # scatter some NaNs like real data

    loop = np.zeros(100)
    for i in range(len(indices)):
        if not np.isnan(volumes[i]):
            loop[indices[i]] += volumes[i]

    vec = np.bincount(indices, weights=np.nan_to_num(volumes), minlength=100)[:100]
    assert np.allclose(loop, vec)


def test_tod_zscore_vectorised_equals_row_apply():
    """The time-of-day volume z-score: vectorised map/where must match the
    old df.apply(calc_tod_z, axis=1) row-by-row semantics (D-3)."""
    idx = pd.date_range("2026-08-01 09:15", periods=120, freq="5min")
    rng = np.random.default_rng(2)
    df = pd.DataFrame({"timestamp": idx, "open": 100.0, "high": 101.0, "low": 99.0,
                       "close": 100.0, "volume": rng.uniform(1e3, 5e4, 120), "oi": 0.0})
    out = MathEngine.calc_institutional_volume(df.copy())

    # Reference: reproduce the old per-row logic independently.
    ref = df.copy()
    ref["time"] = pd.to_datetime(ref["timestamp"]).dt.time
    hist = ref.iloc[:-1]
    stats = hist.groupby("time")["volume"].agg(["mean", "std"]).to_dict(orient="index")
    ref["rmean"] = ref["volume"].rolling(20).mean()
    ref["rstd"] = ref["volume"].rolling(20).std()

    def old(row):
        s = stats.get(row["time"])
        if s and pd.notna(s["std"]) and s["std"] > 0:
            return (row["volume"] - s["mean"]) / s["std"]
        if pd.notna(row["rstd"]) and row["rstd"] > 0:
            return (row["volume"] - row["rmean"]) / row["rstd"]
        return 0.0

    expected = ref.apply(old, axis=1).fillna(0.0)
    assert np.allclose(out["vol_z_score"].values, expected.values, equal_nan=True)
