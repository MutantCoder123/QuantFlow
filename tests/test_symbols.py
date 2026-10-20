"""One bare stock name everywhere: the series suffix goes, a hyphenated name
stays whole (BAJAJ-AUTO was cut to "BAJAJ" and never found its history)."""
import pytest

from core.symbols import base_symbol


@pytest.mark.parametrize("raw,want", [
    ("NSE_EQ|SAIL-EQ", "SAIL"), ("SAIL", "SAIL"), (" sail ", "SAIL"), ("SAIL-BE", "SAIL"),
    ("BAJAJ-AUTO", "BAJAJ-AUTO"), ("NSE_EQ|BAJAJ-AUTO", "BAJAJ-AUTO"), ("NAM-INDIA-EQ", "NAM-INDIA"),
    ("M&M", "M&M"), ("GVT&D", "GVT&D"), ("", ""), (None, ""),
])
def test_base_symbol(raw, want):
    assert base_symbol(raw) == want


def test_the_engine_and_the_watchlist_agree_on_bajaj_auto(tmp_path, monkeypatch):
    from core import watchlist as wl
    f = tmp_path / "watchlist.csv"
    f.write_text("Token,Symbol,Exchange\n16669,BAJAJ-AUTO,NSE\n2963,SAIL,NSE\n")
    monkeypatch.setattr(wl, "WATCHLIST_PATH", f, raising=False)
    from reasoning_engine import ReasoningEngine
    from rolling_state_engine import RollingStateEngine
    eng = RollingStateEngine.__new__(RollingStateEngine)
    eng.watchlist = {"NSE_EQ|BAJAJ-AUTO": {"Symbol": "BAJAJ-AUTO"}}
    assert eng._resolve_symbol("NSE_EQ|BAJAJ-AUTO") == "BAJAJ-AUTO"     # finds BAJAJ-AUTO_1D.parquet
    assert ReasoningEngine._normalize_symbol("NSE_EQ|BAJAJ-AUTO") == "BAJAJ-AUTO"
