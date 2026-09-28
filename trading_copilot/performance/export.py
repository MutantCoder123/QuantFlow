"""The journal and daily equity as flat tables (CSV / Parquet) for offline
tuning work. One row per closed paper trade; nested fields are flattened."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from performance.scope import IST

COST_ITEMS = ("brokerage", "stt", "exchange", "sebi", "stamp", "gst")
TRADE_COLUMNS = (
    "pos_id", "signal_id", "config_version", "symbol", "cluster", "side", "qty",
    "opened_ist", "closed_ist", "hold_min", "ltp_at_entry", "entry_price", "stop", "target",
    "exit_ref_price", "exit_price", "reason", "touch_check", "flags", "risk_amount",
    "gross", *(f"cost_{k}" for k in COST_ITEMS), "cost_total", "net", "r_gross", "r_net",
    "mae_r", "mfe_r", "verdict", "composite", "regime", "session_phase", "attention_rank",
    "capital", "risk_per_trade_pct", "slippage_pct", "rationale",
)
DAILY_COLUMNS = ("day", "trades", "gross", "costs", "net", "return_pct", "equity_close")


def _iso(ts) -> str | None:
    return datetime.fromtimestamp(ts, IST).isoformat(timespec="seconds") if ts else None


def trade_row(t: dict) -> dict:
    ctx, cst, st = t.get("context") or {}, t.get("costs") or {}, t.get("settings") or {}
    row = {k: t.get(k) for k in TRADE_COLUMNS}
    row.update({
        "opened_ist": _iso(t.get("opened_ts")), "closed_ist": _iso(t.get("closed_ts")),
        "flags": ";".join(t.get("flags") or []),
        "cost_total": cst.get("total"),
        **{f"cost_{k}": cst.get(k) for k in COST_ITEMS},
        **{k: ctx.get(k) for k in ("verdict", "composite", "regime", "session_phase",
                                    "attention_rank", "rationale")},
        **{k: st.get(k) for k in ("capital", "risk_per_trade_pct", "slippage_pct")},
    })
    return row


def to_csv(rows: list[dict], columns) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def to_parquet(rows: list[dict], columns) -> bytes:
    import pandas as pd
    df = pd.DataFrame(rows, columns=list(columns))
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


def encode(rows: list[dict], columns, fmt: str) -> bytes:
    if fmt == "csv":
        return to_csv(rows, columns)
    if fmt == "parquet":
        return to_parquet(rows, columns)
    raise ValueError(f"Unknown format '{fmt}'. Use csv or parquet.")
