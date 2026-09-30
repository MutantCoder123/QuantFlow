"""Policy v2's exit and entry rules, from the 2026-09-29 paper session:
a trailing stop (breakeven at 1.5R, +1R locked at 2.5R), no entry the other
way within an hour of a losing close, and a 15:10 square-off."""
import pytest

from core.policy_config import load_policy
from paper.broker import PaperBroker
from paper.fills import Bar
from paper.store import EventStore
from test_paper_broker_open import COSTS, Clock, Mirror, ist, make, sig

TRAIL = [[1.5, 0.0], [2.5, 1.0]]


def trailing(tmp_path, t=ist(10, 0)):
    """Entry 100, stop 98 (1R = 2), target 108: the trail can reach both steps."""
    base, _, _ = make(tmp_path)
    clock = Clock(t)
    b = PaperBroker(store=EventStore(tmp_path / "events"), settings=base.settings, costs_cfg=COSTS,
                    clock=clock, mirror=Mirror(), entry_cutoff="13:40", square_off="15:10",
                    trail=TRAIL, reversal_cooldown_min=60)
    return b, clock


def bar(t, o, h, l):
    return Bar(ts=t, open=o, high=h, low=l, close=o)


def test_the_stop_moves_to_breakeven_at_1_5R_and_a_later_bar_takes_it(tmp_path):
    b, clock = trailing(tmp_path)
    assert b.on_signal(sig(target=108.0))["type"] == "OPEN"
    clock.t = ist(10, 20)
    ev = b.check_touches("SAIL", [bar(ist(10, 5), 101, 103.2, 100.5),     # best 1.6R: stop -> 100
                                  bar(ist(10, 10), 101, 101.2, 99.6)])    # a later bar comes back
    assert ev["reason"] == "TRAIL_STOP" and ev["exit_price"] == 100.0
    assert ev["r_gross"] == pytest.approx(0.0)                            # R still measured on the first stop


def test_the_bar_that_raised_the_trail_cannot_also_hit_it(tmp_path):
    """Inside one bar the high and the low are not ordered."""
    b, _ = trailing(tmp_path)
    b.on_signal(sig(target=108.0))
    assert b.check_touches("SAIL", [bar(ist(10, 5), 101, 103.2, 99.5)]) is None
    assert b.position_for("SAIL")["trail_stop"] == 100.0


def test_the_live_price_takes_a_trailed_stop_between_bars(tmp_path):
    b, _ = trailing(tmp_path)
    b.on_signal(sig(target=108.0))
    b.mark("SAIL", 103.4)                                                  # 1.7R on the tape
    ev = b.check_touches("SAIL", [bar(ist(9, 55), 99, 99, 99)], ltp=99.9)  # only an old bar
    assert ev["reason"] == "TRAIL_STOP" and ev["exit_price"] == 99.9       # the worse of price and stop


def test_at_2_5R_one_R_is_locked_and_the_trail_never_goes_back(tmp_path):
    b, _ = trailing(tmp_path)
    b.on_signal(sig(target=108.0))
    b.mark("SAIL", 105.2)
    assert b.position_for("SAIL")["trail_stop"] == 102.0
    b.mark("SAIL", 103.0)
    assert b.position_for("SAIL")["trail_stop"] == 102.0


def test_below_the_first_step_nothing_moves(tmp_path):
    b, _ = trailing(tmp_path)
    b.on_signal(sig(target=108.0))
    b.mark("SAIL", 102.9)                                                  # 1.45R
    assert "trail_stop" not in b.position_for("SAIL")
    assert b.check_touches("SAIL", None, ltp=98.0)["reason"] == "STOP"


def test_a_short_trails_downwards(tmp_path):
    b, _ = trailing(tmp_path)
    b.on_signal(sig(action="EXECUTE_SHORT", stop=102.0, target=92.0))
    b.mark("SAIL", 94.9)                                                   # 2.55R
    assert b.position_for("SAIL")["trail_stop"] == 98.0


def test_a_restart_keeps_the_trailed_stop(tmp_path):
    b, _ = trailing(tmp_path)
    b.on_signal(sig(target=108.0))
    b.mark("SAIL", 103.4)
    b.equity_snapshot()
    again, _ = trailing(tmp_path)
    again.recover()
    assert again.position_for("SAIL")["trail_stop"] == 100.0


# -- no flip straight after a loss -----------------------------------------------
def stopped_long(tmp_path):
    b, clock = trailing(tmp_path)
    b.on_signal(sig())
    clock.t = ist(10, 10)
    assert b.check_touches("SAIL", None, ltp=97.9)["net"] < 0
    return b, clock


def test_the_other_way_is_refused_for_an_hour_after_a_losing_close(tmp_path):
    b, clock = stopped_long(tmp_path)
    clock.t = ist(10, 38)                                                  # SCI flipped after 27 min
    short = sig(action="EXECUTE_SHORT", stop=102.0, target=96.0)
    assert b.on_signal(short)["reason"] == "REVERSAL_COOLDOWN"
    clock.t = ist(11, 11)
    assert b.on_signal(short)["type"] == "OPEN"


def test_the_same_way_is_not_held_back(tmp_path):
    """OLAELEC was stopped long and re-entered long 50 minutes later: +18,441."""
    b, clock = stopped_long(tmp_path)
    clock.t = ist(10, 40)
    assert b.on_signal(sig())["type"] == "OPEN"


def test_a_winning_close_does_not_hold_back_the_other_way(tmp_path):
    b, clock = trailing(tmp_path)
    b.on_signal(sig())
    clock.t = ist(10, 10)
    assert b.check_touches("SAIL", None, ltp=104.0)["reason"] == "TARGET"
    assert b.on_signal(sig(action="EXECUTE_SHORT", stop=106.0, target=98.0))["type"] == "OPEN"


# -- the policy ------------------------------------------------------------------------
def test_policy_v2_squares_off_before_the_fno_closing_auction():
    h = load_policy().horizon
    assert load_policy().version == 2
    assert h["square_off_ist"] == "15:10" and h["entry_cutoff_ist"] == "13:40"
    assert h["trail"] == TRAIL and h["reversal_cooldown_min"] == 60
    from intraday_gatekeeper import _square_off_hm
    assert _square_off_hm() == (15, 10)
