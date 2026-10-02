"""The local history store: every F&O stock, kept current, so a stock can join
or leave the watchlist without waiting for a download.

What is stored (under trading_copilot/data/):

  <SYMBOL>_1D.parquet   daily OHLCV (5 years for a new stock) plus the EOD
                        options figures the system reads: EOD_PCR, Total_OI,
                        EOD_MAX_PAIN, EOD_IV (the same columns, the same
                        method as master_bootstrap.py).
  nse_fo/<YYYYMMDD>.csv.zip
                        NSE's daily F&O bhavcopy, kept raw: one file a day holds
                        every contract of every stock, so the options figures
                        come from ~1 MB a day instead of ~1,400 Upstox requests
                        per stock-year. Checked 2026-10-02 against the stored
                        Upstox figures (299 stock-days): PCR corr 0.99, Total OI
                        ratio 1.000, max pain identical, IV corr 0.999.
  intraday/<SYMBOL>.parquet
                        1-minute candles, 13 months back (research; Upstox).

Every run fills whatever is missing, however long the system was off: the
trading days come from the NIFTY 50 daily candles, and each store is compared
with them. Nothing already stored is fetched again.

usage: python trading_copilot/scripts/history_sync.py            (daily + options, then 1-minute)
       python trading_copilot/scripts/history_sync.py --daily    (daily prices + options figures only)
       python trading_copilot/scripts/history_sync.py --intraday (1-minute candles only)
       add --watchlist (the watchlist only) or --symbols SAIL,INFY to limit the run
"""
from __future__ import annotations

import collections
import csv
import io
import json
import os
import sys
import time
import urllib.parse
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

BASE = Path(__file__).resolve().parent.parent
for p in (BASE, BASE / "data_services"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import scrip_master_engine  # noqa: E402
from derivatives_engine import implied_volatility  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
DATA = BASE / "data"
RAW_FO = DATA / "nse_fo"
INTRADAY = DATA / "intraday"
EOD_COLS = ("EOD_PCR", "EOD_MAX_PAIN", "EOD_IV", "Total_OI")
DAILY_YEARS = 5                 # depth of daily history for a stock new to the store
EOD_DAYS = 260                  # options figures kept for the last ~52 weeks (+ margin)
INTRADAY_MONTHS = 13
NIFTY_KEY = "NSE_INDEX|Nifty 50"
NSE_URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d}_F_0000.csv.zip"
NSE_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/124 Safari/537.36",
               "Accept": "*/*", "Referer": "https://www.nseindia.com/"}
NSE_READY_HM = (18, 30)         # today's bhavcopy is published in the evening


def say(msg: str) -> None:
    print(f"[{datetime.now(IST):%H:%M:%S}] {msg}", flush=True)


# ------------------------------------------------------------------ Upstox, paced
class Upstox:
    """Upstox REST with a sliding-window pace well under its limits
    (500 a minute, 2,000 per 30 minutes), leaving room for the live feed."""
    PER_MIN, PER_30MIN = 400, 1500

    def __init__(self):
        tok = json.loads((BASE / "upstox_token.json").read_text())["access_token"]
        self.h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
        self.sent: collections.deque = collections.deque()

    def _pace(self):
        while True:
            now = time.time()
            while self.sent and now - self.sent[0] > 1800:
                self.sent.popleft()
            last_min = sum(1 for t in self.sent if now - t <= 60)
            if len(self.sent) < self.PER_30MIN and last_min < self.PER_MIN:
                break
            time.sleep(1.0)
        self.sent.append(time.time())
        time.sleep(0.12)

    def candles(self, path: str) -> list:
        url = f"https://api.upstox.com{path}"
        for attempt in range(6):
            self._pace()
            try:
                r = requests.get(url, headers=self.h, timeout=30)
            except requests.RequestException as e:
                say(f"  network error ({e}); retrying")
                time.sleep(5)
                continue
            if r.status_code == 429:
                wait = 60 * (attempt + 1)
                say(f"  Upstox rate limit; waiting {wait} s")
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                say(f"  {r.status_code} for {path}: {r.text[:160]}")
                return []
            return r.json().get("data", {}).get("candles", []) or []
        return []


def key_of(symbol: str) -> str | None:
    """The NSE equity key. Where the scrip master lists more than one NSE_EQ
    row for a symbol, the ordinary shares are the ISIN with security code 01
    (MOTHERSON: INE775A01035, not the partly paid INE775A08105)."""
    try:
        df = scrip_master_engine._get_df()
        rows = df[(df.exchange == "NSE_EQ") & (df.tradingsymbol.astype(str).str.upper() == symbol.upper())]
        if len(rows) > 1:
            ordinary = rows[rows.instrument_key.str.split("|").str[-1].str[7:9] == "01"]
            if len(ordinary):
                return ordinary.instrument_key.iloc[0]
        return scrip_master_engine.get_instrument_key(symbol)
    except Exception:
        return None


# ------------------------------------------------------------------ calendar and universe
def trading_days(up: Upstox, since: date) -> list[str]:
    """NSE trading days from `since` to the last completed session (NIFTY 50 daily candles)."""
    today = datetime.now(IST).date()
    enc = urllib.parse.quote(NIFTY_KEY)
    days = set()
    start = since
    while start <= today:                                     # a decade per request is allowed; chunk by year
        end = min(start + timedelta(days=365), today)
        for c in up.candles(f"/v3/historical-candle/{enc}/days/1/{end:%Y-%m-%d}/{start:%Y-%m-%d}"):
            days.add(c[0][:10])
        start = end + timedelta(days=1)
    now = datetime.now(IST)
    if (now.hour, now.minute) < (15, 35):                     # today's session is not complete yet
        days.discard(f"{today:%Y-%m-%d}")
    return sorted(days)


def watchlist() -> list[str]:
    try:
        with open(BASE / "watchlist.csv", newline="") as f:
            return sorted({r["Symbol"].split("-")[0].strip().upper() for r in csv.DictReader(f) if r.get("Symbol")})
    except OSError:
        return []


def fo_universe() -> list[str]:
    """Stocks with options, from the newest stored bhavcopy."""
    files = sorted(RAW_FO.glob("*.csv.zip"))
    if not files:
        return []
    b = read_bhav(files[-1])
    return sorted(b.loc[b.FinInstrmTp == "STO", "TckrSymb"].unique())


# ------------------------------------------------------------------ NSE daily F&O file
def read_bhav(path: Path) -> pd.DataFrame:
    z = zipfile.ZipFile(path)
    return pd.read_csv(z.open(z.namelist()[0]))


def sync_nse_files(days: list[str]) -> list[str]:
    """Download every missing bhavcopy for `days`. Returns the days still missing."""
    RAW_FO.mkdir(parents=True, exist_ok=True)
    now = datetime.now(IST)
    missing = []
    todo = [d for d in days if not (RAW_FO / f"{d.replace('-', '')}.csv.zip").exists()]
    if todo:
        say(f"NSE F&O files: {len(todo)} to download ({todo[0]} .. {todo[-1]})")
    for i, d in enumerate(todo, 1):
        ymd = d.replace("-", "")
        if d == f"{now:%Y-%m-%d}" and (now.hour, now.minute) < NSE_READY_HM:
            missing.append(d)
            continue
        try:
            r = requests.get(NSE_URL.format(d=ymd), headers=NSE_HEADERS, timeout=30)
        except requests.RequestException as e:
            say(f"  {d}: {e}")
            missing.append(d)
            continue
        if r.status_code == 200 and r.content[:2] == b"PK":
            (RAW_FO / f"{ymd}.csv.zip").write_bytes(r.content)
        else:
            missing.append(d)
        if i % 25 == 0:
            say(f"  {i}/{len(todo)} files")
        time.sleep(0.35)                                      # polite to NSE's archive
    if missing:
        say(f"NSE F&O files not available yet for: {', '.join(missing)} (retried next run)")
    return missing


def eod_figures(opt: pd.DataFrame, day: str, spot: float) -> dict | None:
    """master_bootstrap's method on one stock-day of the bhavcopy: the nearest
    expiry; PCR and total OI; OI-weighted max pain; IV at the max-pain strike
    (mean of call and put), else the neighbouring strikes. Only contracts that
    traded that day are used -- what Upstox's daily candles contain. A day on
    which none traded (SAIL 2026-10-01: 116 contracts, no volume) falls back to
    every listed contract (checked: PCR corr 0.92, max pain 89%, IV corr 0.95)
    rather than leaving the day empty."""
    traded = opt[opt.TtlTradgVol > 0]
    if (traded.XpryDt >= day).any():
        opt = traded
    exps = sorted(e for e in opt.XpryDt.unique() if e >= day)
    if not exps:
        return None
    o = opt[opt.XpryDt == exps[0]]
    ce, pe = o[o.OptnTp == "CE"], o[o.OptnTp == "PE"]
    ce_oi, pe_oi = float(ce.OpnIntrst.sum()), float(pe.OpnIntrst.sum())
    strikes = np.sort(o.StrkPric.unique())
    mp = 0.0
    if len(strikes) and spot > 0:
        cs, co, ps, po = ce.StrkPric.to_numpy(), ce.OpnIntrst.to_numpy(), pe.StrkPric.to_numpy(), pe.OpnIntrst.to_numpy()
        loss = [np.sum(np.maximum(0, s - cs) * co) + np.sum(np.maximum(0, ps - s) * po) for s in strikes]
        mp = float(strikes[int(np.argmin(loss))])
    iv = np.nan
    if mp > 0 and spot > 0:
        t = max(max((pd.Timestamp(exps[0]) - pd.Timestamp(day)).days, 0) / 365.0, 0.001)

        def at(k):
            c, p = ce.loc[ce.StrkPric == k, "ClsPric"], pe.loc[pe.StrkPric == k, "ClsPric"]
            cp, pp = (float(c.iloc[0]) if len(c) else 0.0), (float(p.iloc[0]) if len(p) else 0.0)
            if cp > 0 and pp > 0:
                v = [x for x in (implied_volatility(cp, spot, k, t, 0.07, "c"),
                                 implied_volatility(pp, spot, k, t, 0.07, "p")) if not np.isnan(x)]
                return float(np.mean(v)) if v else np.nan
            return np.nan
        iv = at(mp)
        if np.isnan(iv):
            fb = []
            if (strikes > mp).any():
                fb.append(at(strikes[strikes > mp][0]))
            if (strikes < mp).any():
                fb.append(at(strikes[strikes < mp][-1]))
            fb = [x for x in fb if not np.isnan(x)]
            iv = float(np.mean(fb)) if fb else np.nan
    return {"EOD_PCR": round(pe_oi / ce_oi, 4) if ce_oi > 0 else 0.0, "Total_OI": ce_oi + pe_oi,
            "EOD_MAX_PAIN": mp, "EOD_IV": round(iv, 2) if not np.isnan(iv) else np.nan}


# ------------------------------------------------------------------ daily parquet
def daily_path(sym: str) -> Path:
    return DATA / f"{sym}_1D.parquet"


def load_daily(sym: str) -> pd.DataFrame:
    p = daily_path(sym)
    if not p.exists():
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume", *EOD_COLS])
    df = pd.read_parquet(p).rename(columns={"timestamp": "Date", "open": "Open", "high": "High", "low": "Low",
                                            "close": "Close", "volume": "Volume"})
    df = df.drop(columns=[c for c in ("oi", "DateStr") if c in df.columns])
    df["Date"] = pd.to_datetime(df["Date"]).dt.normalize()
    for c in EOD_COLS:
        if c not in df.columns:
            df[c] = np.nan if c == "EOD_IV" else 0.0
    return df


def save_daily(sym: str, df: pd.DataFrame) -> None:
    df = df.drop_duplicates("Date", keep="last").sort_values("Date").reset_index(drop=True)
    tmp = daily_path(sym).with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, daily_path(sym))


def eod_filled(df: pd.DataFrame) -> pd.Series:
    """A complete EOD row has its total open interest. Stricter than
    master_bootstrap.is_eod_truly_filled (any one figure non-zero): a row the
    live system wrote part of (SAIL 2026-09-30: PCR 0.5, IV 52.25, no OI) is
    recomputed from NSE's file instead of kept."""
    return df["Total_OI"].fillna(0) > 0


def sync_prices(up: Upstox, symbols: list[str], days: list[str]) -> None:
    """Daily OHLCV for every symbol up to the last completed session."""
    last_day = days[-1]
    added = new = 0
    for i, sym in enumerate(symbols, 1):
        df = load_daily(sym)
        have = set(df["Date"].dt.strftime("%Y-%m-%d")) if len(df) else set()
        if last_day in have:
            continue
        key = NIFTY_KEY if sym == "NIFTY50" else key_of(sym)
        if not key:
            say(f"  {sym}: no instrument key; skipped")
            continue
        if have:
            start = (max(df["Date"]) + pd.Timedelta(days=1)).date()
        else:
            start = date.fromisoformat(last_day) - timedelta(days=365 * DAILY_YEARS)
            new += 1
        enc = urllib.parse.quote(key)
        rows = []
        s = start
        end_all = date.fromisoformat(last_day)
        while s <= end_all:
            e = min(s + timedelta(days=365 * 3), end_all)
            rows += up.candles(f"/v3/historical-candle/{enc}/days/1/{e:%Y-%m-%d}/{s:%Y-%m-%d}")
            s = e + timedelta(days=1)
        fresh = [{"Date": pd.Timestamp(c[0][:10]), "Open": float(c[1]), "High": float(c[2]), "Low": float(c[3]),
                  "Close": float(c[4]), "Volume": float(c[5]), "EOD_PCR": 0.0, "EOD_MAX_PAIN": 0.0,
                  "EOD_IV": np.nan, "Total_OI": 0.0} for c in rows if c[0][:10] not in have]
        if fresh:
            save_daily(sym, pd.concat([df, pd.DataFrame(fresh)], ignore_index=True))
            added += len(fresh)
        if i % 25 == 0:
            say(f"  prices: {i}/{len(symbols)} stocks checked")
    say(f"daily prices: {added:,} new rows ({new} stocks new to the store)")


def fill_eod(symbols: list[str], days: list[str]) -> None:
    """Options figures for every stored stock-day in the last EOD_DAYS sessions
    that lacks them, from the stored NSE files. Each file is read once."""
    window = days[-EOD_DAYS:]
    frames = {s: load_daily(s) for s in symbols if daily_path(s).exists()}
    # stock-days already looked up with no options in NSE's file (before a
    # stock entered F&O, say): not read again on every run
    none_path = RAW_FO / "no_options.json"
    no_opts = {k: set(v) for k, v in (json.loads(none_path.read_text()) if none_path.exists() else {}).items()}
    need: dict = collections.defaultdict(list)           # day -> symbols needing it
    for sym, df in frames.items():
        if df.empty:
            continue
        ds = df["Date"].dt.strftime("%Y-%m-%d")
        done = set(ds[eod_filled(df)])
        present = set(ds)
        for d in window:
            if d in present and d not in done and d not in no_opts.get(sym, ()):
                need[d].append(sym)
    todo = [d for d in sorted(need) if (RAW_FO / f"{d.replace('-', '')}.csv.zip").exists()]
    if not todo:
        say("options figures: nothing missing")
        return
    say(f"options figures: {sum(len(need[d]) for d in todo):,} stock-days over {len(todo)} sessions")
    touched = set()
    for i, d in enumerate(todo, 1):
        b = read_bhav(RAW_FO / f"{d.replace('-', '')}.csv.zip")
        b = b[b.FinInstrmTp == "STO"]
        groups = dict(tuple(b.groupby("TckrSymb")))
        for sym in need[d]:
            opt = groups.get(sym)
            df = frames[sym]
            row = df["Date"] == pd.Timestamp(d)
            fig = eod_figures(opt, d, float(df.loc[row, "Close"].iloc[0])) if opt is not None else None
            if fig and fig["Total_OI"] > 0:
                for k, v in fig.items():
                    df.loc[row, k] = v
                touched.add(sym)
            else:
                no_opts.setdefault(sym, set()).add(d)
        if i % 25 == 0:
            say(f"  {i}/{len(todo)} sessions")
    for sym in touched:
        save_daily(sym, frames[sym])
    none_path.write_text(json.dumps({k: sorted(v) for k, v in no_opts.items()}, indent=0))
    say(f"options figures: updated {len(touched)} stocks")


# ------------------------------------------------------------------ 1-minute candles
def sync_intraday(up: Upstox, symbols: list[str], days: list[str]) -> None:
    INTRADAY.mkdir(parents=True, exist_ok=True)
    last_day = date.fromisoformat(days[-1])
    first_day = (last_day.replace(day=1) - pd.DateOffset(months=INTRADAY_MONTHS - 1)).date()
    for i, sym in enumerate(symbols, 1):
        key = key_of(sym)
        if not key:
            continue
        f = INTRADAY / f"{sym}.parquet"
        have = pd.read_parquet(f) if f.exists() else pd.DataFrame(columns=["ts", "o", "h", "l", "c", "v"])
        have["ts"] = pd.to_datetime(have["ts"])
        stored_days = set(have.ts.dt.strftime("%Y-%m-%d")) if len(have) else set()
        want = [d for d in days if date.fromisoformat(d) >= first_day and d not in stored_days]
        if not want:
            continue
        enc = urllib.parse.quote(key)
        rows = []
        months = sorted({d[:7] for d in want})
        for ym in months:                                     # Upstox: one calendar month per request
            in_month = [d for d in want if d.startswith(ym)]
            rows += up.candles(f"/v3/historical-candle/{enc}/minutes/1/{in_month[-1]}/{in_month[0]}")
        if rows:
            new = pd.DataFrame([(pd.Timestamp(x[0]).tz_convert("Asia/Kolkata").tz_localize(None),
                                 x[1], x[2], x[3], x[4], x[5]) for x in rows], columns=["ts", "o", "h", "l", "c", "v"])
            out = pd.concat([have, new]).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
            tmp = f.with_suffix(".tmp")
            out.to_parquet(tmp, index=False)
            os.replace(tmp, f)
        if i % 10 == 0:
            say(f"  1-minute: {i}/{len(symbols)} stocks")
    say("1-minute candles: up to date")


# ------------------------------------------------------------------ main
def main(argv: list[str]) -> int:
    daily = "--intraday" not in argv
    intraday = "--daily" not in argv
    only = None
    if "--symbols" in argv:
        only = [s.strip().upper() for s in argv[argv.index("--symbols") + 1].split(",") if s.strip()]
    elif "--watchlist" in argv:                               # the quick start-up pass
        only = watchlist()
    up = Upstox()
    since = datetime.now(IST).date() - timedelta(days=int(EOD_DAYS * 1.6) + 40)
    days = trading_days(up, since)
    if not days:
        say("could not read the trading calendar (NIFTY 50 candles); nothing done")
        return 1
    say(f"trading calendar: {len(days)} sessions, last complete {days[-1]}")
    if daily:
        sync_nse_files(days[-EOD_DAYS:])
    symbols = only or sorted(set(fo_universe()) | set(watchlist()))
    if not symbols:
        say("no stocks to sync")
        return 1
    say(f"stocks: {len(symbols)}")
    if daily:
        sync_prices(up, symbols + ["NIFTY50"], days)          # the index: beta/alpha in the baselines
        fill_eod(symbols, days)
    if intraday:
        sync_intraday(up, symbols, days)
    say("history sync done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
