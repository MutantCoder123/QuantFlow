"""A restart must neither lose a position nor double-count one."""
from test_paper_broker_open import Mirror, Clock, ist, make, sig

from paper.broker import PaperBroker


def _restart(b, clock, t=None):
    """A fresh broker over the same log and settings, as after a crash."""
    if t is not None:
        clock.t = t
    b2 = PaperBroker(store=b.store, settings=b.settings, costs_cfg=b.costs_cfg,
                     clock=clock, mirror=Mirror(), entry_cutoff="13:45", square_off="15:20")
    stale = b2.recover()
    return b2, stale


def test_restart_mid_session_restores_open_positions_and_mirror(tmp_path):
    b, clock, _ = make(tmp_path)
    b.on_signal(sig(signal_id="S1"))
    b.on_signal(sig(symbol="IDEA", signal_id="S2", ltp=10.0, stop=9.8, target=10.4))
    b.mark("SAIL", 101.0)
    b.equity_snapshot()

    b2, stale = _restart(b, clock)
    assert stale == 0
    assert {p["symbol"] for p in b2.open.values()} == {"SAIL", "IDEA"}
    assert set(b2.mirror) == {"SAIL", "IDEA"} and b2.mirror["SAIL"]["source"] == "paper"
    sail = b2.position_for("SAIL")
    assert sail["mfe_r"] == b.position_for("SAIL")["mfe_r"] and sail["last"] == 101.0


def test_restart_after_a_close_counts_the_trade_once(tmp_path):
    b, clock, _ = make(tmp_path)
    b.on_signal(sig())
    b.check_touches("SAIL", bars=None, ltp=104.5)                  # target
    b2, _ = _restart(b, clock)
    assert not b2.open and len(b2.closed) == 1
    assert b2.closed[0]["net"] == b.closed[0]["net"]
    assert b2.realized_today() == b.realized_today()


def test_a_position_left_open_from_a_previous_day_is_closed_and_flagged(tmp_path):
    b, clock, _ = make(tmp_path, t=ist(11, 0, day=25))
    b.on_signal(sig())
    b.mark("SAIL", 99.0)
    b.equity_snapshot()                          # last known price persisted
    b2, stale = _restart(b, clock, t=ist(9, 20, day=28))
    assert stale == 1 and not b2.open and "SAIL" not in b2.mirror
    t = b2.closed[-1]
    assert t["reason"] == "RECOVERED_STALE" and "STALE_EXIT_PRICE" in t["flags"]
    assert t["exit_price"] == 99.0
