"""Nightly: refit the alpha score's weights on the last 60 sessions (run.py, after the history store).

Builds the panel for the last 90 sessions from the local store (60 to fit + warm-up for the
20-day factors), labels every decision bar with the v5.1 trade outcome, fits the ridge, and
writes data/alpha/weights.json (also kept as weights_<day>.json). It also logs the fit's sign
stability against the previous weights.

usage: python trading_copilot/alpha/fit.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from alpha import core  # noqa: E402

OUT = APP / "data" / "alpha"
SESSIONS, LAM, PANEL_DAYS = 60, 0.5, 90


def weights_path() -> Path:
    return OUT / "weights.json"


def load_weights() -> dict | None:
    try:
        return json.loads(weights_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main() -> int:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    P = core.panel_from_store(last_days=PANEL_DAYS)
    X = core.labelled(P)
    w = core.fit(X, sessions=SESSIONS, lam=LAM)
    last = str(P["days"][-1])
    prev = load_weights()
    same = None
    if prev:
        pw = prev.get("weights", {})
        both = [f for f in w.index if f in pw]
        same = sum((w[f] > 0) == (pw[f] > 0) for f in both) / max(1, len(both))
    doc = {"fitted_through": last, "sessions": SESSIONS, "lam": LAM, "rows": int(len(X)),
           "stocks": int(len(P["syms"])), "weights": {k: round(float(v), 8) for k, v in w.items()},
           "sign_agreement_with_previous": None if same is None else round(same, 3)}
    text = json.dumps(doc, indent=1)
    from core.fileio import write_json_atomic
    write_json_atomic(str(weights_path()), doc)
    (OUT / f"weights_{last}.json").write_text(text, encoding="utf-8")
    print(f"alpha weights fitted through {last} on {len(X):,} rows ({len(P['syms'])} stocks) in "
          f"{time.time() - t0:.0f}s; sign agreement with the previous fit: {doc['sign_agreement_with_previous']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
