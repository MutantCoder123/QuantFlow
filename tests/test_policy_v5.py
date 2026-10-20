"""Policy v5: a target out of reach is pulled in to 3 x ATR15 from the
price, and a trade whose score has faded below half its entry value for
10 minutes has its stop moved to breakeven (once +0.5R is open)."""
import pytest

from core.policy_config import load_policy
from paper.broker import PaperBroker
from paper.store import EventStore, rebuild
from test_paper_broker_open import COSTS, Clock, Mirror, ist, make, sig

TRAIL = [[1.5, 0.0], [2.5, 1.0]]


def v5(tmp_path, t=ist(10, 0), reach=3.0, fade=0.5, run_on=1.0):
    """Entry 100, stop 98 (1R = 2)."""
    base, _, _ = make(tmp_path)
    clock = Clock(t)
    b = PaperBroker(store=EventStore(tmp_path / "events"), settings=base.settings, costs_cfg=COSTS,
                    clock=clock, mirror=Mirror(), entry_cutoff="13:40", square_off="15:10",
                    trail=TRAIL, reversal_cooldown_min=60, run_on_trail_r=run_on,
                    thesis_against=0.15, thesis_minutes=10, review_every_min=5,
                    reach_atr=reach, reach_floor_r=1.0, reach_every_min=5,
                    fade_frac=fade, fade_minutes=10, fade_min_open_r=0.5)
    return b, clock


def kinds(b):
    return [e["kind"] for e in b.store.load() if e["type"] == "REVIEW"]


def test_v5_policy_file():
    from paths import CONFIG_DIR
    p = load_policy(CONFIG_DIR / "policy_v5.yaml")          # v5.1 (v5 + stop floor) is live
    assert p.version == 5
    assert p.horizon["reach_target"] == {"atr_mult": 3.0, "floor_r": 1.0, "every_min": 5}
    assert p.horizon["memory"] == {"fade_frac": 0.5, "minutes": 10, "min_open_r": 0.5}
    assert p.horizon["run_on"] == {"trail_r": 1.0}                       # v4's exits kept


# ------------------------------------------------------------ reachable target
def test_a_target_out_of_reach_is_pulled_in_to_3_atr(tmp_path):
    b, _ = v5(tmp_path)
    b.on_signal(sig(target=115.0))                                       # 7.5R away
    b.mark("SAIL", 100.5)
    assert b.on_score("SAIL", 0.4, atr15=1.0) is None
    assert b.position_for("SAIL")["target"] == pytest.approx(103.5)      # 100.5 + 3 x 1.0
    assert "TARGET_PULLED" in kinds(b)


def test_a_reachable_target_is_left_alone_and_never_pushed_out(tmp_path):
    b, clock = v5(tmp_path)
    b.on_signal(sig(target=103.0))
    b.on_score("SAIL", 0.4, atr15=1.0)                                   # 103 is within 3 ATR
    assert b.position_for("SAIL")["target"] == 103.0
    clock.t = ist(10, 10)
    b.on_score("SAIL", 0.4, atr15=5.0)                                   # wider ATR: still 103
    assert b.position_for("SAIL")["target"] == 103.0


def test_the_target_never_comes_inside_entry_plus_1R(tmp_path):
    b, _ = v5(tmp_path)
    b.on_signal(sig(target=115.0))
    b.mark("SAIL", 99.0)
    b.on_score("SAIL", 0.4, atr15=0.3)                                   # 99 + 0.9 = 99.9 < 102
    assert b.position_for("SAIL")["target"] == pytest.approx(102.0)


def test_it_rechecks_only_every_five_minutes(tmp_path):
    b, clock = v5(tmp_path)
    b.on_signal(sig(target=115.0))
    b.on_score("SAIL", 0.4, atr15=2.0)                                   # -> 106
    clock.t = ist(10, 2)
    b.on_score("SAIL", 0.4, atr15=1.0)                                   # too soon
    assert b.position_for("SAIL")["target"] == pytest.approx(106.0)
    clock.t = ist(10, 5)
    b.on_score("SAIL", 0.4, atr15=1.0)
    assert b.position_for("SAIL")["target"] == pytest.approx(103.0)


def test_a_pulled_in_target_already_passed_starts_the_run_on(tmp_path):
    b, _ = v5(tmp_path, reach=1.0)
    b.on_signal(sig(target=115.0))
    b.mark("SAIL", 104.0)
    b.on_score("SAIL", 0.4, atr15=0.5)                                   # 104.5? no: floor 102 vs 104.5
    p = b.position_for("SAIL")
    assert p["target"] == pytest.approx(104.5) and not p.get("past_target")
    b.mark("SAIL", 104.6)
    assert b.check_touches("SAIL", None, ltp=104.6) is None              # run on, not closed
    assert b.position_for("SAIL")["past_target"]


def test_a_short_target_is_pulled_up(tmp_path):
    b, _ = v5(tmp_path)
    b.on_signal(sig(action="EXECUTE_SHORT", stop=102.0, target=85.0))
    b.on_score("SAIL", -0.4, atr15=1.0)
    assert b.position_for("SAIL")["target"] == pytest.approx(97.0)


def test_no_atr_means_no_change(tmp_path):
    b, _ = v5(tmp_path)
    b.on_signal(sig(target=115.0))
    b.on_score("SAIL", 0.4, atr15=None)
    b.on_score("SAIL", 0.4, atr15=0)
    assert b.position_for("SAIL")["target"] == 115.0


def test_a_restart_keeps_the_pulled_in_target(tmp_path):
    b, _ = v5(tmp_path)
    b.on_signal(sig(target=115.0))
    b.on_score("SAIL", 0.4, atr15=1.0)
    (pos,) = rebuild(b.store.load()).open.values()
    assert pos["target"] == pytest.approx(103.0)


def test_without_the_policy_targets_stay_as_set(tmp_path):
    b, _ = v5(tmp_path, reach=None)
    b.on_signal(sig(target=115.0))
    b.on_score("SAIL", 0.4, atr15=1.0)
    assert b.position_for("SAIL")["target"] == 115.0


# ------------------------------------------------------------ memory: fade
def faded(b, clock, minutes, score, start=1):
    for m in range(start, start + minutes):
        clock.t = ist(10, m)
        b.on_score("SAIL", score)


def test_a_faded_score_moves_the_stop_to_breakeven_once_in_profit(tmp_path):
    b, clock = v5(tmp_path, reach=None)
    b.on_signal(sig(target=110.0, composite=0.6))
    b.mark("SAIL", 101.2)                                                # +0.6R open
    faded(b, clock, 11, 0.2)                                             # 0.2 < 0.3 for 10 min
    p = b.position_for("SAIL")
    assert p["trail_stop"] == 100.0 and "FADE_TIGHTEN" in kinds(b)
    ev = b.check_touches("SAIL", None, ltp=99.9)
    assert ev["reason"] == "TRAIL_STOP"


def test_a_fade_without_open_profit_changes_nothing(tmp_path):
    b, clock = v5(tmp_path, reach=None)
    b.on_signal(sig(target=110.0, composite=0.6))
    b.mark("SAIL", 100.4)                                                # +0.2R only
    faded(b, clock, 12, 0.2)
    assert "trail_stop" not in b.position_for("SAIL")


def test_a_score_that_holds_up_is_not_a_fade(tmp_path):
    b, clock = v5(tmp_path, reach=None)
    b.on_signal(sig(target=110.0, composite=0.6))
    b.mark("SAIL", 101.2)
    faded(b, clock, 15, 0.35)                                            # above half of 0.6
    assert "trail_stop" not in b.position_for("SAIL")


def test_a_fade_must_last_ten_minutes(tmp_path):
    b, clock = v5(tmp_path, reach=None)
    b.on_signal(sig(target=110.0, composite=0.6))
    b.mark("SAIL", 101.2)
    faded(b, clock, 6, 0.2)
    faded(b, clock, 1, 0.5, start=7)                                     # recovers
    faded(b, clock, 6, 0.2, start=8)
    assert "trail_stop" not in b.position_for("SAIL")


def test_a_short_fades_when_its_negative_score_weakens(tmp_path):
    b, clock = v5(tmp_path, reach=None)
    b.on_signal(sig(action="EXECUTE_SHORT", stop=102.0, target=90.0, composite=-0.6))
    b.mark("SAIL", 98.8)
    faded(b, clock, 11, -0.1)
    assert b.position_for("SAIL")["trail_stop"] == 100.0


def test_a_restart_keeps_the_breakeven_stop(tmp_path):
    b, clock = v5(tmp_path, reach=None)
    b.on_signal(sig(target=110.0, composite=0.6))
    b.mark("SAIL", 101.2)
    faded(b, clock, 11, 0.2)
    (pos,) = rebuild(b.store.load()).open.values()
    assert pos["trail_stop"] == 100.0
