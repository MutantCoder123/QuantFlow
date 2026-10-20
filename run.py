"""Run the whole QuantFlow system from one command.

    run.bat                   check, catch up history, start everything
    run.bat --check           run the checks only; start nothing
    run.bat --no-backfill     skip the history catch-up
    run.bat --verbose         echo every service's output, not just errors

What it does, in order:
  1. Checks: the virtualenv, .env, the Upstox token (its own expiry claim,
     then one live quote call), Ollama and the configured model, free ports.
  2. History: daily prices are caught up before anything starts (seconds).
     The slow options-derived EOD backfill runs first when the market is
     closed; in market hours it waits for the close, because its thousands
     of requests exhaust the Upstox rate limit the live feed needs. The
     macro baselines are rebuilt after each. After 15:45 IST it runs by
     itself, so the next morning starts complete.
  3. Services: the Upstox feed (8001) -> once it is listening, the web UI
     (8000); the news feed (8003) and the macro worker. Each logs to
     logs/run/<service>.log; one that crashes is restarted.
  4. Opens the dashboard. Ctrl+C stops everything.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
APP = ROOT / "trading_copilot"
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
LOG_DIR = ROOT / "logs" / "run"
IST = ZoneInfo("Asia/Kolkata")
DASHBOARD = "http://127.0.0.1:8000/"
EOD_BACKFILL_AFTER = dt.time(15, 45)
EOD_STAMP = APP / "data" / ".last_eod_backfill"
EOD_RETRY_S = 30 * 60      # after the close, retry storing the day this often until NSE's file is in
STEP_POLL_S = 5            # how often a running step checks whether to stop

# name, script, port it listens on (None: no port), started after this port is up
SERVICES = [
    ("feed", "trading_copilot/data_services/upstox_feed.py", 8001, None),
    ("news", "trading_copilot/data_services/news_feed.py", 8003, None),
    ("macro", "trading_copilot/data_services/macro_worker.py", None, None),
    # policy v8: the pre-open auction book (paper). Plans 08:45-09:07:30, so it starts early.
    ("auction", "trading_copilot/auction/service.py", None, None),
    ("web", "trading_copilot/main.py", 8000, 8001),
    # shadow only: asks the AI at each key moment of a paper position and
    # records the answer; never acts (trading_copilot/shadow/). QF_SHADOW=0 idles it.
    ("shadow", "trading_copilot/shadow/reanalysis.py", None, 8000),
    # policy v7: ranks every F&O stock after each 5-minute bar -> data/alpha/live.json (alpha/gate.py)
    ("alpha", "trading_copilot/alpha/service.py", None, None),
]
MAX_RESTARTS = 5            # per service, within RESTART_WINDOW_S
RESTART_WINDOW_S = 600
NOISY = ("ERROR", "CRITICAL", "Traceback", "Exception")

_print_lock = threading.Lock()


def say(tag: str, msg: str) -> None:
    line = f"{dt.datetime.now(IST):%H:%M:%S}  {tag:<8} {msg}"
    with _print_lock:
        print(line, flush=True)
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_DIR / "launcher.log", "a", encoding="utf-8") as fh:
                fh.write(f"{dt.datetime.now(IST):%Y-%m-%d} {line}\n")
        except OSError:
            pass


def now_ist() -> dt.datetime:
    return dt.datetime.now(IST)


def market_hours(t: dt.datetime | None = None) -> bool:
    """09:00-15:45 IST on a weekday: the window in which the services must
    start at once rather than wait for the slow backfill."""
    t = t or now_ist()
    return t.weekday() < 5 and dt.time(9, 0) <= t.time() < EOD_BACKFILL_AFTER


# -- checks ------------------------------------------------------------------
def token_expiry(token: str) -> dt.datetime | None:
    try:
        seg = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
        return dt.datetime.fromtimestamp(int(claims["exp"]), IST)
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def check_token() -> bool:
    path = APP / "upstox_token.json"
    if not path.exists():
        say("token", f"MISSING: {path.relative_to(ROOT)}. Put the Upstox analytics token there.")
        return False
    try:
        token = json.loads(path.read_text(encoding="utf-8"))["access_token"]
    except (OSError, ValueError, KeyError):
        say("token", f"UNREADABLE: {path.relative_to(ROOT)} has no access_token.")
        return False
    exp = token_expiry(token)
    if exp and exp <= now_ist():
        say("token", f"EXPIRED on {exp:%Y-%m-%d}. Generate a new analytics token.")
        return False
    import requests
    try:
        r = requests.get("https://api.upstox.com/v2/market-quote/ltp",
                         params={"instrument_key": "NSE_INDEX|Nifty 50"},
                         headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=10)
    except requests.RequestException as e:
        say("token", f"could not reach Upstox ({e.__class__.__name__}); continuing.")
        return True
    if r.status_code == 401:
        say("token", "REJECTED by Upstox (401). Generate a new analytics token.")
        return False
    left = f"valid until {exp:%Y-%m-%d} ({(exp - now_ist()).days} days)" if exp else "no expiry claim"
    warn = "  <- renew soon" if exp and (exp - now_ist()).days < 14 else ""
    say("token", f"ok, {left}{warn}")
    return True


def configured_models() -> set[str]:
    """Every model the services may call. A Groq model brings its local fallback with
    it: Groq's free tier runs out in a busy session (llm.py)."""
    sys.path.insert(0, str(APP))
    from llm import DEFAULT_MODEL, FALLBACK_MODEL, NEWS_MODEL, SHADOW_MODEL
    models = {DEFAULT_MODEL, NEWS_MODEL, SHADOW_MODEL}
    try:
        import yaml
        p = yaml.safe_load((APP / "config" / "paper.yaml").read_text(encoding="utf-8")) or {}
        models.add(str(p.get("judge_model") or DEFAULT_MODEL))
    except Exception:
        pass
    if any(m.startswith("groq:") for m in models):
        models.add(FALLBACK_MODEL)
    return models


def groq_status() -> None:
    groq = sorted(m for m in configured_models() if m.startswith("groq:"))   # puts APP on sys.path
    from llm import groq_key, groq_keys
    if not groq:
        return
    if groq_key():
        n = len(groq_keys())
        say("llm", f"Groq: {', '.join(m[5:] for m in groq)}, {n} key{'s' if n > 1 else ''} "
                   "(local model when every key is at its free-tier limit)")
    else:
        say("llm", "Groq key is blank in .env (GROQ_API_KEY): every AI call runs on the local model")


def ollama_tags() -> list[str] | None:
    import requests
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    try:
        return [m["name"] for m in requests.get(f"{host}/api/tags", timeout=3).json().get("models", [])]
    except Exception:
        return None


def check_llm() -> bool:
    groq_status()
    local = sorted(m.split(":", 1)[1] for m in configured_models() if m.startswith("ollama:"))
    if not local:
        say("llm", f"using {', '.join(sorted(configured_models()))} (not local)")
        return True
    tags = ollama_tags()
    if tags is None:
        exe = "ollama.exe" if os.name == "nt" else "ollama"
        say("llm", "Ollama is not running; starting it...")
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        except FileNotFoundError:
            say("llm", "Ollama is not installed. Install it from ollama.com, then: ollama pull " + local[0])
            return False
        for _ in range(30):
            time.sleep(1)
            if (tags := ollama_tags()) is not None:
                break
        else:
            say("llm", "Ollama did not start. Start the Ollama app and run again.")
            return False
    missing = [m for m in local if m not in tags]
    if missing:
        say("llm", f"model not installed: {', '.join(missing)}. Run: ollama pull {missing[0]}")
        return False
    say("llm", f"ok, local {', '.join(local)}")
    return True


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def check_ports() -> bool:
    busy = [p for _, _, p, _ in SERVICES if p and port_open(p)]
    if busy:
        say("ports", f"already in use: {', '.join(map(str, busy))}. Is QuantFlow already running? "
                     "Close it (or run stop.bat) first.")
        return False
    say("ports", "ok, 8000 8001 8003 free")
    return True


def checks() -> bool:
    ok = True
    if not VENV_PY.exists():
        say("python", f"MISSING virtualenv at {VENV_PY.relative_to(ROOT)}. Create it: "
                      "python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt")
        return False
    if not (ROOT / ".env").exists():
        say("env", "no .env file at the repo root (Upstox and news keys are read from it).")
        ok = False
    ok = check_token() and ok
    ok = check_llm() and ok
    check_power()
    return ok


def on_battery() -> tuple[bool, int | None]:
    """(running on battery, charge %). (False, None) where it cannot be read."""
    if os.name != "nt":
        return False, None
    try:
        import ctypes

        class SPS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
        s = SPS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return False, None
        pct = None if s.BatteryLifePercent == 255 else int(s.BatteryLifePercent)
        return s.ACLineStatus == 0, pct
    except Exception:
        return False, None


def check_power() -> None:
    """A warning, never a refusal. On 2026-09-30 the laptop ran unplugged
    from 44 % and powered off at 10:30 with four positions open; they went
    unmanaged for 2 h 20 min."""
    batt, pct = on_battery()
    if batt:
        say("power", f"WARNING: on battery ({pct}%). Plug in: if the machine powers off, "
                     "open paper positions go unmanaged until it is back.")


# -- history -------------------------------------------------------------------
def child_env() -> dict:
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}


def run_step(name: str, args: list[str], timeout: float, stop_when=None) -> bool:
    """Run a one-off script to completion, logging to logs/run/<name>.log.
    `stop_when()` true ends it early (checked every few seconds)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"{name}.log"
    started = time.monotonic()
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\n===== {now_ist():%Y-%m-%d %H:%M:%S} {' '.join(args)}\n")
        fh.flush()
        proc = subprocess.Popen([str(VENV_PY), "-u", *args], cwd=ROOT, env=child_env(),
                                stdout=fh, stderr=subprocess.STDOUT)
        while True:
            try:
                rc = proc.wait(timeout=STEP_POLL_S)
                break
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() - started > timeout:
                proc.kill()
                say(name, f"timed out after {timeout:.0f} s; see {_shown(log)}")
                return False
            if stop_when is not None and stop_when():
                proc.terminate()
                say(name, "stopped for market hours; it carries on from here after 15:45")
                return False
    took = time.monotonic() - started
    say(name, f"{'done' if rc == 0 else 'FAILED'} in {took:.0f} s" + ("" if rc == 0 else f"; see {_shown(log)}"))
    return rc == 0


def _shown(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


NSE_FO_DIR = APP / "data" / "nse_fo"
HISTORY = "trading_copilot/scripts/history_sync.py"


def backfill(full: bool) -> bool:
    """The local history store (scripts/history_sync.py) fills every missing
    trading day, however long the system was off.

    full=False (in front of the services, any time): the watchlist's daily
    prices and options figures -- quick, so the session can start.
    full=True (behind the services, outside market hours, and after the close):
    every F&O stock's daily prices and options figures, then 1-minute candles.
    The options figures come from NSE's daily F&O file, published around 18:30;
    the day counts as stored only once that file is in (supervise retries)."""
    t = now_ist()
    after_close = t.weekday() < 5 and t.time() >= EOD_BACKFILL_AFTER
    if full:
        say("history", "storing every F&O stock: daily prices, options figures, 1-minute candles...")
        ok = run_step("history", [HISTORY, "--daily"], timeout=2 * 3600, stop_when=market_hours)
        ok = run_step("intraday", [HISTORY, "--intraday"], timeout=3 * 3600, stop_when=market_hours) and ok
    else:
        say("history", "catching up the watchlist's daily prices and options figures...")
        ok = run_step("history", [HISTORY, "--daily", "--watchlist"], timeout=1800)
    ok = run_step("baselines", ["trading_copilot/scripts/macro_bootstrap.py"], timeout=1800) and ok
    if full:   # policy v6's time-of-day volatility table, from the refreshed 1-minute store
        run_step("tod_vol", ["trading_copilot/scripts/build_tod_vol.py"], timeout=600)
        # policy v7: refit the alpha score on the last 60 sessions (data/alpha/weights.json)
        run_step("alpha_fit", ["trading_copilot/alpha/fit.py"], timeout=1800)
        # policy v8: refit the auction book's day/night score (data/auction/weights.json)
        run_step("auction_fit", ["trading_copilot/auction/fit.py"], timeout=600)
    today_file = NSE_FO_DIR / f"{t:%Y%m%d}.csv.zip"
    if ok and full and after_close and (today_file.exists() or t.time() >= dt.time(23, 0)):
        EOD_STAMP.write_text(now_ist().date().isoformat(), encoding="utf-8")
    return ok


def eod_done_today() -> bool:
    try:
        return EOD_STAMP.read_text(encoding="utf-8").strip() == now_ist().date().isoformat()
    except OSError:
        return False


# -- services ------------------------------------------------------------------
class Service:
    def __init__(self, name, script, port, after, verbose):
        self.name, self.script, self.port, self.after, self.verbose = name, script, port, after, verbose
        self.proc: subprocess.Popen | None = None
        self.restarts: list[float] = []
        self.log = LOG_DIR / f"{name}.log"

    def start(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen([str(VENV_PY), "-u", self.script], cwd=ROOT, env=child_env(),
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     creationflags=flags, start_new_session=os.name != "nt")
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        say(self.name, f"started (pid {self.proc.pid})" + (f", port {self.port}" if self.port else ""))

    def _pump(self, proc):
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {now_ist():%Y-%m-%d %H:%M:%S} start pid {proc.pid}\n")
            for line in proc.stdout:
                fh.write(line)
                fh.flush()
                if self.verbose or any(k in line for k in NOISY):
                    say(self.name, line.rstrip()[:300])

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if not self.alive():
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            import signal
            os.killpg(self.proc.pid, signal.SIGTERM)
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def may_restart(self) -> bool:
        t = time.monotonic()
        self.restarts = [r for r in self.restarts if t - r < RESTART_WINDOW_S]
        if len(self.restarts) >= MAX_RESTARTS:
            return False
        self.restarts.append(t)
        return True


def wait_port(port: int, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if port_open(port):
            return True
        time.sleep(1)
    return False


def start_services(verbose: bool) -> list[Service]:
    services = [Service(*spec, verbose) for spec in SERVICES]
    for s in services:
        if s.after:
            say(s.name, f"waiting for port {s.after}...")
            if not wait_port(s.after, 120):
                say(s.name, f"port {s.after} did not come up in 120 s; starting anyway")
        s.start()
    return services


ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002


def keep_awake() -> bool:
    """While the launcher runs, tell Windows the system and the screen are in use.
    The screen must stay on too: this laptop has only Modern Standby (S0), which
    starts whenever the screen turns off and pauses every process -- on 10-08
    the screen timed out at 08:46 and nothing ran until 13:14 although
    ES_SYSTEM_REQUIRED was held. Windows drops the request by itself when this
    process ends. Closing the lid or pressing the power button still sleeps."""
    if sys.platform != "win32":
        return False
    import ctypes
    ok = bool(ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED))
    say("power", "the PC will not sleep while QuantFlow runs" if ok else "could not stop the PC sleeping")
    return ok


def lan_url() -> str | None:
    """The address other devices on the Wi-Fi open, when sharing is on (QF_LAN=1
    in .env or the environment, and a dashboard password set); else None."""
    on = os.getenv("QF_LAN")
    if on is None:
        try:
            for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("QF_LAN="):
                    on = line.split("=", 1)[1].strip().strip("\"'")
        except OSError:
            pass
    if on != "1" or not (APP / "data" / "access.json").exists():
        return None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))                 # picks the Wi-Fi interface; sends nothing
            return f"https://{s.getsockname()[0]}:8443/"     # api_server LAN_HTTPS_PORT
    except OSError:
        return None


def supervise(services: list[Service], history: bool, full_now: bool = False):
    if wait_port(8000, 90):
        say("web", f"dashboard ready: {DASHBOARD}")
        lan = lan_url()
        if lan:
            say("web", f"on the Wi-Fi: {lan}  (other devices sign in with the dashboard password)")
        webbrowser.open(DASHBOARD)
    say("run", "all services started. Ctrl+C stops everything. Logs: logs/run/")
    eod: threading.Thread | None = None
    # At every launch outside market hours, even if today's EOD run is done:
    # stocks added since then have no options history yet, and the backfill
    # only fetches what is missing.
    if full_now:
        say("history", "updating the full history store in the background (logs/run/history.log)")
        eod = threading.Thread(target=backfill, args=(True,), daemon=True)
        eod.start()
    was_on_battery = on_battery()[0]
    last_eod = time.monotonic() if full_now else float("-inf")
    while True:
        time.sleep(5)
        batt, pct = on_battery()
        if batt and not was_on_battery:
            say("power", f"WARNING: unplugged ({pct}%). Plug in before the battery runs out.")
        was_on_battery = batt
        for s in services:
            if s.alive():
                continue
            rc = s.proc.returncode if s.proc else None
            if s.may_restart():
                say(s.name, f"exited (code {rc}); restarting. Last lines are in {s.log.relative_to(ROOT)}")
                s.start()
            elif s.proc is not None:
                say(s.name, f"exited (code {rc}) and keeps crashing; left stopped. See {s.log.relative_to(ROOT)}")
                s.proc = None
        t = now_ist()
        # After the close, until today is stored: NSE's F&O file arrives around
        # 18:30, so a run that finds it missing is retried every 30 minutes.
        if (history and t.weekday() < 5 and t.time() >= EOD_BACKFILL_AFTER and not eod_done_today()
                and not (eod and eod.is_alive()) and time.monotonic() - last_eod >= EOD_RETRY_S):
            say("history", "market closed: storing today's history")
            last_eod = time.monotonic()
            eod = threading.Thread(target=backfill, args=(True,), daemon=True)
            eod.start()


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the whole QuantFlow system.")
    ap.add_argument("--check", action="store_true", help="run the checks only; start nothing")
    ap.add_argument("--no-backfill", action="store_true", help="skip the history catch-up")
    ap.add_argument("--verbose", action="store_true", help="echo every service's output")
    a = ap.parse_args()

    say("run", f"QuantFlow launcher, {now_ist():%a %d %b %Y %H:%M} IST")
    ok = checks()
    ports_ok = check_ports()
    if a.check:
        say("run", "checks passed" if ok and ports_ok else "checks FAILED (see above)")
        return 0 if ok and ports_ok else 1
    if not (ok and ports_ok):
        say("run", "not starting: fix the items above and run again.")
        return 1

    history = not a.no_backfill
    # policy v7: before the open, the alpha score picks the 50 streamed stocks
    # (alpha/select_watchlist.py; watchlist_policy.yaml alpha_select). A launch during the
    # session keeps the current watchlist.
    if now_ist().weekday() < 5 and now_ist().time() < dt.time(9, 15):
        run_step("watchlist", ["trading_copilot/alpha/select_watchlist.py"], timeout=900)
    if history:
        # Only the watchlist's catch-up holds up the start (every missed day,
        # from the local store and NSE's daily files: about a minute). The
        # whole F&O universe and the 1-minute candles run behind the services.
        backfill(full=False)
        if market_hours():
            say("history", "the full history store is updated after 15:45 IST")

    keep_awake()
    services = start_services(a.verbose)
    try:
        supervise(services, history, full_now=history and not market_hours())
    except KeyboardInterrupt:
        say("run", "stopping...")
    finally:
        for s in reversed(services):
            s.stop()
        say("run", "stopped.")
    return 0


if __name__ == "__main__":
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
