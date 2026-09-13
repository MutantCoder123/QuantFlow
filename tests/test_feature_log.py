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


# --------------------------------------------------------------------------
# Read side (Task 5.6) -- the log had no reader at all until the session
# review needed to count stale-feed rejections out of it.
# --------------------------------------------------------------------------
from journal.feature_log import staleness_incidents


def test_load_day_returns_empty_frame_when_partition_missing(tmp_path):
    log = FeatureLog(tmp_path, flush_n=1_000_000)
    df = log.load_day("1999-01-01")
    assert df.empty


def test_load_day_reads_back_what_was_flushed(tmp_path):
    import datetime
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")
    log = FeatureLog(tmp_path, flush_n=1_000_000)
    log.write(_rec())
    log.flush()
    log.write(_rec(symbol="BHEL"))
    log.flush()                     # second parquet part in the same partition
    df = log.load_day(today)
    assert len(df) == 2
    assert set(df["symbol"]) == {"SAIL", "BHEL"}


def test_staleness_incidents_counts_stale_rejections(tmp_path):
    log = FeatureLog(tmp_path, flush_n=1_000_000)
    log.write(_rec(decision="GATED_STALE_DATA_22s", staleness={"microstructure": 22.0}))
    log.write(_rec(symbol="BHEL", decision="GATED_STALE_DATA_31s",
                   staleness={"microstructure": 31.0}))
    log.write(_rec(symbol="NMDC", decision="PROPOSED", staleness={"microstructure": 0.4}))
    log.flush()
    import datetime
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")

    out = staleness_incidents(log.load_day(today))
    assert out["incidents"] == 2
    assert out["symbols"] == 2
    assert out["max_stale_microstructure_s"] == 31.0


def test_staleness_incidents_on_empty_frame_reports_unknown_not_zero_max(tmp_path):
    log = FeatureLog(tmp_path, flush_n=1_000_000)
    out = staleness_incidents(log.load_day("1999-01-01"))
    assert out["incidents"] == 0
    assert out["max_stale_microstructure_s"] is None
