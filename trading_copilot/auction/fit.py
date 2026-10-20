"""Nightly: refit the day/night score on the last 250 sessions (run.py, after the history store).

Writes data/auction/weights.json (and weights_<day>.json), with the sign agreement against the
previous fit.

usage: python trading_copilot/auction/fit.py
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

from auction import core  # noqa: E402

OUT = APP / "data" / "auction"


def weights_path() -> Path:
    return OUT / "weights.json"


def load_weights() -> dict | None:
    try:
        return json.loads(weights_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main() -> int:
    import numpy as np
    import warnings
    warnings.filterwarnings("ignore")
    t0 = time.time()
    P = core.load_daily()
    if not P:
        print("no daily data")
        return 1
    w, through = core.fit(P)
    prev = load_weights()
    agree = None
    if prev:
        a = np.sign([prev["weights"].get(k, 0.0) for k in core.FACTORS])
        agree = float((a == np.sign(w.values)).mean())
    doc = {"weights": {k: round(float(v), 6) for k, v in w.items()}, "fitted_through": through,
           "fit_days": core.FIT_DAYS, "lam": core.LAM, "stocks": int(P["Close"].shape[1]),
           "fitted_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    OUT.mkdir(parents=True, exist_ok=True)
    from core.fileio import write_json_atomic
    write_json_atomic(str(weights_path()), doc)
    write_json_atomic(str(OUT / f"weights_{through}.json"), doc)
    print(f"auction weights fitted through {through} on {doc['stocks']} stocks in {time.time() - t0:.0f}s"
          + (f"; sign agreement with the previous fit {agree:.2f}" if agree is not None else "") + f": {doc['weights']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
