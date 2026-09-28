"""PaperBroker, closing side: every exit rule, marks, MAE/MFE, costs, R."""
import pytest

from paper.fills import Bar
from test_paper_broker_open import COSTS, ist, make, sig


def opened(tmp_path, **kw):
    b, clock, mirror = make(tmp_path)
    ev = b.on_signal(sig(**kw))
    assert ev["type"] == "OPEN", ev
    return b, clock, mirror


def bar(t, o, h, l):
    return Bar(ts=t, open=o, high=h, low=l, close=o)


# -- stop / target touches ------------------------------------------------------
def test_target_touch_on_a_bar_closes_at_the_target(tmp_path):
    b, clock, mirror = opened(tmp_path)
    clock.t = ist(10, 30)
    ev = b.check_touches("SAIL", [bar(ist(10, 29), 102, 104.3, 101.8)])
    assert ev["reason"] == "TARGET" and ev["exit_price"] == 104.0
    assert ev["touch_check"] == "bars" and "SAIL" not in mirror


def test_stop_gap_closes_at_the_worse_open(tmp_path):
    b, clock, _ = opened(tmp_path)
    ev = b.check_touches("SAIL", [bar(ist(10, 5), 97.2, 97.5, 96.9)])
    assert ev["reason"] == "STOP" and ev["exit_price"] == 97.2


def test_bars_from_before_the_entry_are_ignored(tmp_path):
    b, _, _ = opened(tmp_path)
    assert b.check_touches("SAIL", [bar(ist(9, 55), 99, 99, 97.0)]) is None


def test_an_already_checked_bar_does_not_retrigger_but_a_later_one_counts(tmp_path):
    b, _, _ = opened(tmp_path)
    b.check_touches("SAIL", [bar(ist(10, 1), 100, 101, 99.5)])
    assert b.check_touches("SAIL", [bar(ist(10, 0), 100, 101, 97.0)]) is None   # older than checked
    assert b.check_touches("SAIL", [bar(ist(10, 2), 99, 99, 97.9)])["reason"] == "STOP"


def test_without_bars_the_ltp_is_used_and_flagged(tmp_path):
    b, _, _ = opened(tmp_path)
    ev = b.check_touches("SAIL", None, ltp=97.9)
    assert ev["reason"] == "STOP" and ev["touch_check"] == "ltp_only"


# -- rule exits -------------------------------------------------------------------
@pytest.mark.parametrize("rule, reason", [
    ("STOP_PROXIMITY", "GATEKEEPER_STOP_PROXIMITY"),
    ("WHALE_FLIP", "GATEKEEPER_WHALE_FLIP"),
    ("SQUARE_OFF", "SQUARE_OFF"),
])
def test_a_path_a_close_exits_at_market_with_its_rule(tmp_path, rule, reason):
    b, _, _ = opened(tmp_path)
    ev = b.on_gatekeeper("SAIL", {"Action": "Close", "Exit_Rule": rule}, 98.4)
    assert ev["reason"] == reason and ev["exit_price"] == pytest.approx(98.4)   # slippage 0 in fixture


def test_hold_and_wait_do_not_exit(tmp_path):
    b, _, _ = opened(tmp_path)
    assert b.on_gatekeeper("SAIL", {"Action": "Hold"}, 100.5) is None
    assert b.on_gatekeeper("SAIL", {"Action": "Wait"}, 100.5) is None
    assert b.position_for("SAIL")


@pytest.mark.parametrize("action, reason", [("CLOSE_EXISTING", "LLM_CLOSE"),
                                            ("REVERSE_POSITION", "LLM_REVERSE")])
def test_llm_close_and_reverse_exit(tmp_path, action, reason):
    b, _, _ = opened(tmp_path)
    assert b.on_llm_directive("SAIL", action, 101.0)["reason"] == reason
    assert b.position_for("SAIL") is None                 # reverse closes; no auto re-entry


def test_square_off_closes_everything_at_1520(tmp_path):
    b, clock, mirror = opened(tmp_path)
    b.on_signal(sig(symbol="IDEA", signal_id="S2", ltp=10.0, stop=9.8, target=10.4))
    clock.t = ist(15, 19)
    assert b.square_off_all(lambda s: 100.0) == []
    clock.t = ist(15, 20)
    evs = b.square_off_all(lambda s: {"SAIL": 101.0, "IDEA": None}[s])
    assert {e["symbol"] for e in evs} == {"SAIL", "IDEA"} and not b.open and not mirror
    idea = next(e for e in evs if e["symbol"] == "IDEA")
    assert "STALE_EXIT_PRICE" in idea["flags"]            # no fresh price: last mark, flagged


# -- marks, excursions, economics -----------------------------------------------------
def test_mae_mfe_track_marks_and_bar_extremes_in_R(tmp_path):
    b, _, _ = opened(tmp_path)                            # entry 100, stop 98 → 1R = 2.0
    b.mark("SAIL", 99.0)                                  # -0.5R
    b.mark("SAIL", 102.0)                                 # +1.0R
    b.check_touches("SAIL", [bar(ist(10, 3), 101, 103.0, 98.6)])   # +1.5R / -0.7R intrabar
    p = b.position_for("SAIL")
    assert p["mae_r"] == pytest.approx(-0.7) and p["mfe_r"] == pytest.approx(1.5)


def test_close_economics_reconcile(tmp_path):
    b, _, _ = opened(tmp_path)                            # 2,500 @ 100, risk 5,000
    ev = b.check_touches("SAIL", None, ltp=104.1)         # target 104
    assert ev["gross"] == pytest.approx(2500 * 4.0)
    assert ev["net"] == pytest.approx(ev["gross"] - ev["costs"]["total"])
    assert ev["r_gross"] == pytest.approx(2.0)
    assert ev["r_net"] == pytest.approx(ev["net"] / 5000)
    assert b.realized_today() == pytest.approx(ev["net"])


def test_short_economics(tmp_path):
    b, _, _ = opened(tmp_path, action="EXECUTE_SHORT", stop=102.0, target=96.0)
    ev = b.check_touches("SAIL", None, ltp=102.3)
    assert ev["reason"] == "STOP" and ev["gross"] < 0
    # LTP-only: the stop (102) was never observed, only 102.3 -- so the fill
    # is the price actually seen, like a gap, not the stop level: -1.15R.
    assert ev["exit_price"] == pytest.approx(102.3)
    assert ev["r_gross"] == pytest.approx(-1.15)


def test_daily_loss_breaker_blocks_new_entries_after_the_limit(tmp_path):
    b, clock, _ = make(tmp_path, overrides={"max_daily_loss_pct": 0.5})   # 5,000 limit
    b.on_signal(sig())
    b.check_touches("SAIL", None, ltp=97.9)              # −1R ≈ −5,000 − costs
    assert b.realized_loss_today() >= 5000
    assert b.on_signal(sig(symbol="IDEA", signal_id="S2", ltp=10.0, stop=9.8, target=10.4))["reason"] \
        == "DAILY_LOSS_LIMIT"


def test_yesterdays_losses_do_not_trip_todays_breaker(tmp_path):
    b, clock, _ = make(tmp_path, t=ist(10, 0, day=25), overrides={"max_daily_loss_pct": 0.5})
    b.on_signal(sig())
    b.check_touches("SAIL", None, ltp=97.9)
    clock.t = ist(10, 0, day=28)
    assert b.realized_loss_today() == 0
    assert b.on_signal(sig(signal_id="S2"))["type"] == "OPEN"
