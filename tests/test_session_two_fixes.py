"""Fixes from the second paper session (2026-09-30):

- a stock that never ticked this session is stale, not fresh (ADANIPORTS
  was shorted on the previous close and "managed" on it all day);
- the feed fills an outage from the exchange's candles, so a stop hit while
  the machine was off is seen (four were missed);
- the daily loss limit latches (a win reopened it);
- policy v3: no new entry before 09:45.
"""
import time

import pandas as pd
import pytest

from diagnostic_ui import TerminalDashboard
from rolling_state_engine import NEVER_TICKED_AGE_S, RollingStateEngine
from data_services.upstox_feed import exchange_bars, has_gap, merge_gap
from paper.fills import Bar
from test_paper_broker_open import ist, make, sig

TOKEN = "NSE_EQ|AGETEST"


def _engine():
    idx = pd.date_range("2026-09-29 09:15", periods=100, freq="5min")
    df = pd.DataFrame({"timestamp": idx, "open": 100.0, "high": 101.0, "low": 99.0,
                       "close": 100.0, "volume": 1000.0, "oi": 0.0})
    eng = RollingStateEngine.__new__(RollingStateEngine)
    eng.dfs = {TOKEN: {"ltf_df": df, "htf_df": pd.DataFrame()}}
    eng.watchlist = {TOKEN: {"symbol": "AGETEST"}}
    eng.phantom_candles, eng._failures, eng.last_tick_ts = {}, {}, {}
    return eng


def _phantom():
    return {"timestamp": pd.Timestamp("2026-09-30 09:15"), "open": 100.0, "high": 100.0, "low": 100.0,
            "close": 100.0, "volume": 0.0, "oi": 0.0, "microstructure": {}}


# -- never ticked is not fresh ----------------------------------------------------
def test_a_stock_that_never_ticked_is_stale():
    eng = _engine()
    eng._compute_symbol(TOKEN, _phantom())
    assert TerminalDashboard.active_states[TOKEN]["data_age_s"] == NEVER_TICKED_AGE_S


def test_a_stock_that_ticked_reports_its_real_age():
    eng = _engine()
    eng.last_tick_ts[TOKEN] = time.time() - 3
    eng._compute_symbol(TOKEN, _phantom())
    assert 2 <= TerminalDashboard.active_states[TOKEN]["data_age_s"] < 10


def test_the_paper_account_refuses_a_never_ticked_price(tmp_path):
    b, _, _ = make(tmp_path)
    assert b.on_signal(sig(data_age_s=NEVER_TICKED_AGE_S))["reason"] == "NO_FRESH_PRICE"


# -- the outage is filled from the exchange ------------------------------------------
def _epoch(hm):
    return pd.Timestamp(f"2026-09-30 {hm}", tz="Asia/Kolkata").timestamp()


def candle(hm, o, h, l, c):
    return [f"2026-09-30T{hm}:00+05:30", o, h, l, c, 100, 0]


def test_minute_candles_become_five_minute_bars_on_the_ist_clock():
    bars = exchange_bars([candle("10:31", 5, 7, 4, 6), candle("10:30", 5, 6, 5, 5),
                          candle("10:35", 6, 6, 1, 2)], since=_epoch("10:30"))
    assert [b["timestamp"] for b in bars] == ["2026-09-30 10:30:00", "2026-09-30 10:35:00"]
    assert (bars[0]["open"], bars[0]["high"], bars[0]["low"], bars[0]["close"]) == (5, 7, 4, 6)
    assert bars[0]["volume"] == 200


def test_a_hole_after_since_is_filled_and_live_bars_win_after_it():
    live = [{"timestamp": "2026-09-30 12:50:00", "open": 9, "high": 9, "low": 9, "close": 9}]
    since = _epoch("10:20")
    assert has_gap(live, since)
    exch = exchange_bars([candle("10:30", 5, 5, 1, 2), candle("12:50", 7, 7, 7, 7)], since)
    out = merge_gap(live, exch, since)
    assert [b["timestamp"] for b in out] == ["2026-09-30 10:30:00", "2026-09-30 12:50:00"]
    assert out[-1]["open"] == 9                                            # the feed's own bar kept


def test_no_hole_no_exchange_call():
    live = [{"timestamp": "2026-09-30 10:20:00"}, {"timestamp": "2026-09-30 10:25:00"}]
    assert not has_gap(live, _epoch("10:22"))


def test_the_paper_engine_sees_a_stop_hit_during_an_outage(tmp_path):
    """DRREDDY: long, stop hit at 10:30 while the machine was off, recorded
    as a target at 12:55. With the hole filled, the first touch is the stop."""
    b, clock, _ = make(tmp_path, t=ist(10, 0, day=30))
    b.on_signal(sig())
    clock.t = ist(12, 55, day=30)
    exch = exchange_bars([candle("10:30", 99, 99.5, 97.5, 98), candle("12:50", 103, 104.5, 103, 104)],
                         since=ist(10, 0, day=30))
    bars = [Bar(ts=pd.Timestamp(x["timestamp"], tz="Asia/Kolkata").timestamp(), open=x["open"],
                high=x["high"], low=x["low"], close=x["close"]) for x in exch]
    ev = b.check_touches("SAIL", bars)
    assert ev["reason"] == "STOP" and ev["exit_price"] == 98.0


# -- the loss limit latches -----------------------------------------------------------
def _close(b, clock, hm, px, **kw):
    clock.t = ist(*hm)
    b.on_signal(sig(**kw))
    clock.t = ist(hm[0], hm[1] + 5)
    return b.check_touches(kw.get("symbol", "SAIL"), None, ltp=px)


def test_a_win_after_the_limit_does_not_reopen_it(tmp_path):
    """As DRREDDY: a position opened early closes at its target after the
    limit has tripped, lifting the day back above it."""
    b, clock, _ = make(tmp_path)
    b.on_signal(sig(symbol="IDEA", signal_id="early"))                   # stays open
    for i, sym in enumerate(("SAIL", "NMDC", "SAIL", "NMDC")):           # four 1R losses: about -21k
        _close(b, clock, (10, 5 + i * 10), 97.9, symbol=sym)
    assert b.realized_loss_today() > 20_000
    clock.t = ist(11, 0)
    assert b.check_touches("IDEA", None, ltp=104.0)["reason"] == "TARGET"
    assert b.realized_today() > -20_000                                  # the day is back inside the limit
    clock.t = ist(11, 20)
    assert b.on_signal(sig(symbol="NMDC"))["reason"] == "DAILY_LOSS_LIMIT"   # but the gate stays shut


# -- policy v3 ----------------------------------------------------------------------
def test_policy_v3_starts_entries_at_0945():
    from core.policy_config import load_policy
    from paths import CONFIG_DIR
    p = load_policy(CONFIG_DIR / "policy_v3.yaml")                # v4 is the default now; v3 kept as is
    assert p.version == 3 and p.horizon["entry_start_ist"] == "09:45"
    assert p.horizon["square_off_ist"] == "15:10"                         # v2's values kept


def test_the_paper_account_refuses_an_opening_range_entry(tmp_path):
    from paper.broker import PaperBroker
    base, clock, _ = make(tmp_path, t=ist(9, 30))
    b = PaperBroker(store=base.store, settings=base.settings, costs_cfg=base.costs_cfg, clock=clock,
                    entry_start="09:45")
    assert b.on_signal(sig())["reason"] == "BEFORE_ENTRY_START"
    clock.t = ist(9, 45)
    assert b.on_signal(sig())["type"] == "OPEN"


def test_a_hole_in_the_middle_of_the_day_is_found_and_filled():
    """The ledger asks from the open: bars to 10:25, then nothing until 12:50."""
    live = [{"timestamp": f"2026-09-30 {hm}:00", "open": 1, "high": 1, "low": 1, "close": 1}
            for hm in ("09:15", "09:20", "10:25", "12:50")]
    since = _epoch("09:15")
    assert has_gap(live, since)
    exch = exchange_bars([candle("10:30", 5, 5, 1, 2), candle("09:20", 7, 7, 7, 7)], since)
    out = merge_gap(live, exch, since)
    assert [b["timestamp"][11:16] for b in out] == ["09:15", "09:20", "10:25", "10:30", "12:50"]
    assert out[1]["open"] == 1                                             # the feed's own 09:20 kept
