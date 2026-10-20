"""Policy v8: the pre-open auction book (trading_copilot/auction) and intraday entries in shadow."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from auction import book as B, core

COSTS = {"brokerage_pct": 0.03, "brokerage_cap": 20, "stt_sell_pct": 0.025, "exchange_txn_pct": 0.00297,
         "sebi_pct": 0.0001, "stamp_duty_buy_pct": 0.003, "gst_pct": 18}
CFG = {"enabled": True, "n_per_side": 2, "gross_x": 5.0, "min_adv_crore": 0.0, "kill_loss_pct": 6.0}


def _panel(days=120, n=12, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2026-03-02", periods=days)
    cols = [f"S{i}" for i in range(n)]
    night = pd.DataFrame(rng.normal(0, 0.004, (days, n)), index=idx, columns=cols)
    intra = pd.DataFrame(rng.normal(0, 0.008, (days, n)), index=idx, columns=cols)
    lc = (night + intra).cumsum() + np.log(100.0)
    C = np.exp(lc)
    O = np.exp(lc - intra)
    return {"Open": O, "Close": C, "Volume": pd.DataFrame(1e6, index=idx, columns=cols)}


def test_factors_split_each_day_into_its_overnight_and_intraday_parts():
    P = _panel()
    F = core.factors(P)
    C, O = P["Close"], P["Open"]
    d = C.index[30]
    intra = np.log(C / O)
    night = np.log(O / C.shift(1))
    assert F["intra5"].loc[d, "S0"] == pytest.approx(intra["S0"].loc[:d].tail(5).mean())
    assert F["night20"].loc[d, "S3"] == pytest.approx(night["S3"].loc[:d].tail(20).mean())
    assert np.isnan(F["intra60"].loc[C.index[30], "S0"])                     # needs 45 days


def test_the_fit_never_uses_a_label_it_could_not_know():
    """The label of day t is t+1's open -> close; the last fitted day is the one before the last."""
    P = _panel()
    w, through = core.fit(P)
    assert through == str(P["Close"].index[-2].date())
    assert list(w.index) == core.FACTORS


def test_select_takes_the_top_and_bottom_liquid_stocks():
    s = pd.Series({"A": 0.9, "B": 0.8, "C": 0.1, "D": -0.5, "E": -0.9, "F": 0.0})
    adv = pd.Series({"A": 100, "B": 10, "C": 100, "D": 100, "E": 100, "F": 100})
    assert core.select(s, 2, adv, 50) == {"long": ["A", "C"], "short": ["E", "D"]}


def test_freshness_needs_yesterday_and_never_today():
    P = _panel()
    last = P["Close"].index[-1]
    nxt = last + pd.offsets.BDay(1)
    assert core.freshness(P, nxt, synced=False)[0]
    assert not core.freshness(P, last, synced=True)[0]                      # today already in the data
    assert not core.freshness(P, last + pd.offsets.BDay(3), synced=False)[0]  # days missing, no sync
    assert core.freshness(P, last + pd.offsets.BDay(2), synced=True)[0]     # a holiday, after a sync


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _book(tmp_path, cfg=CFG):
    from paper.store import EventStore
    return B.AuctionBook(EventStore(tmp_path), capital=1_000_000, slippage_pct=0.03, costs_cfg=COSTS, cfg=dict(cfg),
                         clock=Clock(datetime(2026, 10, 16, 9, 5, tzinfo=B.IST).timestamp()))


PICKS = {"long": ["AAA", "BBB"], "short": ["CCC", "DDD"]}
SCORES = {"AAA": 0.2, "BBB": 0.1, "CCC": -0.1, "DDD": -0.2}
DAY = "2026-10-16"


def test_a_plan_after_the_preopen_window_is_not_traded(tmp_path):
    bk = _book(tmp_path)
    e = bk.make_plan(DAY, datetime(2026, 10, 16, 9, 8, tzinfo=B.IST), PICKS, SCORES, "2026-10-15")
    assert e["type"] == "SKIP" and bk.skipped(DAY) and bk.plan_for(DAY) is None


def test_positions_fill_at_the_official_open_sized_by_notional(tmp_path):
    bk = _book(tmp_path)
    plan = bk.make_plan(DAY, datetime(2026, 10, 16, 9, 0, tzinfo=B.IST), PICKS, SCORES, "2026-10-15")
    assert plan["notional"] == pytest.approx(1_000_000 * 5 / 4)
    got = bk.fill(DAY, {"AAA": 1000.0, "BBB": 250.5, "CCC": 99.0})        # DDD has no open print
    assert [(e["symbol"], e["side"], e["qty"], e["entry_price"]) for e in got] == [
        ("AAA", "LONG", 1250, 1000.0), ("BBB", "LONG", 4990, 250.5), ("CCC", "SHORT", 12626, 99.0)]
    assert any(e["type"] == "SKIP" and e.get("symbol") == "DDD" for e in bk.today(DAY))
    assert bk.filled(DAY) and len(bk.open_positions(DAY)) == 3


def test_the_exit_pays_slippage_and_the_real_charges(tmp_path):
    bk = _book(tmp_path)
    bk.make_plan(DAY, datetime(2026, 10, 16, 9, 0, tzinfo=B.IST), PICKS, SCORES, "2026-10-15")
    bk.fill(DAY, {"AAA": 1000.0, "CCC": 100.0})
    done = {e["symbol"]: e for e in bk.close_all(DAY, {"AAA": 1010.0, "CCC": 99.0}, "EXIT_1510")}
    a, c = done["AAA"], done["CCC"]
    assert a["exit_price"] == pytest.approx(1010.0 * (1 - 0.0003))         # sell a long below the price
    assert c["exit_price"] == pytest.approx(99.0 * (1 + 0.0003))           # buy back a short above it
    assert a["gross"] == pytest.approx((a["exit_price"] - 1000.0) * 1250)
    assert a["net"] == pytest.approx(a["gross"] - a["costs"]["total"]) and a["costs"]["stt"] > 0
    assert bk.open_positions(DAY) == [] and bk.summary(DAY)["status"] == "closed"


def test_the_kill_switch_and_a_restart_from_the_store(tmp_path):
    bk = _book(tmp_path)
    bk.make_plan(DAY, datetime(2026, 10, 16, 9, 0, tzinfo=B.IST), PICKS, SCORES, "2026-10-15")
    bk.fill(DAY, {"AAA": 1000.0, "BBB": 1000.0, "CCC": 1000.0, "DDD": 1000.0})
    assert not bk.kill_hit(DAY, {"AAA": 990.0, "BBB": 990.0, "CCC": 1010.0, "DDD": 1010.0})   # -50k: under 6%
    assert bk.kill_hit(DAY, {"AAA": 970.0, "BBB": 970.0, "CCC": 1030.0, "DDD": 1030.0})       # -150k
    again = _book(tmp_path)                                                                   # restart
    assert again.plan_for(DAY) and len(again.open_positions(DAY)) == 4 and again.filled(DAY)


def test_policy_v8_shadows_intraday_entries_and_adds_the_auction_book():
    from core.policy_config import load_policy
    from paths import CONFIG_DIR
    v71, v8 = load_policy(CONFIG_DIR / "policy_v7_1.yaml"), load_policy(CONFIG_DIR / "policy_v8.yaml")
    assert v8.version == 8
    assert v8.books["intraday"] == {"entries": "shadow"}
    assert v8.books["auction"]["enabled"] is True and v8.books["auction"]["n_per_side"] == 5
    assert (v8.horizon, v8.gates, v8.semantic, v8.regime, v8.conviction) == \
        (v71.horizon, v71.gates, v71.semantic, v71.regime, v71.conviction)
    assert v71.books == {}


def test_a_shadow_entry_is_recorded_with_its_geometry_and_not_opened(tmp_path):
    from test_paper_broker_open import make, sig
    b, _, _ = make(tmp_path)
    b.entries_shadow = True
    ev = b.on_signal(sig(target=104.0, scorer="ALPHA"))
    assert ev["type"] == "REJECT" and ev["reason"] == "SHADOW_ENTRY"
    assert (ev["side"], ev["ltp"], ev["stop"], ev["target"]) == ("LONG", 100.0, 98.0, 104.0)
    assert b.position_for("SAIL") is None
    stored = [e for e in b.store.load() if e["type"] == "REJECT"]
    assert stored[-1]["side"] == "LONG" and stored[-1]["target"] == 104.0


def test_policy_v8_1_is_live_trades_intraday_and_keeps_the_auction_book_for_research():
    from core.policy_config import DEFAULT_POLICY, load_policy
    from paths import CONFIG_DIR
    v8, v81 = load_policy(CONFIG_DIR / "policy_v8.yaml"), load_policy(CONFIG_DIR / DEFAULT_POLICY)
    assert DEFAULT_POLICY == "policy_v8_1.yaml" and v81.version == 8.1
    assert v81.books["intraday"] == {"entries": "trade"}
    assert v81.books["auction"] == v8.books["auction"]
    assert (v81.horizon, v81.gates, v81.semantic, v81.regime, v81.conviction) ==         (v8.horizon, v8.gates, v8.semantic, v8.regime, v8.conviction)


def test_a_late_simulated_plan_is_tagged_and_overrides_the_days_skip(tmp_path):
    bk = _book(tmp_path)
    bk.skip(DAY, "STARTED_AFTER_PREOPEN: no plan was made before 09:07:30")
    assert bk.skipped(DAY)
    e = bk.make_plan(DAY, datetime(2026, 10, 16, 9, 58, tzinfo=B.IST), PICKS, SCORES, "2026-10-15", late_ok=True)
    assert e["type"] == "PLAN" and e["late_simulated"] is True
    assert bk.skipped(DAY) is None and bk.plan_for(DAY)
