"""Headline metrics, the curve, daily rows and range selection, against a
four-trade book whose every figure is computed by hand below.

Capital ₹1,00,000. Mon 21 Sep and Tue 22 Sep 2026 (IST).
  T1 SAIL  LONG   09:30-10:00 Mon  gross +1100  costs 100  net +1000  r_net +2.0  TARGET
  T2 IDEA  SHORT  09:45-10:15 Mon  gross  -450  costs  50  net  -500  r_net -1.0  STOP
  T3 NMDC  LONG   11:00-12:00 Tue  gross  +320  costs  20  net  +300  r_net +0.6  WHALE_FLIP
  T4 SAIL  LONG   13:00-13:30 Tue  gross   +10  costs 210  net  -200  r_net -0.4  SQUARE_OFF
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from performance.metrics import curve, daily, gated, headline, risk_adjusted
from performance.scope import bounds, select

IST = ZoneInfo("Asia/Kolkata")
CAP = 100_000.0
LOW = {"rates": 1, "bucket": 1, "days": 1, "calibration": 1}
REAL = {"rates": 20, "bucket": 10, "days": 20, "calibration": 15}


def at(day, h, m):
    return datetime(2026, 9, day, h, m, tzinfo=IST).timestamp()


def costs(total):
    items = {"brokerage": total * 0.5, "stt": total * 0.3, "exchange": total * 0.1,
             "sebi": 0.0, "stamp": total * 0.05, "gst": total * 0.05}
    return dict(items, total=sum(items.values()))


def trade(n, symbol, side, opened, closed, gross, cost, r_net, r_gross, mae, mfe, reason,
          composite, regime, verdict="CONFIRM", risk=500.0):
    entry, stop, target = (100.0, 99.0, 102.0) if side == "LONG" else (100.0, 101.0, 98.0)
    exit_px = entry + (r_gross if side == "LONG" else -r_gross)
    op = {"type": "OPEN", "id": f"o{n}", "ts": opened, "pos_id": f"P{n}", "symbol": symbol,
          "side": side, "qty": 500, "entry_price": entry, "ltp_at_entry": entry - 0.03,
          "stop": stop, "target": target, "risk_amount": risk, "cluster": f"C-{symbol}",
          "signal_id": f"SIG-{symbol}-{int(opened) - 5}", "config_version": 1,
          "settings": {"capital": CAP, "risk_per_trade_pct": 0.5, "slippage_pct": 0.03},
          "context": {"verdict": verdict, "composite": composite, "regime": regime,
                      "session_phase": "MORNING_SESSION"}}
    net = gross - cost
    cl = {"type": "CLOSE", "id": f"c{n}", "ts": closed, "pos_id": f"P{n}", "symbol": symbol,
          "exit_price": exit_px, "exit_ref_price": exit_px + 0.02, "reason": reason,
          "touch_check": "bars", "flags": [], "gross": gross, "costs": costs(cost), "net": net,
          "r_gross": r_gross, "r_net": r_net, "mae_r": mae, "mfe_r": mfe,
          "hold_min": (closed - opened) / 60.0}
    return [op, cl]


def book():
    return (trade(1, "SAIL", "LONG", at(21, 9, 30), at(21, 10, 0), 1100, 100, 2.0, 2.2, -0.2, 2.2,
                  "TARGET", 0.35, "TREND_EXPANSION")
            + trade(2, "IDEA", "SHORT", at(21, 9, 45), at(21, 10, 15), -450, 50, -1.0, -0.9, -1.0, 0.6,
                    "STOP", -0.25, "LUNCH_CHOP", verdict="ADJUST")
            + trade(3, "NMDC", "LONG", at(22, 11, 0), at(22, 12, 0), 320, 20, 0.6, 0.64, -0.9, 1.0,
                    "GATEKEEPER_WHALE_FLIP", 0.45, "TREND_EXPANSION")
            + trade(4, "SAIL", "LONG", at(22, 13, 0), at(22, 13, 30), 10, 210, -0.4, 0.02, -0.5, 0.1,
                    "SQUARE_OFF", 0.12, "LUNCH_CHOP"))


NOW = at(22, 16, 0)


def scope(events=None, rng="all", cap=CAP, **kw):
    return select(events if events is not None else book(), rng, NOW, cap, **kw)


def val(m):
    assert m["status"] == "ok", m
    return m["value"]


# ------------------------------------------------------------------ gating
def test_gate_statuses():
    assert gated(1.5, 20, 20) == {"value": 1.5, "n": 20, "min_n": 20, "status": "ok"}
    assert gated(1.5, 19, 20) == {"value": None, "n": 19, "min_n": 20, "status": "insufficient"}
    assert gated(None, 30, 20)["status"] == "undefined"
    assert gated(float("inf"), 30, 20)["value"] is None


def test_every_rate_is_withheld_below_its_minimum():
    h = headline(scope(), REAL)
    for k in ("win_rate", "profit_factor", "expectancy", "expectancy_r", "avg_win", "avg_loss",
              "payoff", "cost_drag_pct", "cost_per_trade_r"):
        assert h[k]["value"] is None and h[k]["status"] == "insufficient" and h[k]["n"] < 20, k
    # facts are always reported
    assert val(h["net_pnl"]) == pytest.approx(600) and val(h["trades"]) == 4
    r = risk_adjusted(scope(), REAL)
    assert all(v["status"] == "insufficient" and v["value"] is None for v in r.values())


# ---------------------------------------------------------------- headline
def test_headline_matches_hand_computation():
    h = headline(scope(), LOW)
    assert val(h["trades"]) == 4
    assert val(h["net_pnl"]) == pytest.approx(600)
    assert val(h["gross_pnl"]) == pytest.approx(980)
    assert val(h["costs"]) == pytest.approx(380)
    assert val(h["return_pct"]) == pytest.approx(0.6)
    assert val(h["win_rate"]) == pytest.approx(50.0)
    assert val(h["profit_factor"]) == pytest.approx(1300 / 700)
    assert val(h["expectancy"]) == pytest.approx(150)
    assert val(h["expectancy_r"]) == pytest.approx(0.3)
    assert val(h["avg_win"]) == pytest.approx(650)
    assert val(h["avg_loss"]) == pytest.approx(-350)
    assert val(h["payoff"]) == pytest.approx(650 / 350)
    assert val(h["cost_drag_pct"]) == pytest.approx(380 / 1430 * 100)
    assert val(h["cost_per_trade_r"]) == pytest.approx((0.2 + 0.1 + 0.04 + 0.42) / 4)
    assert val(h["max_drawdown"]) == pytest.approx(-500)
    assert val(h["max_drawdown_pct"]) == pytest.approx(-500 / 101_000 * 100)
    assert val(h["longest_win_streak"]) == 1 and val(h["longest_loss_streak"]) == 1
    assert val(h["trades_per_day"]) == pytest.approx(2.0)
    assert val(h["avg_hold_min"]) == pytest.approx(37.5)
    # Mon: 09:30-10:15 (the overlap counted once) = 45 min; Tue 60 + 30 = 90
    assert val(h["exposure_pct"]) == pytest.approx(135 / 750 * 100)


def test_no_losing_trade_leaves_profit_factor_undefined_not_infinite():
    only_wins = [e for e in book() if e["pos_id"] in ("P1", "P3")]
    h = headline(scope(only_wins), LOW)
    assert h["profit_factor"]["status"] == "undefined" and h["profit_factor"]["value"] is None
    assert h["payoff"]["status"] == "undefined"


def test_streaks_follow_close_order():
    ev = book()
    for e in ev:                      # make T3 a loss too: W L L L
        if e["type"] == "CLOSE" and e["pos_id"] == "P3":
            e["gross"], e["net"] = -20, -40
    h = headline(scope(ev), LOW)
    assert val(h["longest_win_streak"]) == 1 and val(h["longest_loss_streak"]) == 3


def test_an_empty_book_reports_zeros_as_facts_and_rates_as_insufficient():
    h = headline(scope([]), LOW)
    assert val(h["trades"]) == 0 and val(h["net_pnl"]) == 0
    assert h["win_rate"]["status"] == "insufficient"
    assert curve(scope([]))["realised"] == []


# ------------------------------------------------------------------ curve
def test_curve_reconciles_to_the_trades():
    c = curve(scope())
    pts = c["realised"]
    assert [p["pnl"] for p in pts] == pytest.approx([0, 1000, 500, 800, 600])
    assert [p["dd"] for p in pts] == pytest.approx([0, 0, -500, -200, -400])
    assert pts[-1]["equity"] == pytest.approx(CAP + 600)
    assert pts[-1]["gross"] == pytest.approx(980)
    assert [t["pos_id"] for t in c["ticks"]] == ["P1", "P2", "P3", "P4"]


def test_marked_curve_comes_from_equity_snapshots():
    ev = book() + [{"type": "EQUITY", "id": "e1", "ts": at(22, 11, 30), "capital": CAP,
                    "realized_total": 500.0, "unrealized": -250.0, "equity": CAP + 250.0,
                    "open_risk": 500.0, "open_count": 1, "marks": {}}]
    c = curve(scope(ev, rng="today"))
    # today's P&L starts from what Monday banked (500): 500 - 250 - 500 = -250
    assert c["marked"][0]["pnl"] == pytest.approx(-250) and c["marked"][0]["dd"] == pytest.approx(-250)
    h = headline(scope(ev, rng="today"), LOW)
    assert val(h["max_drawdown_marked"]) == pytest.approx(-250)


# ------------------------------------------------------------------ ranges
def test_today_uses_what_was_banked_before_it():
    sc = scope(rng="today")
    assert [t["pos_id"] for t in sc.trades] == ["P3", "P4"] and sc.prior_net == pytest.approx(500)
    h = headline(sc, LOW)
    assert val(h["net_pnl"]) == pytest.approx(100)
    assert val(h["return_pct"]) == pytest.approx(100 / 100_500 * 100)
    assert val(h["max_drawdown_pct"]) == pytest.approx(-200 / 100_800 * 100)


def test_range_bounds():
    assert bounds("today", NOW) == ("2026-09-22", "2026-09-22")
    assert bounds("5d", NOW) == ("2026-09-16", "2026-09-22")        # Wed..Tue, weekend skipped
    assert bounds("1m", NOW) == ("2026-08-23", "2026-09-22")
    assert bounds("all", NOW) == (None, None)
    assert bounds("custom", NOW, "2026-09-21", "2026-09-21") == ("2026-09-21", "2026-09-21")
    for bad in (("week",), ("custom", "21-09-2026"), ("custom", "2026-09-22", "2026-09-21")):
        with pytest.raises(ValueError):
            bounds(bad[0], NOW, *bad[1:])


def test_custom_range_selects_by_close_day():
    sc = scope(rng="custom", frm="2026-09-21", to="2026-09-21")
    assert [t["pos_id"] for t in sc.trades] == ["P1", "P2"] and sc.session_days == ["2026-09-21"]


# ---------------------------------------------------- capital history
def test_changing_capital_never_rewrites_history():
    ev = book() + [{"type": "SETTINGS", "id": "s1", "ts": at(22, 8, 0),
                    "old": {"capital": CAP}, "new": {"capital": 200_000.0}}]
    sc = scope(ev, cap=200_000.0)                     # today's capital is 2 lakh
    assert sc.capital_at(at(21, 10, 0)) == CAP and sc.capital_at(at(22, 12, 0)) == 200_000.0
    h = headline(sc, LOW)
    assert val(h["return_pct"]) == pytest.approx(0.6)                 # against the 1 lakh in force then
    d = daily(sc)
    assert d[0]["return_pct"] == pytest.approx(0.5)
    assert d[1]["return_pct"] == pytest.approx(100 / 200_500 * 100)
    assert curve(sc)["settings_changes"][0]["new"] == {"capital": 200_000.0}
    # made before Tuesday's open, so Tuesday is measured against it
    today = scope(ev, rng="today", cap=200_000.0)
    assert val(headline(today, LOW)["return_pct"]) == pytest.approx(100 / 200_500 * 100)
    assert len(curve(today)["settings_changes"]) == 1


# ---------------------------------------------------------- daily & risk
def test_daily_rows_and_flat_days_count():
    ev = book() + [{"type": "REJECT", "id": "r1", "ts": at(18, 10, 0), "symbol": "SAIL",
                    "reason": "NO_ADV"}]                 # Fri: engine ran, no trade
    d = daily(scope(ev))
    assert [x["day"] for x in d] == ["2026-09-18", "2026-09-21", "2026-09-22"]
    assert [x["net"] for x in d] == pytest.approx([0, 500, 100])
    assert d[0]["return_pct"] == 0.0 and d[1]["return_pct"] == pytest.approx(0.5)
    assert sum(x["net"] for x in d) == pytest.approx(val(headline(scope(ev), LOW)["net_pnl"]))


def test_risk_adjusted_by_hand():
    ev = book() + [{"type": "REJECT", "id": "r1", "ts": at(18, 10, 0), "symbol": "SAIL",
                    "reason": "NO_ADV"}]
    r = risk_adjusted(scope(ev), LOW)
    import math
    from statistics import mean, stdev
    rets = [0.0, 0.005, 100 / 100_500]
    mu = mean(rets)
    assert val(r["sharpe"]) == pytest.approx(mu / stdev(rets) * math.sqrt(252))
    # no losing day: no downside deviation, no drawdown -> undefined, not a number
    assert r["sortino"]["status"] == "undefined" and r["calmar"]["status"] == "undefined"


def test_sortino_and_calmar_with_a_losing_day():
    ev = [e for e in book() if e["pos_id"] in ("P1", "P2", "P4")]    # Mon +500, Tue -200
    r = risk_adjusted(scope(ev), LOW)
    import math
    rets = [0.005, -200 / 100_500]
    mu = sum(rets) / 2
    down = math.sqrt((0 + rets[1] ** 2) / 2)
    assert val(r["sortino"]) == pytest.approx(mu / down * math.sqrt(252))
    dd = -200 / 100_500
    assert val(r["calmar"]) == pytest.approx(mu * 252 / abs(dd))
