"""The local history store (scripts/history_sync.py): the options figures from
NSE's daily F&O file, and filling exactly the days that are missing."""
import io
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "trading_copilot" / "scripts"))
import history_sync as hs  # noqa: E402

DAY = "2026-09-30"
EXP = "2026-10-27"


def contract(sym, kind, strike, oi, close, vol=10, exp=EXP):
    return {"TckrSymb": sym, "FinInstrmTp": "STO", "XpryDt": exp, "StrkPric": strike, "OptnTp": kind,
            "OpnIntrst": oi, "ClsPric": close, "TtlTradgVol": vol}


def chain(sym="SAIL"):
    rows = [contract(sym, "CE", 90, 100, 12.0), contract(sym, "PE", 90, 50, 1.0),
            contract(sym, "CE", 100, 400, 4.0), contract(sym, "PE", 100, 300, 4.2),
            contract(sym, "CE", 110, 600, 0.8), contract(sym, "PE", 110, 100, 11.0),
            contract(sym, "CE", 120, 999, 0.1, vol=0),                       # untraded: not counted
            contract(sym, "CE", 100, 5000, 9.0, exp="2026-11-24")]           # next expiry: not counted
    return pd.DataFrame(rows)


def write_bhav(dirpath: Path, day: str, df: pd.DataFrame):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bhav.csv", df.to_csv(index=False))
    (dirpath / f"{day.replace('-', '')}.csv.zip").write_bytes(buf.getvalue())


# ------------------------------------------------------------------ the figures
def test_figures_use_the_nearest_expiry_and_traded_contracts_only():
    f = hs.eod_figures(chain(), DAY, spot=100.0)
    assert f["Total_OI"] == 100 + 400 + 600 + 50 + 300 + 100                # no untraded, no next expiry
    assert f["EOD_PCR"] == pytest.approx(450 / 1100, abs=1e-4)
    assert f["EOD_MAX_PAIN"] == 100.0
    assert 10 < f["EOD_IV"] < 80                                             # at the max-pain strike


def test_a_day_with_no_trades_falls_back_to_every_listed_contract():
    """SAIL 2026-10-01: 116 contracts listed, none traded."""
    c = chain()
    c["TtlTradgVol"] = 0
    f = hs.eod_figures(c, DAY, spot=100.0)
    assert f["Total_OI"] == 100 + 400 + 600 + 999 + 50 + 300 + 100      # all of the nearest expiry
    assert f["EOD_MAX_PAIN"] > 0


def test_no_contract_of_a_live_expiry_means_no_figures():
    c = chain()
    c["XpryDt"] = "2026-09-01"                                            # all expired
    assert hs.eod_figures(c, DAY, spot=100.0) is None


def test_a_row_is_complete_only_with_its_open_interest():
    """SAIL 2026-09-30 was written in part by the live system: PCR 0.5, IV 52.25,
    no OI. It is recomputed rather than kept."""
    df = pd.DataFrame({"EOD_IV": [np.nan, 52.25, 30.0], "EOD_PCR": [0.0, 0.5, 0.7],
                       "Total_OI": [0.0, np.nan, 1e6], "EOD_MAX_PAIN": [0.0, 180.0, 100.0]})
    assert list(hs.eod_filled(df)) == [False, False, True]


# ------------------------------------------------------------------ filling only what is missing
@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "DATA", tmp_path)
    monkeypatch.setattr(hs, "RAW_FO", tmp_path / "nse_fo")
    (tmp_path / "nse_fo").mkdir()
    return tmp_path


def daily(days, filled=()):
    return pd.DataFrame({"Date": pd.to_datetime(days), "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0,
                         "Volume": 1e6, "EOD_PCR": [0.5 if d in filled else 0.0 for d in days],
                         "EOD_MAX_PAIN": 0.0, "EOD_IV": np.nan,
                         "Total_OI": [7.0 if d in filled else 0.0 for d in days]})


def test_missing_options_days_are_filled_and_stored_days_left_alone(store):
    days = ["2026-09-28", "2026-09-29", DAY]
    hs.save_daily("SAIL", daily(days, filled={"2026-09-28"}))
    for d in days:
        write_bhav(store / "nse_fo", d, chain())
    hs.fill_eod(["SAIL"], days)
    out = hs.load_daily("SAIL")
    assert out.loc[0, "EOD_PCR"] == 0.5 and out.loc[0, "Total_OI"] == 7.0  # already stored: untouched
    assert (out.loc[1:, "EOD_MAX_PAIN"] == 100.0).all()                     # the two gaps: filled
    assert (out.loc[1:, "EOD_PCR"] > 0).all()


def test_a_day_without_its_nse_file_waits_for_the_next_run(store):
    days = ["2026-09-29", DAY]
    hs.save_daily("SAIL", daily(days))
    write_bhav(store / "nse_fo", "2026-09-29", chain())                    # 09-30's file not in yet
    hs.fill_eod(["SAIL"], days)
    out = hs.load_daily("SAIL")
    assert out.loc[0, "EOD_MAX_PAIN"] == 100.0 and out.loc[1, "EOD_MAX_PAIN"] == 0.0


def test_only_missing_nse_files_are_downloaded_and_todays_waits_for_the_evening(store, monkeypatch):
    write_bhav(store / "nse_fo", "2026-09-29", chain())
    asked = []

    class R:
        status_code, content = 200, b"PK\x03\x04"

    monkeypatch.setattr(hs.requests, "get", lambda url, **k: asked.append(url) or R())
    monkeypatch.setattr(hs.time, "sleep", lambda s: None)
    noon = datetime(2026, 10, 1, 12, 0, tzinfo=hs.IST)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return noon

    monkeypatch.setattr(hs, "datetime", Clock)
    missing = hs.sync_nse_files(["2026-09-29", DAY, "2026-10-01"])
    assert len(asked) == 1 and "20260930" in asked[0]                       # 09-29 stored; today too early
    assert missing == ["2026-10-01"]
    assert (store / "nse_fo" / "20260930.csv.zip").exists()


def test_a_gap_of_many_days_is_filled_in_one_run(store):
    """The system was off for a week: every session in between is filled."""
    days = [f"2026-09-{d:02d}" for d in (21, 22, 23, 24, 25, 28, 29, 30)]
    hs.save_daily("SAIL", daily(days))
    for d in days:
        write_bhav(store / "nse_fo", d, chain())
    hs.fill_eod(["SAIL"], days)
    assert (hs.load_daily("SAIL").EOD_MAX_PAIN == 100.0).all()


def test_a_stock_day_without_options_is_looked_up_once(store, monkeypatch):
    """POWERINDIA before it entered F&O: absent from NSE's file. Remembered, so
    later runs do not re-read every file for it."""
    days = ["2026-09-29", DAY]
    hs.save_daily("POWERINDIA", daily(days))
    for d in days:
        write_bhav(store / "nse_fo", d, chain("SAIL"))                      # no POWERINDIA in the file
    hs.fill_eod(["POWERINDIA"], days)
    import json
    assert json.loads((store / "nse_fo" / "no_options.json").read_text())["POWERINDIA"] == days
    read = []
    monkeypatch.setattr(hs, "read_bhav", lambda p: read.append(p) or pd.DataFrame())
    hs.fill_eod(["POWERINDIA"], days)
    assert read == []


def test_the_ordinary_shares_are_chosen_when_nse_lists_two_series(monkeypatch):
    """MOTHERSON: INE775A08105 (partly paid) and INE775A01035 (shares)."""
    df = pd.DataFrame({"exchange": ["NSE_EQ", "BSE_EQ", "NSE_EQ"],
                       "tradingsymbol": ["MOTHERSON", "MOTHERSON", "MOTHERSON"],
                       "instrument_key": ["NSE_EQ|INE775A08105", "BSE_EQ|INE775A01035", "NSE_EQ|INE775A01035"]})
    monkeypatch.setattr(hs.scrip_master_engine, "_get_df", lambda: df)
    assert hs.key_of("MOTHERSON") == "NSE_EQ|INE775A01035"
