"""Average daily volume (data_readiness needs it): from the daily candles, so a
short 5-minute warm-up (19 sessions on 2026-10-06) no longer zeroes it."""
import pandas as pd

from rolling_state_engine import adv_from_daily


def daily(n, last=None, vol=1000.0):
    last = pd.Timestamp(last or pd.Timestamp.now(tz="Asia/Kolkata").normalize().tz_localize(None))
    days = pd.bdate_range(end=last, periods=n)
    return pd.DataFrame({"timestamp": days, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                         "volume": [vol] * (n - 1) + [vol * 50]})


def test_adv_is_the_mean_of_the_last_20_complete_sessions():
    yday = pd.Timestamp.now().normalize() - pd.offsets.BDay(1)
    d = daily(100, last=yday)
    d.loc[d.index[-1], "volume"] = 1000.0
    assert adv_from_daily(d) == 1000.0


def test_todays_partial_candle_is_left_out():
    d = daily(30)                                  # last row is today, with an odd volume
    assert adv_from_daily(d) == 1000.0


def test_too_few_sessions_or_no_data_gives_zero():
    assert adv_from_daily(daily(5)) == 0.0
    assert adv_from_daily(None) == 0.0
    assert adv_from_daily(pd.DataFrame()) == 0.0


def test_prev_close_is_the_last_session_before_today_not_the_row_before_last():
    from rolling_state_engine import prev_close_from_daily
    yday = pd.Timestamp.now().normalize() - pd.offsets.BDay(1)
    d = daily(30, last=yday)
    d["close"] = range(30)
    assert prev_close_from_daily(d) == 29.0          # last row is yesterday: take it (old code took 28)
    today = daily(30)
    today["close"] = range(30)
    assert prev_close_from_daily(today) == 28.0      # last row is today: the one before
    assert prev_close_from_daily(None) == 0.0
