"""Headline and risk-adjusted metrics of the paper account -- pure functions.

Every figure comes back as {value, n, min_n, status}:
  ok            -- a number you can use
  insufficient  -- fewer than min_n observations; value is None, never a
                   number that merely looks meaningful
  undefined     -- enough data, but the ratio has no value (e.g. a profit
                   factor with no losing trade)
Sums and counts are facts and have min_n 0.

A trade wins when its NET P&L (after costs) is above zero.
"""
from __future__ import annotations

import math
from statistics import mean, stdev

from performance.scope import SESSION_OPEN_S, Scope, day_start_ts, ist_day

SESSION_MIN = 375            # 09:15-15:30 IST
TRADING_DAYS = 252


def gated(value, n: int, min_n: int) -> dict:
    if n < min_n:
        return {"value": None, "n": n, "min_n": min_n, "status": "insufficient"}
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return {"value": None, "n": n, "min_n": min_n, "status": "undefined"}
    return {"value": value, "n": n, "min_n": min_n, "status": "ok"}


def fact(value, n: int) -> dict:
    return gated(value, n, 0)


def _div(a, b):
    return a / b if b else None


def _pct(a, b):
    return a / b * 100.0 if b else None


def _streaks(nets: list[float]) -> tuple[int, int]:
    best_w = best_l = w = l = 0
    for v in nets:
        if v > 0:
            w, l = w + 1, 0
        else:
            w, l = 0, l + 1
        best_w, best_l = max(best_w, w), max(best_l, l)
    return best_w, best_l


# ---------------------------------------------------------------- the curve
def curve(sc: Scope) -> dict:
    """The tape: realised P&L at each close (exact, reconciles to the trade
    list), the marked P&L from the per-minute snapshots, drawdown under
    both, a tick per trade, and a marker per settings change.

    P&L is relative to the start of the range. Drawdown % is against the
    equity at the preceding peak, using the capital in force at the time."""
    start = sc.start_ts()
    realised, cum, gross, peak = [], 0.0, 0.0, 0.0
    if start is not None:
        realised.append({"ts": start, "pnl": 0.0, "gross": 0.0,
                         "equity": sc.capital_at(start) + sc.prior_net, "dd": 0.0, "dd_pct": 0.0})
    for t in sc.trades:
        ts = t["closed_ts"]
        cum += t["net"]
        gross += t["gross"]
        peak = max(peak, cum)
        cap = sc.capital_at(ts)
        peak_eq = cap + sc.prior_net + peak
        dd = cum - peak
        realised.append({"ts": ts, "pnl": cum, "gross": gross, "equity": cap + sc.prior_net + cum,
                         "dd": dd, "dd_pct": dd / peak_eq * 100.0 if peak_eq > 0 else None})

    marked, mpeak = [], 0.0
    for e in sc.equity:
        p = e["realized_total"] + e["unrealized"] - sc.prior_net
        mpeak = max(mpeak, p)
        peak_eq = e["capital"] + sc.prior_net + mpeak
        marked.append({"ts": e["ts"], "pnl": p, "equity": e["equity"], "dd": p - mpeak,
                       "dd_pct": (p - mpeak) / peak_eq * 100.0 if peak_eq > 0 else None,
                       "open_risk": e.get("open_risk"), "open_count": e.get("open_count")})

    ticks = [{"ts": t["closed_ts"], "pos_id": t["pos_id"], "symbol": t["symbol"], "side": t["side"],
              "net": t["net"], "r_net": t.get("r_net"), "reason": t["reason"]} for t in sc.trades]
    markers = [{"ts": e["ts"], "old": e.get("old"), "new": e.get("new")}
               for e in sc.settings_changes if sc.contains(e["ts"])]
    return {"realised": realised, "marked": marked, "ticks": ticks, "settings_changes": markers}


def _max_dd(points: list[dict]) -> tuple[float, float | None]:
    if not points:
        return 0.0, None
    worst = min(points, key=lambda p: p["dd"])
    return worst["dd"], worst["dd_pct"]


def _exposure_min(trades: list) -> float:
    """Minutes with at least one position open (overlaps counted once)."""
    spans = sorted((t["opened_ts"], t["closed_ts"]) for t in trades)
    total, cur_s, cur_e = 0.0, None, None
    for s, e in spans:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total / 60.0


# ------------------------------------------------------------- headline
def headline(sc: Scope, min_n: dict) -> dict:
    t = sc.trades
    n = len(t)
    rates = int(min_n.get("rates", 20))
    nets = [x["net"] for x in t]
    wins, losses = [v for v in nets if v > 0], [v for v in nets if v <= 0]
    rs = [x["r_net"] for x in t if x.get("r_net") is not None]
    gross = sum(x["gross"] for x in t)
    net = sum(nets)
    costs = sum(x["costs"]["total"] for x in t)
    gross_won = sum(x["gross"] for x in t if x["gross"] > 0)
    cr = [x["r_gross"] - x["r_net"] for x in t
          if x.get("r_gross") is not None and x.get("r_net") is not None]
    start = sc.start_ts()
    base = sc.capital_at(start) + sc.prior_net if start is not None else sc.current_capital
    cv = curve(sc)
    dd, dd_pct = _max_dd(cv["realised"])
    mdd, mdd_pct = _max_dd(cv["marked"])
    avg_win = mean(wins) if wins else None
    avg_loss = mean(losses) if losses else None
    loss_sum = -sum(losses)
    sw, sl = _streaks(nets)
    days = len(sc.session_days)
    return {
        "trades": fact(n, n),
        "net_pnl": fact(net, n),
        "gross_pnl": fact(gross, n),
        "costs": fact(costs, n),
        # share of the gross winnings paid away in costs
        "cost_drag_pct": gated(_pct(costs, gross_won) if gross_won > 0 else None, n, rates),
        "cost_per_trade_r": gated(mean(cr) if cr else None, len(cr), rates),
        "return_pct": fact(_pct(net, base), n),
        "win_rate": gated(len(wins) / n * 100.0 if n else None, n, rates),
        "profit_factor": gated(_div(sum(wins), loss_sum) if loss_sum > 0 else None, n, rates),
        "expectancy": gated(mean(nets) if nets else None, n, rates),
        "expectancy_r": gated(mean(rs) if rs else None, len(rs), rates),
        "avg_win": gated(avg_win, n, rates),
        "avg_loss": gated(avg_loss, n, rates),
        "payoff": gated(avg_win / abs(avg_loss) if avg_win is not None and avg_loss else None, n, rates),
        "max_drawdown": fact(dd, n),
        "max_drawdown_pct": fact(dd_pct, n),
        "max_drawdown_marked": fact(mdd, len(cv["marked"])),
        "max_drawdown_marked_pct": fact(mdd_pct, len(cv["marked"])),
        "longest_win_streak": fact(sw, n),
        "longest_loss_streak": fact(sl, n),
        "trades_per_day": fact(_div(n, days), days),
        "avg_hold_min": fact(mean(x["hold_min"] for x in t) if t else None, n),
        "exposure_pct": fact(_pct(_exposure_min(t), days * SESSION_MIN), days),
        "rejections": fact(len(sc.rejections), len(sc.rejections)),
    }


# -------------------------------------------------------- risk-adjusted
def daily(sc: Scope) -> list[dict]:
    """One row per session day in the range (days the engine ran count even
    with no trade -- a flat day is a real 0% day). Return is against the
    equity banked at that day's open, using the capital in force at 09:15
    (a change made before the open applies to that day)."""
    by_day: dict[str, list] = {d: [] for d in sc.session_days}
    for t in sc.trades:
        by_day.setdefault(ist_day(t["closed_ts"]), []).append(t)
    out, banked = [], sc.prior_net
    for d in sorted(by_day):
        ts = by_day[d]
        net = sum(t["net"] for t in ts)
        base = sc.capital_at(day_start_ts(d) + SESSION_OPEN_S) + banked
        out.append({"day": d, "trades": len(ts), "net": net, "gross": sum(t["gross"] for t in ts),
                    "costs": sum(t["costs"]["total"] for t in ts),
                    "return_pct": net / base * 100.0 if base else None,
                    "equity_close": base + net})
        banked += net
    return out


def risk_adjusted(sc: Scope, min_n: dict) -> dict:
    need = int(min_n.get("days", 20))
    rows = daily(sc)
    r = [x["return_pct"] / 100.0 for x in rows if x["return_pct"] is not None]
    n = len(r)
    sharpe = sortino = calmar = None
    if n >= 2:
        mu, sd = mean(r), stdev(r)
        sharpe = mu / sd * math.sqrt(TRADING_DAYS) if sd > 0 else None
        down = math.sqrt(mean(min(x, 0.0) ** 2 for x in r))
        sortino = mu / down * math.sqrt(TRADING_DAYS) if down > 0 else None
        # Calmar on the daily-close equity path
        peak, worst = -math.inf, 0.0
        for x in rows:
            peak = max(peak, x["equity_close"])
            worst = min(worst, (x["equity_close"] - peak) / peak if peak > 0 else 0.0)
        calmar = (mu * TRADING_DAYS) / abs(worst) if worst < 0 else None
    return {"sharpe": gated(sharpe, n, need), "sortino": gated(sortino, n, need),
            "calmar": gated(calmar, n, need)}
