"""Diagnostics: the "why" behind the headline, each tied to the config keys
it informs ("tunes"). Pure functions; every rate is gated on its n.

R here is price R: (exit - entry) / |entry - stop|, signed for the side.
MAE/MFE are the worst/best price R seen while the position was open.
"""
from __future__ import annotations

from statistics import mean

from performance.breakdowns import breakdown, bucket_stats, score_bucket
from performance.metrics import fact, gated

TUNES = {
    "stops": ["gates.stop_proximity_pct", "conviction.atr_mult_by_iv_regime"],
    "targets": ["conviction.min_reward_risk", "conviction.atr_mult_by_iv_regime"],
    "calibration": ["conviction.weights", "conviction.bias_threshold", "gates.min_stat_edge"],
    "costs": ["conviction.min_reward_risk", "paper.fill.slippage_pct"],
    "ai": ["paper.judge_model", "paper.autonomous"],
}
EXIT_TUNES = {
    "STOP": ["conviction.atr_mult_by_iv_regime"],
    "TARGET": ["conviction.min_reward_risk"],
    "GATEKEEPER_STOP_PROXIMITY": ["gates.stop_proximity_pct"],
    "GATEKEEPER_WHALE_FLIP": ["gates.whale_flip_adv_frac"],
    "SQUARE_OFF": ["horizon.entry_cutoff_ist", "horizon.square_off_ist"],
    "LLM_CLOSE": ["paper.judge_model"],
    "LLM_REVERSE": ["paper.judge_model"],
    "RECOVERED_STALE": [],
}
REJECT_TUNES = {
    "DAILY_LOSS_LIMIT": ["risk.max_daily_loss_pct"],
    "SIZE_ROUNDS_TO_ZERO": ["risk.risk_per_trade_pct", "risk.max_adv_participation"],
    "AFTER_ENTRY_CUTOFF": ["horizon.entry_cutoff_ist"],
    "PRICE_BEYOND_GEOMETRY": ["paper.fill.slippage_pct"],
    "NO_FRESH_PRICE": ["paper.stale_price_seconds"],
}


def _target_r(t: dict) -> float | None:
    rps = abs(t["entry_price"] - t["stop"])
    return abs(t["target"] - t["entry_price"]) / rps if rps > 0 else None


# ------------------------------------------------------------- MAE / MFE
def excursions(trades: list, min_n: int) -> dict:
    """Are stops too tight, targets too far, and how much edge is given back?"""
    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    n = len(trades)
    stopped = [t for t in trades if t["reason"] == "STOP"]
    # losers that were at least half an R in profit before they were stopped
    stopped_after_up = [t for t in stopped if (t.get("mfe_r") or 0.0) >= 0.5]
    # winners that came within 0.2 R of the stop first
    near_stop_wins = [t for t in wins if (t.get("mae_r") or 0.0) <= -0.8]
    tr = [(t, _target_r(t)) for t in trades]
    reached = [t for t, r in tr if r is not None and (t.get("mfe_r") or 0.0) >= r]
    gave_back = [t["mfe_r"] - t["r_gross"] for t in trades
                 if (t.get("mfe_r") or 0.0) > 0 and t.get("r_gross") is not None]
    capture = [t["r_gross"] / t["mfe_r"] for t in wins
               if (t.get("mfe_r") or 0.0) > 0 and t.get("r_gross") is not None]
    avg = lambda xs, k: mean(x.get(k) or 0.0 for x in xs) if xs else None  # noqa: E731
    return {
        "points": [{"pos_id": t["pos_id"], "symbol": t["symbol"], "mae_r": t.get("mae_r"),
                    "mfe_r": t.get("mfe_r"), "r_net": t.get("r_net"), "win": t["net"] > 0,
                    "reason": t["reason"]} for t in trades],
        "avg_mae_winners": gated(avg(wins, "mae_r"), len(wins), min_n),
        "avg_mfe_losers": gated(avg(losses, "mfe_r"), len(losses), min_n),
        "stops_after_half_r_up_pct": gated(len(stopped_after_up) / len(stopped) * 100.0
                                           if stopped else None, len(stopped), min_n),
        "wins_near_stop_pct": gated(len(near_stop_wins) / len(wins) * 100.0 if wins else None,
                                    len(wins), min_n),
        "reached_target_pct": gated(len(reached) / n * 100.0 if n else None, n, min_n),
        "avg_given_back_r": gated(mean(gave_back) if gave_back else None, len(gave_back), min_n),
        "winner_capture_pct": gated(mean(capture) * 100.0 if capture else None, len(capture), min_n),
        "tunes": list(dict.fromkeys(TUNES["stops"] + TUNES["targets"])),   # no key twice
    }


# ------------------------------------------------------------ calibration
def calibration(trades: list, min_n: int) -> dict:
    """Does a higher composite score win more? Win rate and R per score bucket."""
    groups: dict = {}
    for t in trades:
        groups.setdefault(score_bucket((t.get("context") or {}).get("composite")), []).append(t)
    rows = [{"bucket": k, **bucket_stats(v, min_n)} for k, v in groups.items()]
    rows.sort(key=lambda r: (r["bucket"] is None, r["bucket"] or ""))
    ok = [r for r in rows if r["bucket"] is not None and r["win_rate"]["status"] == "ok"]
    monotonic = (all(a["win_rate"]["value"] <= b["win_rate"]["value"] for a, b in zip(ok, ok[1:]))
                 if len(ok) >= 2 else None)
    return {"buckets": rows, "monotonic": monotonic, "buckets_ok": len(ok),
            "tunes": TUNES["calibration"]}


# ---------------------------------------------------- exit-rule economics
def exit_economics(trades: list, min_n: int) -> dict:
    total = sum(t["net"] for t in trades)
    rows = breakdown(trades, "exit_reason", min_n)
    for r in rows:
        r["share_of_net_pct"] = (r["net"]["value"] / abs(total) * 100.0) if total else None
        r["tunes"] = EXIT_TUNES.get(r["key"], [])
    return {"rows": rows, "total_net": total}


# ------------------------------------------------------------------ costs
def costs(trades: list, min_n: int) -> dict:
    items: dict = {}
    slip = 0.0
    for t in trades:
        for k, v in (t.get("costs") or {}).items():
            if k != "total":
                items[k] = items.get(k, 0.0) + float(v)
        q = t["qty"]
        slip += abs(t["entry_price"] - t.get("ltp_at_entry", t["entry_price"])) * q
        slip += abs(t["exit_price"] - t.get("exit_ref_price", t["exit_price"])) * q
    n = len(trades)
    total = sum(t["costs"]["total"] for t in trades)
    flipped = [t for t in trades if t["gross"] > 0 and t["net"] <= 0]
    return {
        "total": fact(total, n),
        "items": items,
        "per_trade": gated(total / n if n else None, n, min_n),
        "slippage": fact(slip, n),   # the assumed slippage, in rupees -- an assumption, not observed
        "flipped_to_loss": fact(len(flipped), n),
        "flipped_symbols": [t["symbol"] for t in flipped],
        "tunes": TUNES["costs"],
    }


# ----------------------------------------------------------- distribution
R_EDGES = [-3.0, -2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]


def r_histogram(trades: list) -> list[dict]:
    rs = [t["r_net"] for t in trades if t.get("r_net") is not None]
    bins = [{"lo": None, "hi": R_EDGES[0], "n": 0}]
    bins += [{"lo": a, "hi": b, "n": 0} for a, b in zip(R_EDGES, R_EDGES[1:])]
    bins += [{"lo": R_EDGES[-1], "hi": None, "n": 0}]
    for r in rs:
        for b in bins:
            if (b["lo"] is None or r >= b["lo"]) and (b["hi"] is None or r < b["hi"]):
                b["n"] += 1
                break
    return bins


# ----------------------------------------------------------- rejections
def rejections(rejs: list) -> list[dict]:
    counts: dict = {}
    for r in rejs:
        reason = r.get("reason") or "UNKNOWN"
        key = "CLUSTER_LIMIT" if reason.startswith("CLUSTER_LIMIT_") else reason
        counts[key] = counts.get(key, 0) + 1
    tunes = dict(REJECT_TUNES, CLUSTER_LIMIT=["risk.max_cluster_risk_pct"])
    return sorted(({"reason": k, "n": v, "tunes": tunes.get(k, [])} for k, v in counts.items()),
                  key=lambda r: -r["n"])


# ------------------------------------------------------- LLM value-add
def _signal_ts(signal_id) -> int | None:
    """SignalLedger ids are SIG-<symbol>-<unix ts>."""
    try:
        return int(str(signal_id).rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


def match_arm(trade: dict, arms: list, tolerance_s: int = 30) -> dict | None:
    """The arm record written for this trade's escalation: same symbol,
    closest timestamp to the signal, within `tolerance_s`."""
    ts = _signal_ts(trade.get("signal_id"))
    if ts is None:
        return None
    best, gap = None, tolerance_s + 1
    for a in arms:
        if a.get("symbol") != trade["symbol"]:
            continue
        try:
            d = abs(int(a["ts"]) - ts)
        except (KeyError, TypeError, ValueError):
            continue
        if d < gap:
            best, gap = a, d
    return best


def _arm_r(a: dict, key: str):
    o = a.get(key)
    if not isinstance(o, dict) or o.get("outcome") == "NO_DATA":
        return None
    return o.get("r_multiple")


def llm_value(trades: list, arms: list, min_n: int) -> dict:
    """Is the AI judge worth it?

    - by_verdict: paper results for CONFIRM vs ADJUST (the AI's own call).
    - vetoed: signals the AI refused, scored by the arms journal's
      fixed-horizon labeller on the math arm. Negative R means the veto
      saved money. This is a counterfactual, not a paper fill.
    - taken: the same labeller's R for signals the AI accepted, so the two
      are compared like for like; plus the paper R on the trades that
      could be matched to their arm record.
    """
    from journal.arms import arm_was_taken
    vetoed = [a for a in arms if not arm_was_taken(a.get("llm_arm"))]
    taken = [a for a in arms if arm_was_taken(a.get("llm_arm"))]
    v_r = [r for r in (_arm_r(a, "math_outcome") for a in vetoed) if r is not None]
    t_r = [r for r in (_arm_r(a, "math_outcome") for a in taken) if r is not None]
    matched = [(t, match_arm(t, taken)) for t in trades]
    paired = [t["r_net"] for t, a in matched if a is not None and t.get("r_net") is not None]
    return {
        "by_verdict": breakdown(trades, "verdict", min_n),
        "escalations": fact(len(arms), len(arms)),
        "vetoed": {"n": len(vetoed), "labelled": len(v_r),
                   "avg_math_r": gated(mean(v_r) if v_r else None, len(v_r), min_n)},
        "taken": {"n": len(taken), "labelled": len(t_r),
                  "avg_math_r": gated(mean(t_r) if t_r else None, len(t_r), min_n),
                  "matched_paper_trades": len(paired),
                  "avg_paper_r": gated(mean(paired) if paired else None, len(paired), min_n)},
        "note": "Vetoed and taken R come from the arms journal's fixed-horizon labeller "
                "(a counterfactual, gross of costs); paper R is net of costs.",
        "tunes": TUNES["ai"],
    }


def all_diagnostics(trades: list, rejs: list, arms: list, min_n: dict) -> dict:
    rates, bucket = int(min_n.get("rates", 20)), int(min_n.get("bucket", 10))
    return {
        "excursions": excursions(trades, rates),
        "calibration": calibration(trades, int(min_n.get("calibration", 15))),
        "exits": exit_economics(trades, bucket),
        "costs": costs(trades, rates),
        "r_histogram": r_histogram(trades),
        "rejections": rejections(rejs),
        "ai": llm_value(trades, arms, bucket),
    }
