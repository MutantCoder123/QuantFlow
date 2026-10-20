"""Policy v8: runs the pre-open auction book every weekday (a run.py service). Paper only.

  08:45  sync every F&O stock's daily prices (scripts/history_sync.py --daily), score, PLAN
  09:16  fill at the official open (Upstox 1-minute candles: the 09:15 minute's open)
  5 min  marks -> data/auction/live.json (and the kill switch)
  15:10  exit at the 15:09 minute's close
A plan made after 09:07:30 is not traded (the auction order window has closed): no back-dated fills.

usage: python trading_copilot/auction/service.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
for p in (APP, APP / "data_services"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd  # noqa: E402

from auction import book as B, core, fit  # noqa: E402

OUT = APP / "data" / "auction"
STORE_DIR = APP / "data" / "paper_auction"
HISTORY = APP / "scripts" / "history_sync.py"
MARK_EVERY_S = 300
log = logging.getLogger("auction")


def make_book(cfg: dict) -> B.AuctionBook:
    from core.costs import load_costs
    from paper.settings import PaperSettings
    from paper.store import EventStore
    eff = PaperSettings().effective()
    return B.AuctionBook(EventStore(STORE_DIR), eff["capital"], eff["slippage_pct"], load_costs(), cfg)


def sync_daily() -> bool:
    try:
        r = subprocess.run([sys.executable, str(HISTORY), "--daily"], cwd=str(APP.parent),
                           capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            log.warning(f"daily sync exited {r.returncode}: {(r.stderr or r.stdout)[-300:]}")
        return r.returncode == 0
    except Exception as e:
        log.warning(f"daily sync failed: {e}")
        return False


def minute_candles(syms) -> dict:
    """{symbol: DataFrame[ts, o, h, l, c, v]} of today's 1-minute candles."""
    from alpha.service import fetch_candles, instrument_keys
    token = json.load(open(APP / "upstox_token.json"))["access_token"]
    raw = asyncio.run(fetch_candles(instrument_keys(syms), token))
    out = {}
    for s, c in raw.items():
        if not c:
            continue
        m = pd.DataFrame([x[:6] for x in c], columns=["ts", "o", "h", "l", "c", "v"])
        m["ts"] = pd.to_datetime(m.ts)
        if m.ts.dt.tz is not None:
            m["ts"] = m.ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        out[s] = m.sort_values("ts")
    return out


def official_opens(candles: dict, day: str) -> dict:
    t = pd.Timestamp(f"{day} 09:15")
    return {s: float(m.loc[m.ts == t, "o"].iloc[0]) for s, m in candles.items() if (m.ts == t).any()}


def last_prices(candles: dict, upto: str | None = None) -> dict:
    out = {}
    for s, m in candles.items():
        x = m if upto is None else m[m.ts.dt.strftime("%H:%M") <= upto]
        if len(x):
            out[s] = float(x.c.iloc[-1])
    return out


def write_live(bk: B.AuctionBook, day: str, prices: dict | None = None) -> None:
    from core.fileio import write_json_atomic
    OUT.mkdir(parents=True, exist_ok=True)
    write_json_atomic(str(OUT / "live.json"), bk.summary(day, prices))


def plan_today(bk: B.AuctionBook, day: str, now: datetime) -> None:
    synced = sync_daily()
    P = core.load_daily()
    ok, why = core.freshness(P, day, synced)
    if not ok:
        bk.skip(day, f"STALE_DATA: {why}")
        log.warning(f"{day}: not trading, {why}")
        return
    doc = fit.load_weights()
    if not doc:
        fit.main()
        doc = fit.load_weights()
    scores, asof = core.score(P, pd.Series(doc["weights"]))
    picks = core.select(scores, int(bk.cfg["n_per_side"]), core.adv_crore(P), float(bk.cfg["min_adv_crore"]))
    e = bk.make_plan(day, datetime.now(B.IST), picks, scores.to_dict(), asof)
    if e["type"] == "PLAN":
        log.info(f"{day} plan ({why}): long {', '.join(picks['long'])} | short {', '.join(picks['short'])} | "
                 f"Rs {e['notional'] / 1e5:.1f} L each, weights through {doc.get('fitted_through')}")
    else:
        log.warning(f"{day}: {e['reason']}")


def step(bk: B.AuctionBook, state: dict) -> None:
    now = datetime.now(B.IST)
    day = now.strftime("%Y-%m-%d")
    t = now.time()
    if now.weekday() >= 5 or bk.skipped(day):
        return
    plan = bk.plan_for(day)
    if plan is None:
        if B.PLAN_FROM <= t <= B.PLAN_BY:
            plan_today(bk, day, now)
            write_live(bk, day)
        elif t > B.PLAN_BY and t < B.EXIT_AT and not state.get(f"late_{day}"):
            state[f"late_{day}"] = True
            bk.skip(day, "STARTED_AFTER_PREOPEN: no plan was made before 09:07:30")
            write_live(bk, day)
            log.warning(f"{day}: started after the pre-open window; no auction book today")
        return
    if not bk.filled(day):
        if t < B.FILL_FROM:
            return
        syms = plan["long"] + plan["short"]
        c = minute_candles(syms)
        opens = official_opens(c, day)
        if not opens:
            if t >= B.FILL_GIVE_UP:
                bk.skip(day, "NO_SESSION: no 09:15 candle by 09:30 (holiday or feed problem)")
                log.warning(f"{day}: no opening candles; no auction book today")
            return
        if len(opens) < len(syms) and t < dtime_plus(B.FILL_FROM, 120):
            return                                  # give the missing ones two minutes
        got = bk.fill(day, opens)
        log.info(f"{day} filled at the open: " + ", ".join(f"{e['side'][0]} {e['symbol']} {e['qty']} @ {e['entry_price']:.2f}" for e in got))
        write_live(bk, day, last_prices(c))
        state["mark_ts"] = time.time()
        return
    openp = bk.open_positions(day)
    if not openp:
        return
    if t >= B.EXIT_AT:
        c = minute_candles([p["symbol"] for p in openp])
        px = last_prices(c, B.EXIT_MINUTE)
        done = bk.close_all(day, px, "EXIT_1510")
        log.info(f"{day} closed {len(done)}: net Rs {sum(e['net'] for e in done):+,.0f} "
                 f"({sum(e['bps_net'] for e in done) / max(1, len(done)):+.1f} bps/position)")
        write_live(bk, day, px)
        return
    if time.time() - state.get("mark_ts", 0) >= MARK_EVERY_S:
        state["mark_ts"] = time.time()
        c = minute_candles([p["symbol"] for p in openp])
        px = last_prices(c)
        if bk.kill_hit(day, px):
            done = bk.close_all(day, px, "KILL")
            log.warning(f"{day} kill switch: book down {bk.cfg['kill_loss_pct']}% of capital; closed {len(done)}")
        write_live(bk, day, px)


def dtime_plus(t, seconds: int):
    return (datetime.combine(datetime.today(), t) + timedelta(seconds=seconds)).time()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    import warnings
    warnings.filterwarnings("ignore")
    cfg = B.config()
    if not cfg.get("enabled"):
        log.info("auction book is off (policy books.auction.enabled); idling")
        while True:
            time.sleep(3600)
    bk = make_book(cfg)
    log.info(f"auction book: {cfg['n_per_side']} long + {cfg['n_per_side']} short, gross {cfg['gross_x']}x capital "
             f"(Rs {bk.capital * float(cfg['gross_x']) / 1e5:.0f} L), kill at -{cfg['kill_loss_pct']}% of capital")
    state: dict = {}
    while True:
        try:
            step(bk, state)
        except Exception as e:
            log.error(f"auction step failed: {type(e).__name__}: {e}", exc_info=True)
            time.sleep(30)
        time.sleep(10)


if __name__ == "__main__":
    main()
