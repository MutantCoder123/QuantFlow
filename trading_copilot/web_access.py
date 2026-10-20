"""The sign-in gate in front of the web server when it is open to the Wi-Fi
(QF_LAN=1). Pure ASGI, so it covers pages, /api calls and the /ws socket alike.

  - the PC itself (127.0.0.1 / ::1) passes: the local browser and the shadow
    service keep working without a password;
  - /login (GET: the page, POST: the password), /static/login/ and the design
    tokens are open;
  - anything else needs a valid session cookie (core.access): a page is sent
    to /login, an /api call gets 401, a socket is closed (1008) unaccepted.
"""
from __future__ import annotations

import html
import os
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

from core.access import COOKIE, MAX_TRIES_TOTAL, SESSION_HOURS, Access

LOOPBACK = {"127.0.0.1", "::1"}
LOGIN_PAGE = Path(__file__).resolve().parent / "static" / "login" / "login.html"
MAX_FORM_BYTES = 4096
# the sign-in page and the colour tokens it uses: no data in either
OPEN_PREFIX = "/static/login/"
OPEN_FILES = {"/static/shared/tokens.css"}


def lan_mode() -> bool:
    return os.getenv("QF_LAN", "0") == "1"


def _cookie(scope) -> str | None:
    for k, v in scope.get("headers") or []:
        if k == b"cookie":
            c = SimpleCookie()
            try:
                c.load(v.decode("latin-1"))
            except Exception:
                return None
            return c[COOKIE].value if COOKIE in c else None
    return None


def message(result: dict | None) -> str:
    if not result:
        return ""
    why = result.get("why")
    if why == "wrong":
        left = result["left"]
        return f"Wrong password. {left} {'try' if left == 1 else 'tries'} left on this device."
    if why == "device_locked":
        return (f"Too many wrong passwords from this device. Try again in {result['wait_min']} "
                f"minute{'s' if result['wait_min'] != 1 else ''}.")
    if why == "shut":
        return (f"Sign-in from other devices is locked after {MAX_TRIES_TOTAL} wrong passwords. "
                "Unlock it on the PC: python trading_copilot/scripts/set_password.py --unlock")
    return ""


class AccessGate:
    def __init__(self, app, access: Access):
        self.app, self.access = app, access

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        ip = (scope.get("client") or ("", 0))[0]
        if ip in LOOPBACK:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if scope["type"] == "http" and path == "/login":
            return await self._login(scope, receive, send, ip)
        if scope["type"] == "http" and path == "/logout":
            self.access.sign_out(_cookie(scope))
            return await self._redirect(send, "/login", clear=True)
        if path.startswith(OPEN_PREFIX) or path in OPEN_FILES:
            return await self.app(scope, receive, send)
        if self.access.session_ok(_cookie(scope)):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        if path.startswith("/api/"):
            return await self._send(send, 401, b'{"status":"error","message":"Sign in first."}',
                                    "application/json")
        return await self._redirect(send, "/login")

    async def _login(self, scope, receive, send, ip):
        if scope["method"] == "GET":
            return await self._page(send, message({"why": "shut"}) if self.access.shut() else "")
        if scope["method"] != "POST":
            return await self._send(send, 405, b"", "text/plain")
        body = b""
        while True:
            m = await receive()
            body += m.get("body", b"")
            if len(body) > MAX_FORM_BYTES:
                return await self._send(send, 413, b"", "text/plain")
            if not m.get("more_body"):
                break
        password = (parse_qs(body.decode("utf-8", "replace")).get("password") or [""])[0]
        r = self.access.sign_in(ip, password)
        if r["ok"]:
            return await self._redirect(send, "/", session=r["session"], secure=scope.get("scheme") == "https")
        return await self._page(send, message(r), status=401 if r["why"] == "wrong" else 429)

    async def _page(self, send, msg: str, status: int = 200):
        page = LOGIN_PAGE.read_text(encoding="utf-8")
        note = f'<p class="lg-note" role="alert">{html.escape(msg)}</p>' if msg else ""
        await self._send(send, status, page.replace("<!--note-->", note).encode("utf-8"), "text/html; charset=utf-8")

    async def _redirect(self, send, to: str, session: str | None = None, clear: bool = False,
                        secure: bool = False):
        headers = [(b"location", to.encode())]
        if session:                                  # Secure over HTTPS: never sent back in the clear
            headers.append((b"set-cookie", f"{COOKIE}={session}; Path=/; HttpOnly; SameSite=Strict; "
                                           f"Max-Age={SESSION_HOURS * 3600}{'; Secure' if secure else ''}".encode()))
        if clear:
            headers.append((b"set-cookie", f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0".encode()))
        await send({"type": "http.response.start", "status": 303, "headers": headers})
        await send({"type": "http.response.body", "body": b""})

    async def _send(self, send, status: int, body: bytes, ctype: str):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", ctype.encode()), (b"cache-control", b"no-store"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
