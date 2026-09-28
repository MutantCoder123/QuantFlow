"""Paper fill model and per-trade costs -- pure, conservative, explicit.

Every assumption here is shown to the operator (Architecture §5): slippage
always against us; a stop that gaps is filled at the worse price; a bar that
touches both stop and target is treated as a stop.
"""
import numpy as np
import pytest

from paper.fills import Bar, entry_fill, market_exit_fill, first_touch, trade_costs, pnl


# -- entry / market exits -----------------------------------------------------
def test_entry_slippage_is_always_against_us():
    assert entry_fill("LONG", 100.0, 0.03) == pytest.approx(100.03)
    assert entry_fill("SHORT", 100.0, 0.03) == pytest.approx(99.97)


def test_market_exit_slippage_is_always_against_us():
    assert market_exit_fill("LONG", 100.0, 0.03) == pytest.approx(99.97)
    assert market_exit_fill("SHORT", 100.0, 0.03) == pytest.approx(100.03)


def test_numpy_inputs_come_back_as_plain_floats():
    px = entry_fill("LONG", np.float64(100.0), np.float32(0.03))
    assert type(px) is float


# -- stop / target touches ----------------------------------------------------
def B(ts, o, h, l, c=None):
    return Bar(ts=ts, open=o, high=h, low=l, close=c if c is not None else o)


def test_long_stop_touch_fills_at_the_stop():
    hit = first_touch("LONG", stop=98.0, target=105.0, bars=[B(1, 100, 101, 99), B(2, 99, 99.5, 97.8)])
    assert hit == ("STOP", 98.0, 2)


def test_long_stop_gap_fills_at_the_worse_open():
    hit = first_touch("LONG", stop=98.0, target=105.0, bars=[B(1, 96.5, 97, 96)])
    assert hit == ("STOP", 96.5, 1)


def test_short_stop_gap_fills_at_the_worse_open():
    hit = first_touch("SHORT", stop=102.0, target=95.0, bars=[B(1, 103.2, 104, 103)])
    assert hit == ("STOP", 103.2, 1)


def test_target_fills_at_the_target_never_better():
    hit = first_touch("LONG", stop=98.0, target=105.0, bars=[B(1, 106, 107, 105.5)])
    assert hit == ("TARGET", 105.0, 1)
    hit = first_touch("SHORT", stop=102.0, target=95.0, bars=[B(1, 99, 99, 94)])
    assert hit == ("TARGET", 95.0, 1)


def test_a_bar_touching_both_is_a_stop():
    hit = first_touch("LONG", stop=98.0, target=105.0, bars=[B(1, 100, 106, 97)])
    assert hit[0] == "STOP"


def test_first_touch_in_time_order_wins():
    bars = [B(3, 100, 100, 97), B(2, 100, 106, 100)]      # unsorted on purpose
    assert first_touch("LONG", stop=98.0, target=105.0, bars=bars)[0] == "TARGET"


def test_no_touch_returns_none():
    assert first_touch("LONG", stop=98.0, target=105.0, bars=[B(1, 100, 101, 99)]) is None
    assert first_touch("LONG", stop=98.0, target=105.0, bars=[]) is None


# -- P&L and costs ------------------------------------------------------------
CFG = {"brokerage_pct": 0.03, "brokerage_cap": 20, "stt_sell_pct": 0.025,
       "exchange_txn_pct": 0.00297, "sebi_pct": 0.0001, "stamp_duty_buy_pct": 0.003,
       "gst_pct": 18}


def test_gross_pnl_by_side():
    assert pnl("LONG", 100.0, 102.0, 50) == pytest.approx(100.0)
    assert pnl("SHORT", 100.0, 102.0, 50) == pytest.approx(-100.0)


def test_long_costs_are_itemised_and_hand_checkable():
    c = trade_costs("LONG", entry=100.0, exit_px=102.0, qty=1000, cfg=CFG)
    buy, sell = 100_000.0, 102_000.0
    assert c["brokerage"] == pytest.approx(20 + 20)                       # both legs capped
    assert c["stt"] == pytest.approx(sell * 0.025 / 100)                  # sell leg = exit
    assert c["stamp"] == pytest.approx(buy * 0.003 / 100)                 # buy leg = entry
    assert c["exchange"] == pytest.approx((buy + sell) * 0.00297 / 100)
    assert c["sebi"] == pytest.approx((buy + sell) * 0.0001 / 100)
    assert c["gst"] == pytest.approx(0.18 * (c["brokerage"] + c["exchange"] + c["sebi"]))
    assert c["total"] == pytest.approx(sum(v for k, v in c.items() if k != "total"))


def test_short_costs_put_stt_on_the_entry_and_stamp_on_the_exit():
    """A short SELLS first. core.costs.round_trip_cost_pct assumes entry is the
    buy leg, which is wrong for shorts; paper costs are side-aware."""
    c = trade_costs("SHORT", entry=100.0, exit_px=98.0, qty=1000, cfg=CFG)
    assert c["stt"] == pytest.approx(100_000 * 0.025 / 100)
    assert c["stamp"] == pytest.approx(98_000 * 0.003 / 100)


def test_small_orders_pay_percentage_brokerage_below_the_cap():
    c = trade_costs("LONG", entry=100.0, exit_px=100.0, qty=10, cfg=CFG)
    assert c["brokerage"] == pytest.approx(2 * 1000 * 0.03 / 100)
