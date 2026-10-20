"""Who may open the dashboard from another device on the Wi-Fi.

The PC itself (127.0.0.1) is always let in. Any other device must sign in with
the dashboard password. Wrong passwords are counted:

  - one device: MAX_TRIES_PER_DEVICE wrong in a row -> that device waits
    DEVICE_LOCK_MIN minutes, and every further wrong one restarts the wait;
  - all devices together: MAX_TRIES_TOTAL wrong since the last good sign-in ->
    sign-in from other devices is shut until it is unlocked on the PC
    (python trading_copilot/scripts/set_password.py --unlock). This survives a
    restart, so an attacker cannot reset it.

Only a scrypt hash of the password is stored (data/access.json, gitignored).
A signed-in device holds a random session id in an HttpOnly, SameSite=Strict
cookie, valid SESSION_HOURS.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

MAX_TRIES_PER_DEVICE = 5
DEVICE_LOCK_MIN = 15
MAX_TRIES_TOTAL = 20
SESSION_HOURS = 12
MIN_PASSWORD_LEN = 8
COOKIE = "qf_session"
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Access:
    def __init__(self, data_dir: Path, clock=time.time):
        self.secret_path = Path(data_dir) / "access.json"
        self.state_path = Path(data_dir) / "access_state.json"
        self.clock = clock
        self._lock = threading.Lock()
        # session id -> (expiry, the password hash it was issued under). session_ok compares that
        # hash with the one on disk, so a password changed by ANY process (scripts/set_password.py
        # runs outside the server) signs every device out at once.
        self._sessions: dict[str, tuple[float, str]] = {}
        self._device: dict[str, dict] = {}              # ip -> {"fails", "until"}

    # ------------------------------------------------------------ the password
    def has_password(self) -> bool:
        return bool(_read_json(self.secret_path).get("hash"))

    def set_password(self, password: str) -> None:
        if len(password) < MIN_PASSWORD_LEN:
            raise ValueError(f"the password needs at least {MIN_PASSWORD_LEN} characters")
        salt = secrets.token_bytes(16)
        _write_json(self.secret_path, {"salt": salt.hex(), "hash": _hash(password, salt).hex()})
        self.unlock()
        with self._lock:
            self._sessions.clear()                      # a new password signs everyone out

    def _current_hash(self) -> str:
        return str(_read_json(self.secret_path).get("hash") or "")

    def _check(self, password: str) -> bool:
        d = _read_json(self.secret_path)
        if not d.get("hash"):
            return False
        return hmac.compare_digest(_hash(password, bytes.fromhex(d["salt"])), bytes.fromhex(d["hash"]))

    # ------------------------------------------------------------ locks
    def total_fails(self) -> int:
        return int(_read_json(self.state_path).get("fails", 0))

    def shut(self) -> bool:
        return self.total_fails() >= MAX_TRIES_TOTAL

    def unlock(self) -> None:
        _write_json(self.state_path, {"fails": 0})
        with self._lock:
            self._device.clear()

    def device_wait_s(self, ip: str) -> float:
        d = self._device.get(ip)
        return max(0.0, d["until"] - self.clock()) if d else 0.0

    # ------------------------------------------------------------ sign in
    def sign_in(self, ip: str, password: str) -> dict:
        """{"ok": True, "session": id} or {"ok": False, "why": "wrong"|"device_locked"|"shut",
        "left": tries left on this device, "wait_min": minutes to wait}."""
        with self._lock:
            if self.shut():
                return {"ok": False, "why": "shut"}
            wait = self.device_wait_s(ip)
            if wait > 0:
                return {"ok": False, "why": "device_locked", "wait_min": int(wait // 60) + 1}
            if self._check(password):
                self._device.pop(ip, None)
                _write_json(self.state_path, {"fails": 0})
                sid = secrets.token_urlsafe(32)
                self._sessions[sid] = (self.clock() + SESSION_HOURS * 3600, self._current_hash())
                return {"ok": True, "session": sid}
            d = self._device.setdefault(ip, {"fails": 0, "until": 0.0})
            d["fails"] += 1
            total = self.total_fails() + 1
            _write_json(self.state_path, {"fails": total})
            if total >= MAX_TRIES_TOTAL:
                return {"ok": False, "why": "shut"}
            if d["fails"] >= MAX_TRIES_PER_DEVICE:
                d["until"] = self.clock() + DEVICE_LOCK_MIN * 60
                return {"ok": False, "why": "device_locked", "wait_min": DEVICE_LOCK_MIN}
            return {"ok": False, "why": "wrong", "left": MAX_TRIES_PER_DEVICE - d["fails"]}

    def session_ok(self, sid: str | None) -> bool:
        if not sid:
            return False
        with self._lock:
            entry = self._sessions.get(sid)
            if entry is None:
                return False
            exp, issued_under = entry
            current = self._current_hash()
            if exp < self.clock() or not current or not hmac.compare_digest(issued_under, current):
                self._sessions.pop(sid, None)          # expired, or the password changed / was removed
                return False
            return True

    def sign_out(self, sid: str | None) -> None:
        with self._lock:
            self._sessions.pop(sid or "", None)
