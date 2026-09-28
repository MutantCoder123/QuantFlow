"""An Upstox access token dies at the next 03:30 IST, whenever it was issued.

_is_token_valid treated a saved token as good for 24 h. A token saved at
00:18 on 2026-09-26 was rejected (401 UDAPI100050) by 11:21 the same
morning while the app kept logging "Found valid cached Upstox token" --
so it started every service on a dead token instead of asking for a login.
"""
import json
from datetime import datetime

import pytest

from data_services.upstox_feed import UpstoxAuthenticator, token_expiry


@pytest.mark.parametrize("saved, expires", [
    ("2026-09-26T00:18:00", "2026-09-26T03:30:00"),   # after midnight: same-day 03:30
    ("2026-09-26T03:29:59", "2026-09-26T03:30:00"),
    ("2026-09-26T03:30:00", "2026-09-27T03:30:00"),   # at/after 03:30: next day's
    ("2026-09-26T09:05:00", "2026-09-27T03:30:00"),
    ("2026-09-26T23:59:00", "2026-09-27T03:30:00"),
])
def test_expiry_is_the_next_0330_ist(saved, expires):
    assert token_expiry(datetime.fromisoformat(saved)) == datetime.fromisoformat(expires)


def _auth(tmp_path, saved):
    a = UpstoxAuthenticator.__new__(UpstoxAuthenticator)
    a.token_file = tmp_path / "upstox_token.json"
    a.token_file.write_text(json.dumps({"access_token": "tok", "timestamp": saved}))
    return a


def test_the_real_failure_is_now_reported_as_expired(tmp_path, monkeypatch):
    import data_services.upstox_feed as uf
    monkeypatch.setattr(uf, "_now_ist", lambda: datetime(2026, 9, 26, 11, 21))
    assert _auth(tmp_path, "2026-09-26T00:18:00")._is_token_valid() is False


def test_a_token_is_valid_until_its_expiry(tmp_path, monkeypatch):
    import data_services.upstox_feed as uf
    monkeypatch.setattr(uf, "_now_ist", lambda: datetime(2026, 9, 26, 15, 0))
    assert _auth(tmp_path, "2026-09-26T09:05:00")._is_token_valid() == "tok"
    monkeypatch.setattr(uf, "_now_ist", lambda: datetime(2026, 9, 27, 3, 30))
    assert _auth(tmp_path, "2026-09-26T09:05:00")._is_token_valid() is False
