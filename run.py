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

# name, script, port it listens on (None: no port), started after this port is up
SERVICES = [
    ("feed", "trading_copilot/data_services/upstox_feed.py", 8001, None),
    ("news", "trading_copilot/data_services/news_feed.py", 8003, None),
    ("macro", "trading_copilot/data_services/macro_worker.py", None, None),
    ("web", "trading_copilot/main.py", 8000, 8001),
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
    sys.path.insert(0, str(APP))
    from llm import DEFAULT_MODEL
    models = {DEFAULT_MODEL}
    try:
        import yaml
        p = yaml.safe_load((APP / "config" / "paper.yaml").read_text(encoding="utf-8")) or {}
        models.add(str(p.get("judge_model") or DEFAULT_MODEL))
    except Exception:
        pass
    return models


def ollama_tags() -> list[str] | None:
    import requests
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    try:
        return [m["name"] for m in requests.get(f"{host}/api/tags", timeout=3).json().get("models", [])]
    except Exception:
        return None


def check_llm() -> bool:
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
    return ok


# -- history -------------------------------------------------------------------
def child_env() -> dict:
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}


def run_step(name: str, args: list[str], timeout: float) -> bool:
    """Run a one-off script to completion, logging to logs/run/<name>.log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"{name}.log"
    started = time.monotonic()
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\n===== {now_ist():%Y-%m-%d %H:%M:%S} {' '.join(args)}\n")
        fh.flush()
        try:
            rc = subprocess.run([str(VENV_PY), "-u", *args], cwd=ROOT, env=child_env(),
                                stdout=fh, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            say(name, f"timed out after {timeout:.0f} s; see {log.relative_to(ROOT)}")
            return False
    took = time.monotonic() - started
    say(name, f"{'done' if rc == 0 else 'FAILED'} in {took:.0f} s" + ("" if rc == 0 else f"; see {log.relative_to(ROOT)}"))
    return rc == 0


def backfill(full: bool) -> bool:
    t = now_ist()
    # Only a run that starts after the close has today's session to store;
    # a morning or mid-session run must not stand in for it.
    after_close = t.weekday() < 5 and t.time() >= EOD_BACKFILL_AFTER
    args = ["trading_copilot/scripts/master_bootstrap.py"] + ([] if full else ["--prices-only"])
    say("history", "catching up daily prices and options history..." if full else "catching up daily prices...")
    ok = run_step("backfill" if full else "prices", args, timeout=6 * 3600 if full else 300)
    if ok:
        ok = run_step("baselines", ["trading_copilot/scripts/macro_bootstrap.py"], timeout=900)
    if ok and full and after_close:
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


def supervise(services: list[Service], history: bool):
    if wait_port(8000, 90):
        say("web", f"dashboard ready: {DASHBOARD}")
        webbrowser.open(DASHBOARD)
    say("run", "all services started. Ctrl+C stops everything. Logs: logs/run/")
    eod: threading.Thread | None = None
    while True:
        time.sleep(5)
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
        if (history and t.weekday() < 5 and t.time() >= EOD_BACKFILL_AFTER and not eod_done_today()
                and not (eod and eod.is_alive())):
            say("history", "market closed: storing today's history")
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
    if history:
        if market_hours():
            backfill(full=False)                       # options history: after the close (supervise)
            say("history", "options history will be stored after 15:45 IST")
        else:
            backfill(full=True)

    services = start_services(a.verbose)
    try:
        supervise(services, history)
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
