"""A stock removed from the watchlist gets no new trades at once, while a
position already open on it is still managed (operator report 2026-10-02:
removed stocks kept producing signals and paper trades until a restart)."""
import os
import time

import pytest

from core import watchlist as wl
from test_paper_broker_open import make, sig


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(wl, "_cache", {"mtime": None, "symbols": None})


def write(path, *symbols):
    path.write_text("Token,Symbol,Exchange\n" + "".join(f"{i},{s},NSE\n" for i, s in enumerate(symbols)),
                    encoding="utf-8")
    t = time.time() + len(symbols) * 0.001 + os.getpid() % 7
    os.utime(path, (t, t))                       # a new mtime, as a real save would give


def test_symbols_are_bare_and_follow_the_file(tmp_path):
    f = tmp_path / "watchlist.csv"
    write(f, "SAIL-EQ", "INFY")
    assert wl.symbols(f) == {"SAIL", "INFY"}
    assert wl.on_watchlist("NSE_EQ|SAIL-EQ", f) and wl.on_watchlist("infy", f)
    write(f, "INFY")                             # SAIL removed in Settings
    assert not wl.on_watchlist("SAIL", f) and wl.on_watchlist("INFY", f)


def test_a_read_mid_write_keeps_the_last_good_list(tmp_path):
    f = tmp_path / "watchlist.csv"
    write(f, "SAIL", "INFY")
    assert wl.on_watchlist("SAIL", f)
    f.write_text("Token,Symbol,Exchange\n", encoding="utf-8")   # truncated: header only
    os.utime(f, (time.time() + 99, time.time() + 99))
    assert wl.on_watchlist("SAIL", f)


def test_with_no_list_yet_nothing_is_blocked(tmp_path):
    assert wl.on_watchlist("SAIL", tmp_path / "missing.csv")


def test_the_paper_account_rejects_an_entry_on_a_removed_stock(tmp_path):
    b, _, _ = make(tmp_path)
    ev = b.on_signal(sig(off_watchlist=True))
    assert ev["type"] == "REJECT" and ev["reason"] == "NOT_ON_WATCHLIST"
    assert b.on_signal(sig())["type"] == "OPEN"


def test_an_open_position_on_a_removed_stock_still_exits(tmp_path):
    b, _, _ = make(tmp_path)
    b.on_signal(sig(target=104.0))
    ev = b.check_touches("SAIL", None, ltp=97.9)                       # removal changes nothing here
    assert ev["type"] == "CLOSE" and ev["reason"] == "STOP"


def test_the_ticket_carries_the_watchlist_state(monkeypatch):
    from paper import runtime
    seen = {}

    class B:
        def on_signal(self, s):
            seen.update(s)

    monkeypatch.setattr(runtime, "_broker", B())
    monkeypatch.setattr(runtime, "_on_watchlist", lambda s: s != "SAIL")
    monkeypatch.setattr(runtime, "_data_missing", lambda raw: [])
    runtime.on_ticket("SAIL", {"action_directive": "EXECUTE_LONG"}, {}, {}, {}, {"ltp": 100}, "S1", False)
    assert seen["off_watchlist"] is True
    runtime.on_ticket("INFY", {"action_directive": "EXECUTE_LONG"}, {}, {}, {}, {"ltp": 100}, "S2", False)
    assert seen["off_watchlist"] is False
