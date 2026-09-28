"""Reading the journals the performance figures draw on (the only I/O in
this package besides export's byte encoding -- and it only reads)."""
from __future__ import annotations

import datetime as _dt

from performance.scope import Scope, ist_day


def load_arms(sc: Scope) -> list[dict]:
    """Arms-journal rows (every escalation, both arms) inside the range."""
    from journal.arms import ArmJournal
    from paths import SIGNALS_DIR
    first = sc.first_day or (sc.session_days[0] if sc.session_days else None)
    days = 1 if first is None else (_dt.date.today() - _dt.date.fromisoformat(first)).days + 2
    rows = ArmJournal(SIGNALS_DIR / "arms").load_all(last_n_days=max(1, days))
    out = []
    for r in rows:
        try:
            d = ist_day(int(r["ts"]))
        except (KeyError, TypeError, ValueError):
            continue
        if (sc.first_day is None or d >= sc.first_day) and (sc.last_day is None or d <= sc.last_day):
            out.append(r)
    return out
