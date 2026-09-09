"""Session-boundary reset.

Four of seven per-token accumulators were previously omitted from the daily
reset, so the intraday POC accumulated across days and whale-CVD history
carried the previous session into today's EMA and slope.
"""
import pytest
from freezegun import freeze_time

from microstructure_engine import MicrostructureEngine as M

TOKEN = "NSE_EQ|TESTSYM"
_STATE_DICTS = ("cvd_state", "vol_profile_state", "session_vwap_state",
                "whale_cvd_state", "whale_cvd_history", "last_vtt_state",
                "last_bba_state", "session_date")


@pytest.fixture(autouse=True)
def clean_state():
    for name in _STATE_DICTS:
        if hasattr(M, name):
            getattr(M, name).clear()
    yield


def _tick(vtt, price=100.0):
    return M.generate_microstructure_payload({
        "token": TOKEN, "price": price, "volume": vtt,
        "bids": [{"quantity": 10, "price": price - 0.05}],
        "asks": [{"quantity": 10, "price": price + 0.05}],
    })


def test_every_per_token_dict_is_cleared_on_new_session():
    with freeze_time("2026-09-09 10:00:00+05:30"):
        _tick(1_000_000.0)
        _tick(1_500_000.0, price=101.0)
        assert M.vol_profile_state[TOKEN]
        assert TOKEN in M.last_vtt_state

    with freeze_time("2026-09-10 09:16:00+05:30"):
        _tick(2_000.0)
        assert len(M.vol_profile_state[TOKEN]) == 1
        assert M.cvd_state.get(TOKEN, 0) == 0
        assert len(M.whale_cvd_history.get(TOKEN, [])) <= 1


def test_poc_does_not_carry_yesterdays_prices():
    with freeze_time("2026-09-09 10:00:00+05:30"):
        _tick(1_000_000.0, price=500.0)
        _tick(1_100_000.0, price=500.0)

    with freeze_time("2026-09-10 09:16:00+05:30"):
        _tick(1_000.0, price=100.0)
        _tick(5_000.0, price=100.0)
        assert M.calculate_poc(TOKEN, 100.0) == 100


def test_session_vwap_resets_across_days():
    with freeze_time("2026-09-09 10:00:00+05:30"):
        _tick(1_000_000.0, price=500.0)
        _tick(1_100_000.0, price=500.0)

    with freeze_time("2026-09-10 09:16:00+05:30"):
        _tick(1_000.0, price=100.0)
        out = _tick(5_000.0, price=100.0)
        assert out["session_vwap"] == pytest.approx(100.0)


def test_roll_is_idempotent_within_a_session():
    with freeze_time("2026-09-09 10:00:00+05:30"):
        assert M.roll_session_if_needed(TOKEN) is True
        assert M.roll_session_if_needed(TOKEN) is False
        _tick(1_000_000.0)
        _tick(1_010_000.0)
        assert M.roll_session_if_needed(TOKEN) is False
        assert M.last_vtt_state[TOKEN] == 1_010_000.0
