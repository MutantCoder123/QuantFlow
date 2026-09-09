import pandas as pd
from journal.feature_log import FeatureLog, FeatureRecord


def _rec(**kw):
    base = dict(ts=1_757_000_000, symbol="SAIL", config_version=1,
                features={"obi": 0.42, "vol_z_score_5m": 2.7, "rsi_5m": 61.0},
                staleness={"microstructure": 0.4, "derivatives": 271.0},
                regime="TREND_EXPANSION", session_phase="MORNING_SESSION",
                composite=0.34, decision="PROPOSED", llm_verdict=None)
    base.update(kw)
    return FeatureRecord(**base)


def test_features_are_flattened_into_columns(tmp_path):
    log = FeatureLog(tmp_path, flush_n=1_000_000)
    log.write(_rec())
    df = pd.read_parquet(log.flush())
    assert df["f_obi"].iloc[0] == 0.42
    assert df["f_vol_z_score_5m"].iloc[0] == 2.7
    assert df["stale_derivatives"].iloc[0] == 271.0
    assert df["decision"].iloc[0] == "PROPOSED"


def test_rejected_rows_are_recorded_too(tmp_path):
    """The counterfactual is the point — rejections must be logged."""
    log = FeatureLog(tmp_path, flush_n=1_000_000)
    log.write(_rec(decision="REJECTED_NEUTRAL_CONVICTION", composite=0.04))
    df = pd.read_parquet(log.flush())
    assert df["decision"].iloc[0] == "REJECTED_NEUTRAL_CONVICTION"


def test_heterogeneous_feature_sets_do_not_crash(tmp_path):
    log = FeatureLog(tmp_path, flush_n=1_000_000)
    log.write(_rec())
    log.write(_rec(features={"obi": 0.1, "new_feature": 9.9}))
    df = pd.read_parquet(log.flush())
    assert len(df) == 2
    assert df["f_new_feature"].isna().iloc[0]
