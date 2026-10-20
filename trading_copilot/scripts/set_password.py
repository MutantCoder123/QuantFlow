"""Set the dashboard password for devices on the Wi-Fi, or unlock sign-in.

usage: python trading_copilot/scripts/set_password.py            (asks twice; hidden)
       python trading_copilot/scripts/set_password.py --unlock   (after too many wrong tries)
       python trading_copilot/scripts/set_password.py --status

Then set QF_LAN=1 in .env and restart: the dashboard opens on http://<this PC's
Wi-Fi address>:8000 for other devices, behind the password. The PC itself never
needs it. Only a hash is stored (data/access.json).
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.access import MAX_TRIES_TOTAL, MIN_PASSWORD_LEN, Access  # noqa: E402
from paths import DATA_DIR  # noqa: E402


def main(argv: list[str]) -> int:
    a = Access(DATA_DIR)
    if "--status" in argv:
        print(f"password set: {'yes' if a.has_password() else 'no'}; wrong tries since the last good sign-in: "
              f"{a.total_fails()} of {MAX_TRIES_TOTAL}{' -- LOCKED' if a.shut() else ''}")
        return 0
    if "--unlock" in argv:
        a.unlock()
        print("Sign-in from other devices is unlocked; every device's wrong-try count is reset.")
        return 0
    pw = getpass.getpass(f"New dashboard password (at least {MIN_PASSWORD_LEN} characters): ")
    if pw != getpass.getpass("Again: "):
        print("The two entries differ; nothing changed.")
        return 1
    try:
        a.set_password(pw)
    except ValueError as e:
        print(f"Not set: {e}.")
        return 1
    print("Password set. Devices signed in before must sign in again.")
    print("Restart QuantFlow for a running dashboard to use it (QF_LAN=1 in .env opens it to the Wi-Fi).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
