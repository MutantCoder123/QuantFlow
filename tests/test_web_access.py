"""Dashboard access from the Wi-Fi: password sign-in, the per-device and the
overall wrong-password limits, and the gate over pages, /api and /ws."""
import json

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import core.access as acc
from core.access import Access
from web_access import AccessGate

PW = "correct horse"


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def access(tmp_path):
    a = Access(tmp_path, clock=Clock())
    a.set_password(PW)
    return a


# ------------------------------------------------------------------ the rules
def test_only_a_hash_is_stored_and_short_passwords_are_refused(access, tmp_path):
    raw = (tmp_path / "access.json").read_text()
    assert PW not in raw and set(json.loads(raw)) == {"salt", "hash"}
    with pytest.raises(ValueError):
        access.set_password("short")


def test_wrong_tries_count_down_then_the_device_waits(access):
    for left in (4, 3, 2, 1):
        assert access.sign_in("192.168.1.9", "nope") == {"ok": False, "why": "wrong", "left": left}
    r = access.sign_in("192.168.1.9", "nope")
    assert r["why"] == "device_locked" and r["wait_min"] == acc.DEVICE_LOCK_MIN
    assert access.sign_in("192.168.1.9", PW)["why"] == "device_locked"      # even the right one waits
    assert access.sign_in("192.168.1.7", PW)["ok"]                          # another device is not held up
    access.clock.t += acc.DEVICE_LOCK_MIN * 60 + 1
    assert access.sign_in("192.168.1.9", "nope")["why"] == "device_locked"  # one more wrong: waits again
    access.clock.t += acc.DEVICE_LOCK_MIN * 60 + 1
    assert access.sign_in("192.168.1.9", PW)["ok"]


def test_too_many_wrong_tries_overall_shut_sign_in_until_unlocked_on_the_pc(access, tmp_path):
    for i in range(acc.MAX_TRIES_TOTAL):
        access.sign_in(f"10.0.0.{i}", "nope")                               # spread over many devices
    assert access.shut() and access.sign_in("10.0.0.200", PW) == {"ok": False, "why": "shut"}
    again = Access(tmp_path, clock=Clock())                                 # survives a restart
    assert again.shut()
    again.unlock()
    assert again.sign_in("10.0.0.200", PW)["ok"]


def test_a_good_sign_in_resets_the_overall_count(access):
    for _ in range(3):
        access.sign_in("10.0.0.1", "nope")
    assert access.sign_in("10.0.0.1", PW)["ok"] and access.total_fails() == 0


def test_sessions_expire_and_sign_out_ends_one(access):
    sid = access.sign_in("10.0.0.1", PW)["session"]
    assert access.session_ok(sid) and not access.session_ok("forged") and not access.session_ok(None)
    access.sign_out(sid)
    assert not access.session_ok(sid)
    sid = access.sign_in("10.0.0.1", PW)["session"]
    access.clock.t += acc.SESSION_HOURS * 3600 + 1
    assert not access.session_ok(sid)


def test_a_new_password_signs_everyone_out(access):
    sid = access.sign_in("10.0.0.1", PW)["session"]
    access.set_password("another long one")
    assert not access.session_ok(sid)


# ------------------------------------------------------------------ the gate
async def ws_echo(ws):
    await ws.accept()
    await ws.send_text("live")
    await ws.close()


def gated(access):
    inner = Starlette(routes=[Route("/", lambda r: PlainTextResponse("desk")),
                              Route("/api/state", lambda r: JSONResponse({"ok": 1})),
                              WebSocketRoute("/ws", ws_echo)])
    return AccessGate(inner, access)


def test_another_device_must_sign_in_for_pages_api_and_socket(access):
    c = TestClient(gated(access), follow_redirects=False)              # client host "testclient"
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/api/state").status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws") as ws:
            ws.receive_text()
    page = c.get("/login")
    assert page.status_code == 200 and 'name="password"' in page.text


def test_wrong_password_says_how_many_tries_are_left(access):
    c = TestClient(gated(access), follow_redirects=False)
    r = c.post("/login", data={"password": "nope"})
    assert r.status_code == 401 and "4 tries left on this device" in r.text
    assert "set-cookie" not in r.headers


def test_signing_in_opens_everything_until_sign_out(access):
    c = TestClient(gated(access), follow_redirects=False)
    r = c.post("/login", data={"password": PW})
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert c.get("/").text == "desk" and c.get("/api/state").json() == {"ok": 1}
    with c.websocket_connect("/ws") as ws:
        assert ws.receive_text() == "live"
    c.get("/logout")
    assert c.get("/").status_code == 303


def test_the_pc_itself_needs_no_password(access):
    c = TestClient(gated(access), client=("127.0.0.1", 50000))
    assert c.get("/").text == "desk"


def test_the_page_shows_the_lock_when_sign_in_is_shut(access):
    for i in range(acc.MAX_TRIES_TOTAL):
        access.sign_in(f"10.0.0.{i}", "nope")
    c = TestClient(gated(access), follow_redirects=False)
    assert "set_password.py --unlock" in c.get("/login").text
    assert c.post("/login", data={"password": PW}).status_code == 429


def test_over_https_the_session_cookie_is_secure(access):
    c = TestClient(gated(access), base_url="https://testserver", follow_redirects=False)
    assert "Secure" in c.post("/login", data={"password": PW}).headers["set-cookie"]
    plain = TestClient(gated(access), follow_redirects=False)
    assert "Secure" not in plain.post("/login", data={"password": PW}).headers["set-cookie"]


def test_the_certificate_covers_the_wifi_address_and_is_remade_when_it_changes(tmp_path):
    from cryptography import x509
    from core.tls import ensure_cert
    cert, key = ensure_cert(tmp_path, "192.170.12.112")
    sans = x509.load_pem_x509_certificate(cert.read_bytes()).extensions.get_extension_for_class(
        x509.SubjectAlternativeName).value
    assert "192.170.12.112" in {str(i) for i in sans.get_values_for_type(x509.IPAddress)}
    first = cert.read_bytes()
    assert ensure_cert(tmp_path, "192.170.12.112")[0].read_bytes() == first      # kept
    assert ensure_cert(tmp_path, "10.0.0.5")[0].read_bytes() != first            # new address: remade
