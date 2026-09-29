"""An Upstox access token dies at the next 03:30 IST, whenever it was issued.

_is_token_valid treated a saved token as good for 24 h. A token saved at
00:18 on 2026-09-26 was rejected (401 UDAPI100050) by 11:21 the same
morning while the app kept logging "Found valid cached Upstox token" --
so it started every service on a dead token instead of asking for a login.
"""
import json
from datetime import datetime

import pytest

from data_services.upstox_feed import UpstoxAuthenticator, jwt_expiry, token_expiry


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


def _jwt(exp):
    import base64
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'HS256'})}.{enc({'exp': exp, 'isExtended': True})}.sig"


def test_an_extended_token_lives_until_its_own_expiry_claim(tmp_path, monkeypatch):
    """The long-lived analytics token was saved months ago; the 03:30 rule
    would call it dead every morning and ask for a login."""
    import data_services.upstox_feed as uf
    exp = int(datetime(2027, 6, 20, 3, 30).timestamp())       # this machine's local time is IST
    tok = _jwt(exp)
    assert jwt_expiry(tok) == datetime.fromtimestamp(exp, uf._IST).replace(tzinfo=None)
    a = _auth(tmp_path, "2026-06-19T10:34:07")
    a.token_file.write_text(json.dumps({"access_token": tok, "timestamp": "2026-06-19T10:34:07"}))
    monkeypatch.setattr(uf, "_now_ist", lambda: datetime(2026, 9, 29, 9, 15))
    assert a._is_token_valid() == tok
    monkeypatch.setattr(uf, "_now_ist", lambda: jwt_expiry(tok))
    assert a._is_token_valid() is False


def test_a_token_without_an_expiry_claim_falls_back_to_the_0330_rule():
    assert jwt_expiry("tok") is None and jwt_expiry("a.bm90LWpzb24.c") is None
