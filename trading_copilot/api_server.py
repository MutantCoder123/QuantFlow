import json
import os
import asyncio
import logging
import csv
import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

logger = logging.getLogger(__name__)

from diagnostic_ui import TerminalDashboard
from config import load_watchlist_from_csv
from reasoning_engine import ReasoningEngine, attention_rank
from history_manager import HistoryManager
from paths import INSTITUTIONAL_FLOW_PATH, TRADE_HISTORY_PATH, WATCHLIST_PATH, ensure_dirs
from llm import DEFAULT_MODEL

ensure_dirs()

app = FastAPI(title="AlgoTrade Live Web Portal")

# Scoped to localhost: this server binds to 127.0.0.1 (below) and holds no
# authentication, so an open "*" origin let any page loaded in the same
# browser make authenticated-looking requests against it (E-1/E-2).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# The Performance tab's ES modules and styles (no build step). Only this
# directory is served; it holds no data. "no-cache" makes the browser
# revalidate (ETag) on every load, so an updated module is never run
# against a stale sibling from the cache.
class _RevalidatedStatic(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", _RevalidatedStatic(directory=os.path.join(os.path.dirname(__file__), "static")),
          name="static")

watchlist_path = str(WATCHLIST_PATH)
watchlist = load_watchlist_from_csv(watchlist_path)


def _reload_watchlist() -> dict:
    """Re-read watchlist.csv. It has a writer in each process -- a Discovery
    run in the feed process, manual edits here -- so a copy loaded once at
    import goes stale, and rebuilding the file from that copy silently
    reverts the other process's write. Small file; read it every time."""
    global watchlist
    watchlist = load_watchlist_from_csv(watchlist_path)
    return watchlist

local_active_states = {}
local_macro_state = {}
local_stock_derivatives_state = {}
local_fii_dii_state = {}
local_catalyst_cache = {}
local_macro_context = None
local_news_state = {}

async def poll_upstox():
    global local_active_states, watchlist
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get("http://127.0.0.1:8001/state", timeout=2) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        local_active_states = data.get("active_states", {})
                        from diagnostic_ui import TerminalDashboard
                        TerminalDashboard.active_states = local_active_states
            except: pass
            await asyncio.sleep(0.5)

async def poll_nse():
    global local_macro_state, local_stock_derivatives_state, local_fii_dii_state
    import os, json
    flow_file = INSTITUTIONAL_FLOW_PATH
    while True:
        try:
            if os.path.exists(flow_file):
                with open(flow_file, 'r') as f:
                    data = json.load(f)
                    local_fii_dii_state = data
                    local_macro_state = data # Fallback for backwards compat
        except: pass
        await asyncio.sleep(5)

async def poll_news():
    global local_catalyst_cache
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get("http://127.0.0.1:8003/state", timeout=2) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        local_catalyst_cache = data.get("catalyst_cache", {})
                        global local_macro_context, local_news_state
                        local_macro_context = data.get("macro_context")
                        local_news_state = {k: v for k, v in data.items() if k not in ("catalyst_cache", "macro_context")}
                        TerminalDashboard.catalyst_cache = local_catalyst_cache
            except: pass
            await asyncio.sleep(0.5)

@app.on_event("startup")
async def startup_event():
    print("[SYSTEM] Upstox Tri-Stream separated. Macro Polling ENGAGED.")
    asyncio.create_task(poll_upstox())
    asyncio.create_task(poll_nse())
    asyncio.create_task(poll_news())
    asyncio.create_task(ReasoningEngine.start_global_gatekeeper_loop())

async def proxy_post(port: int, endpoint: str, payload: dict = None, timeout: int = 300):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"http://127.0.0.1:{port}{endpoint}", json=payload, timeout=timeout) as resp:
                return await resp.json()
    except Exception as e:
        logger.error(f"Proxy request failed to {endpoint}: {e}")
        return {"status": "error", "message": f"Service on {port} unreachable: {e}"}

async def proxy_get(port: int, endpoint: str, timeout: int = 30):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}{endpoint}", timeout=timeout) as resp:
                return await resp.json()
    except Exception as e:
        logger.error(f"Proxy request failed to {endpoint}: {e}")
        return {"status": "error", "message": f"Service on {port} unreachable: {e}"}


@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>templates/index.html not found.</h1>", status_code=404)

# Discovery runs in the feed process (port 8001): it owns the Upstox
# client, the live state, and the watchlist it updates. It is a background
# job there -- start returns at once, the UI polls status.
class DiscoveryRunRequest(BaseModel): model: str = DEFAULT_MODEL

@app.post("/api/discovery/run")
async def run_discovery(req: DiscoveryRunRequest | None = None):
    model = req.model if req else DEFAULT_MODEL
    return await proxy_post(8001, "/api/discovery/run", {"model": model}, timeout=10)

async def _ollama_models() -> list:
    """Tags installed on the local Ollama server (OLLAMA_HOST)."""
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{host}/api/tags", timeout=aiohttp.ClientTimeout(total=2)) as resp:
            data = await resp.json()
    return [m["name"] for m in data.get("models", [])]


@app.get("/api/ai/models")
async def list_ai_models():
    """Models the AI features can use: whatever the local Ollama has
    installed, then Gemini. `default` is the system default (llm.py); Ollama
    being down just means no local options."""
    models = []
    try:
        models += [{"id": f"ollama:{tag}", "label": f"Local: {tag}"} for tag in await _ollama_models()]
    except Exception as e:
        logger.info(f"Ollama not reachable for model list: {e}")
    models.append({"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"})
    return {"status": "success", "models": models, "default": DEFAULT_MODEL}

@app.get("/api/discovery/status")
async def discovery_status(): return await proxy_get(8001, "/api/discovery/status", timeout=5)

# /api/map-option-tokens was removed (A-11): it proxied to a route that only
# ever existed on the dead Angel One smart_api_feed.py, never on the live
# upstox_feed.py (port 8001) -- the call always errored.

class InstantAnalyzeRequest(BaseModel):
    model: str = DEFAULT_MODEL
    prompt: str = ""
    user_position: dict | None = None
    user_intent: dict | None = None

class LoopStartRequest(BaseModel):
    symbol: str
    interval: int = 90
    model: str = DEFAULT_MODEL
    prompt: str = ""
    user_position: dict | None = None
    user_intent: dict | None = None

class LoopStopRequest(BaseModel):
    symbol: str

class SavePositionRequest(BaseModel):
    symbol: str
    user_position: dict | None = None

@app.post("/api/reasoning/position/save")
async def save_position_api(req: SavePositionRequest):
    norm = ReasoningEngine._normalize_symbol(req.symbol)
    pos = dict(req.user_position) if req.user_position else req.user_position

    # Backfill the whale-CVD baseline used by the A-6 adverse-flip check so a
    # position saved without one doesn't silently compare against 0.0 forever.
    if pos and "whale_cvd_at_entry" not in pos:
        for key, state in local_active_states.items():
            if key.split('|')[-1].split('-')[0] == norm:
                pos["whale_cvd_at_entry"] = state.get("whale_cvd_ema_1h", 0.0)
                break

    ReasoningEngine.user_positions[norm] = pos
    return {"status": "success"}

class SyncPositionsRequest(BaseModel):
    positions: dict

@app.post("/api/reasoning/position/sync_all")
async def sync_all_positions_api(req: SyncPositionsRequest):
    for sym, pos in req.positions.items():
        norm = ReasoningEngine._normalize_symbol(sym)
        ReasoningEngine.user_positions[norm] = pos
    return {"status": "success"}

@app.get("/api/performance/dashboard")
async def get_performance_dashboard():
    try:
        from performance_analyzer import PerformanceAnalyzer
        return {"status": "success", "data": PerformanceAnalyzer.compute_dashboard()}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/performance/regime")
async def get_regime_accuracy():
    try:
        from performance_analyzer import PerformanceAnalyzer
        return {"status": "success", "data": PerformanceAnalyzer.compute_regime_accuracy()}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/performance/symbols")
async def get_symbol_accuracy():
    try:
        from performance_analyzer import PerformanceAnalyzer
        return {"status": "success", "data": PerformanceAnalyzer.compute_symbol_accuracy()}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/performance/arms")
async def get_arm_comparison(days: int = 30):
    """Shadow-mode both-arm comparison (improved §4.5): math-only vs
    math+LLM, plus LLM veto precision."""
    try:
        from journal.arms import ArmJournal, summarise_arms
        from paths import SIGNALS_DIR
        rows = ArmJournal(SIGNALS_DIR / "arms").load_all(days)
        return {"status": "success", "data": summarise_arms(rows)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/performance/reliability")
async def get_reliability(days: int = 60):
    """Realised win rate per |composite| decile (Task 4.4). A flat curve
    means the score has no discriminative power -- the view says so."""
    try:
        from core.calibration import reliability_buckets, load_calibration, calibration_status
        from signal_ledger import SignalLedger
        from performance_analyzer import PerformanceAnalyzer
        data = reliability_buckets(SignalLedger.load_all_signals(days),
                                   primary_minute=PerformanceAnalyzer._primary_minute())
        data["calibration_status"] = calibration_status(load_calibration())
        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/session/review")
async def get_session_review(date: str | None = None):
    """End-of-day review for one session: signals, outcomes at the primary
    horizon, best/worst regime and cluster, config version, staleness (5.6)."""
    try:
        from datetime import datetime, date as _date
        from zoneinfo import ZoneInfo
        from signal_ledger import SignalLedger
        from performance_analyzer import PerformanceAnalyzer
        from core.policy_config import load_policy
        from journal.feature_log import FeatureLog, staleness_incidents
        from paths import FEATURES_DIR

        ist_today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        if date:
            session_date = _date.fromisoformat(date)   # raises on malformed input
        else:
            session_date = ist_today
        session_str = session_date.isoformat()

        # load_all_signals takes a days-back count, so load just far enough to
        # cover the requested date and filter inside session_review().
        days_back = max(1, (ist_today - session_date).days + 1)
        data = PerformanceAnalyzer.session_review(
            SignalLedger.load_all_signals(days_back), session_str)

        data["config_version"] = load_policy().version
        try:
            data["staleness"] = staleness_incidents(
                FeatureLog(FEATURES_DIR).load_day(session_str))
        except Exception as e:
            logger.error(f"Session review: feature log unreadable: {e}")
            data["staleness"] = {"incidents": None, "symbols": None,
                                 "max_stale_microstructure_s": None}
        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ---- Autonomous paper trading (simulated fills, no real orders) ----------
def _paper_or_error():
    from paper import runtime as paper_rt
    b = paper_rt.broker()
    if b is None:
        return None, {"status": "error", "engine_ok": False,
                      "message": "The paper engine is not running (it starts with the decision loop)."}
    return b, None


def _paper_settings_body(b):
    from paper.settings import EDITABLE
    return {"settings": b.settings.effective(), "sources": b.settings.sources(),
            "defaults": b.settings.defaults(), "editable": list(EDITABLE),
            "rules": {k: v[2] for k, v in EDITABLE.items()}}


@app.get("/api/paper/settings")
async def get_paper_settings():
    b, err = _paper_or_error()
    return err or {"status": "success", **_paper_settings_body(b)}


@app.post("/api/paper/settings")
async def save_paper_settings(changes: dict):
    b, err = _paper_or_error()
    if err:
        return err
    ok, errors = b.update_settings(changes)
    if not ok:
        return {"status": "error", "errors": errors, **_paper_settings_body(b)}
    return {"status": "success", **_paper_settings_body(b)}


@app.post("/api/paper/enable")
async def enable_paper():
    b, err = _paper_or_error()
    if err:
        return err
    b.resume()
    return {"status": "success", "enabled": b.enabled}


@app.post("/api/paper/disable")
async def disable_paper():
    b, err = _paper_or_error()
    if err:
        return err
    b.pause()
    return {"status": "success", "enabled": b.enabled}


@app.get("/api/paper/positions")
async def paper_positions():
    b, err = _paper_or_error()
    return err or {"status": "success", **b.summary()}


@app.get("/api/paper/trades")
async def paper_trades(rng: str = Query("all", alias="range"),
                       frm: str | None = Query(None, alias="from"), to: str | None = None):
    sc, _, err = _paper_scope(rng, frm, to)
    return err or {"status": "success", "range": _range_body(rng, sc), "trades": sc.trades}


@app.get("/api/paper/rejections")
async def paper_rejections(rng: str = Query("all", alias="range"),
                           frm: str | None = Query(None, alias="from"), to: str | None = None):
    sc, _, err = _paper_scope(rng, frm, to)
    return err or {"status": "success", "range": _range_body(rng, sc), "rejections": sc.rejections}


# ---- Paper performance (pure metrics over the event log) -----------------
def _paper_scope(rng, frm, to):
    """(scope, min_n, error). Reads the event log directly, so the figures
    are there even when the decision loop isn't running."""
    import time as _time
    from paper import runtime as paper_rt
    from paper.settings import PaperSettings
    from paper.store import EventStore
    from performance.scope import select
    b = paper_rt.broker()
    store = b.store if b else EventStore()
    settings = b.settings if b else PaperSettings()
    now = b._now() if b else _time.time()
    try:
        sc = select(store.load(), rng, now, settings.effective()["capital"], frm, to)
    except ValueError as e:
        return None, None, {"status": "error", "message": str(e)}
    return sc, settings.options().get("metrics_min_n") or {}, None


def _range_body(rng, sc):
    return {"name": rng, "first_day": sc.first_day, "last_day": sc.last_day,
            "session_days": len(sc.session_days)}


def _paper_arms(sc):
    """Arms-journal rows inside the range (a seam the tests replace)."""
    from performance.sources import load_arms
    return load_arms(sc)


@app.get("/api/paper/metrics")
async def paper_metrics(rng: str = Query("all", alias="range"),
                        frm: str | None = Query(None, alias="from"), to: str | None = None):
    from performance.metrics import daily, headline, risk_adjusted
    sc, min_n, err = _paper_scope(rng, frm, to)
    if err:
        return err
    return {"status": "success", "range": _range_body(rng, sc), "min_n": min_n,
            "headline": headline(sc, min_n), "risk_adjusted": risk_adjusted(sc, min_n),
            "daily": daily(sc)}


@app.get("/api/paper/equity")
async def paper_equity(rng: str = Query("all", alias="range"),
                       frm: str | None = Query(None, alias="from"), to: str | None = None):
    from performance.metrics import curve
    sc, _, err = _paper_scope(rng, frm, to)
    if err:
        return err
    start = sc.start_ts()
    return {"status": "success", "range": _range_body(rng, sc), "session_days": sc.session_days,
            "start_equity": (sc.capital_at(start) + sc.prior_net) if start is not None else sc.current_capital,
            **curve(sc)}


@app.get("/api/paper/breakdowns")
async def paper_breakdowns(rng: str = Query("all", alias="range"), by: str | None = None,
                           frm: str | None = Query(None, alias="from"), to: str | None = None):
    from performance.breakdowns import all_breakdowns, breakdown
    sc, min_n, err = _paper_scope(rng, frm, to)
    if err:
        return err
    need = int(min_n.get("bucket", 10))
    try:
        body = {by: breakdown(sc.trades, by, need)} if by else all_breakdowns(sc.trades, need)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    return {"status": "success", "range": _range_body(rng, sc), "min_n": need, "breakdowns": body}


@app.get("/api/paper/diagnostics")
async def paper_diagnostics(rng: str = Query("all", alias="range"),
                            frm: str | None = Query(None, alias="from"), to: str | None = None):
    from performance.diagnostics import all_diagnostics
    sc, min_n, err = _paper_scope(rng, frm, to)
    if err:
        return err
    try:
        arms = _paper_arms(sc)
    except Exception as e:
        logger.error(f"paper diagnostics: arms journal unreadable: {e}")
        arms = []
    return {"status": "success", "range": _range_body(rng, sc), "min_n": min_n,
            **all_diagnostics(sc.trades, sc.rejections, arms, min_n)}


@app.get("/api/paper/report")
async def paper_report(rng: str = Query("all", alias="range"), fmt: str = "json", download: int = 0,
                       frm: str | None = Query(None, alias="from"), to: str | None = None):
    """The evidence report. It recommends only; nothing writes config from it."""
    import time as _time
    from paper import runtime as paper_rt
    from performance.report import build, to_markdown
    sc, min_n, err = _paper_scope(rng, frm, to)
    if err:
        return err
    try:
        arms = _paper_arms(sc)
    except Exception as e:
        logger.error(f"paper report: arms journal unreadable: {e}")
        arms = []
    b = paper_rt.broker()
    rep = build(sc, arms, min_n, b._now() if b else _time.time())
    if fmt == "json":
        return {"status": "success", "range": _range_body(rng, sc), "report": rep}
    if fmt != "md":
        return {"status": "error", "message": "Unknown format. Use fmt=json or fmt=md."}
    headers = {}
    if download:
        name = f"paper_evidence_{sc.first_day or 'start'}_{sc.last_day or 'now'}.md"
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return Response(content=to_markdown(rep), media_type="text/plain; charset=utf-8", headers=headers)


@app.get("/api/paper/export")
async def paper_export(rng: str = Query("all", alias="range"), fmt: str = "csv",
                       kind: str = "trades",
                       frm: str | None = Query(None, alias="from"), to: str | None = None):
    from performance import export
    from performance.metrics import daily
    sc, _, err = _paper_scope(rng, frm, to)
    if err:
        return err
    if kind == "trades":
        rows, cols = [export.trade_row(t) for t in sc.trades], export.TRADE_COLUMNS
    elif kind == "daily":
        rows, cols = daily(sc), export.DAILY_COLUMNS
    else:
        return {"status": "error", "message": "Unknown export. Use kind=trades or kind=daily."}
    try:
        body = export.encode(rows, cols, fmt)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    name = f"paper_{kind}_{sc.first_day or 'start'}_{sc.last_day or 'now'}.{fmt}"
    media = "text/csv" if fmt == "csv" else "application/vnd.apache.parquet"
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/legacy/trades")
async def legacy_trades():
    """The mock-platform trades from before the current engine. Read-only,
    labelled, and never included in any paper metric."""
    try:
        with open(TRADE_HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    # The file is {symbol: [trade, ...]}; flatten to one list of trades
    # (older copies held {trade_id: trade}, so accept either).
    trades = []
    for v in (data.values() if isinstance(data, dict) else data):
        if isinstance(v, list):
            trades.extend(t for t in v if isinstance(t, dict))
        elif isinstance(v, dict):
            trades.append(v)
    return {"status": "success", "source": "legacy_mock_platform",
            "note": "Made on a mock trading platform before the current engine; "
                    "does not represent the current engine and is excluded from every metric.",
            "trades": trades}


@app.get("/api/risk/exposure")
async def get_risk_exposure():
    """Report open risk per cluster against max_cluster_risk_pct limits (Task 5.3)."""
    try:
        from core.risk import load_risk_limits
        book = ReasoningEngine._build_portfolio()
        limits = load_risk_limits()

        # Collect unique clusters that have open risk
        open_clusters = set(p["cluster"] for p in book._open if p.get("risk_amount", 0) > 0)

        # Build response for each cluster with open risk
        cluster_data = []
        limit_per_cluster = limits.capital * limits.max_cluster_risk_pct / 100.0

        for cluster_id in open_clusters:
            open_risk = book.risk_in_cluster(cluster_id)
            pct_of_limit = open_risk / limit_per_cluster if limit_per_cluster > 0 else 0.0

            cluster_data.append({
                "cluster": cluster_id,
                "open_risk": round(open_risk, 2),
                "limit": round(limit_per_cluster, 2),
                "pct_of_limit": round(pct_of_limit, 4)
            })

        # Sort descending by pct_of_limit (highest risk % first)
        cluster_data.sort(key=lambda x: x["pct_of_limit"], reverse=True)

        return {
            "status": "success",
            "data": {
                "capital": limits.capital,
                "max_cluster_risk_pct": limits.max_cluster_risk_pct,
                "clusters": cluster_data,
                # Positions held but missing a qty/entry/stop, so their risk
                # could not be computed. Without this an empty `clusters` is
                # indistinguishable from "flat", and the panel would reassure
                # the operator they are within limits over exactly the
                # positions it failed to measure.
                "unsizeable_positions": ReasoningEngine._unsizeable_positions(),
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/reasoning/instant/{symbol}")
async def instant_analyze(symbol: str, req: InstantAnalyzeRequest):
    TerminalDashboard.active_states = local_active_states
    report = await ReasoningEngine.analyze_stock(symbol, req.model, req.prompt, req.user_position, req.user_intent)
    return {"status": "success", "report": report}

@app.post("/api/reasoning/loop/start")
async def start_analysis_loop(req: LoopStartRequest):
    TerminalDashboard.active_states = local_active_states
    norm = ReasoningEngine._normalize_symbol(req.symbol)
    if req.user_position is not None:
        ReasoningEngine.user_positions[norm] = req.user_position
    ReasoningEngine.set_llm_toggle(req.symbol, True, req.user_position)
    return {"status": "success"}

@app.post("/api/reasoning/loop/stop")
async def stop_analysis_loop(req: LoopStopRequest):
    ReasoningEngine.set_llm_toggle(req.symbol, False)
    return {"status": "success"}

@app.get("/api/reasoning/debug/toggles")
async def debug_toggles():
    return {
        "llm_enabled": ReasoningEngine.llm_enabled,
        "active_loops": list(ReasoningEngine.active_loops.keys()),
        "user_positions_keys": list(ReasoningEngine.user_positions.keys()),
    }

@app.get("/api/reasoning/all_reports")
async def get_all_reports():
    return {
        "status": "success",
        "reports": ReasoningEngine.latest_reports,
        "llm_trigger_count": getattr(ReasoningEngine, "llm_trigger_count", 0)
    }

@app.get("/api/alerts/unread")
async def alerts_unread():
    return {"count": sum(1 for a in ReasoningEngine.global_alerts if not a.get("read"))}

@app.get("/api/alerts/history")
async def alerts_history():
    return {"status": "success", "alerts": ReasoningEngine.global_alerts}

@app.post("/api/alerts/mark-read/{alert_id}")
async def alerts_mark_read(alert_id: int):
    for a in ReasoningEngine.global_alerts:
        if a.get("id") == alert_id:
            a["read"] = True
    return {"status": "success"}
@app.get("/api/reasoning/report/{symbol}")
async def get_latest_report(symbol: str):
    norm = ReasoningEngine._normalize_symbol(symbol)
    report = ReasoningEngine.latest_reports.get(norm, "No report generated yet.")
    is_active = ReasoningEngine.llm_enabled.get(norm, False)
    return {"status": "success", "report": report, "is_active": is_active}

def _state_for(symbol: str):
    """(instrument key, live state) for a bare symbol, or (None, None)."""
    want = ReasoningEngine._normalize_symbol(symbol).upper()
    if want in ("NIFTY", "NIFTY50", "NIFTY 50"):
        return (NIFTY_KEY, local_active_states.get(NIFTY_KEY) or {})
    for key, st in list(local_active_states.items()):
        if ReasoningEngine._normalize_symbol(key).upper() == want:
            return key, st
    return None, None


@app.get("/api/stock/{symbol}/bars")
async def stock_bars(symbol: str, n: int = 90):
    """Today's 5-minute bars for the Inspector's chart, with the session
    VWAP per bar, and the levels drawn over them (views.stock)."""
    from views.stock import levels, session_bars
    key, st = _state_for(symbol)
    if key is None:
        return {"status": "error", "message": f"{symbol} is not on the live watchlist."}
    from urllib.parse import quote
    raw = await proxy_get(8001, f"/api/bars?token={quote(key)}&n={max(1, min(int(n), 400))}", timeout=5)
    if raw.get("status") == "error":
        return raw
    return {"status": "success", "symbol": ReasoningEngine._normalize_symbol(key),
            **session_bars(raw.get("bars") or []), "levels": levels(st)}


# ---- Market tab (views.market) --------------------------------------------
NIFTY_KEY = "NSE_INDEX|Nifty 50"
_nifty_daily_cache = {"mtime": None, "rows": []}


def _nifty_daily() -> list:
    """[{date, close}] from data/NIFTY50_1D.parquet, re-read only when it changes."""
    from paths import DATA_DIR
    path = DATA_DIR / "NIFTY50_1D.parquet"
    try:
        m = path.stat().st_mtime
    except FileNotFoundError:
        return []
    if m != _nifty_daily_cache["mtime"]:
        import pandas as pd
        df = pd.read_parquet(path, columns=["Date", "Close"]).sort_values("Date")
        _nifty_daily_cache.update(mtime=m, rows=[{"date": d.strftime("%Y-%m-%d"), "close": float(c)}
                                                 for d, c in zip(df["Date"], df["Close"])])
    return _nifty_daily_cache["rows"]


def _watchlist_ad() -> float:
    """Advances / declines over the watchlist -- the proxy when NSE breadth is stale."""
    advances = declines = 0
    for k, v in local_active_states.items():
        if "Nifty 50" in k:
            continue
        ltp = v.get("ltp", 0)
        pc = v.get("prev_close", ltp)
        if ltp > pc: advances += 1
        elif ltp < pc: declines += 1
    return advances / declines if declines > 0 else (advances if advances > 0 else 1.0)


def _engine_states() -> dict:
    """{symbol: engine state in words} for every stock the engine has something to say about."""
    from views.signals import engine_state, parse_report
    paper = {p["symbol"]: p for p in (_paper_live_block().get("open") or [])}
    out = {}
    syms = set(ReasoningEngine.latest_reports) | set(paper) | set(ReasoningEngine.user_positions)
    for sym in syms:
        manual = ReasoningEngine.user_positions.get(sym)
        if isinstance(manual, dict) and manual.get("source") == "paper":
            manual = None
        st = engine_state(parse_report(ReasoningEngine.latest_reports.get(sym)), paper.get(sym), manual)
        if st:
            out[sym] = st
    return out


@app.get("/api/market/summary")
async def market_summary():
    """Everything on the Market tab that is not a per-tick number: the index,
    breadth, flows, the AI's global read, sectors, the order-book quadrant,
    each stock's engine state, and the news wire."""
    import time as _time
    from views import market as M
    rows = M.stocks(local_active_states)
    daily = _nifty_daily()
    ad, src = select_ad_ratio(local_fii_dii_state, _watchlist_ad())
    try:
        from core.risk import load_clusters
        clusters = load_clusters()
    except Exception:
        clusters = {}
    return {"status": "success", "as_of": _time.time(),
            "nifty": M.nifty(local_active_states.get(NIFTY_KEY), daily[-1] if daily else None, _time.time()),
            "breadth": M.breadth(local_fii_dii_state, ad, src),
            "flows": M.flows(local_fii_dii_state),
            "context": M.context(local_macro_context),
            "sectors": M.sectors(rows, clusters),
            "quadrant": M.quadrant(rows),
            "engine": _engine_states(),
            "wire": M.wire(local_catalyst_cache, {r["symbol"].upper() for r in rows}),
            "news": M.news_status(local_news_state),
            "symbols": [r["symbol"] for r in rows]}


_sparks_cache = {"at": 0.0, "body": None}


@app.get("/api/market/sparks")
async def market_sparks():
    """Today's closes per stock, for the board's sparklines (cached 60 s)."""
    import time as _time
    from urllib.parse import quote
    from views.market import sparks
    if _sparks_cache["body"] and _time.time() - _sparks_cache["at"] < 60:
        return _sparks_cache["body"]
    keys = [k for k in list(local_active_states) if not k.startswith("NSE_INDEX|")]
    results = await asyncio.gather(*[proxy_get(8001, f"/api/bars?token={quote(k)}&n=80", timeout=5) for k in keys])
    body = {"status": "success",
            "sparks": sparks({ReasoningEngine._normalize_symbol(k): (r or {}).get("bars") or [] for k, r in zip(keys, results)})}
    _sparks_cache.update(at=_time.time(), body=body)
    return body


@app.get("/api/market/index-history")
async def market_index_history(rng: str = Query("1M", alias="range")):
    """NIFTY 50 daily closes for the phone's 1W / 1M / 1Y / 5Y control."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    from views.market import index_history
    try:
        return {"status": "success", **index_history(_nifty_daily(), rng, _dt.now(ZoneInfo("Asia/Kolkata")).date())}
    except ValueError as e:
        return {"status": "error", "message": str(e)}


class NewsInstantRequest(BaseModel): model: str = DEFAULT_MODEL
class NewsStartRequest(BaseModel): interval: int = 120; model: str = DEFAULT_MODEL

@app.post("/api/news/instant")
async def instant_news_fetch(req: NewsInstantRequest): return await proxy_post(8003, "/api/news/instant", {"model": req.model})

@app.post("/api/news/fetch/{symbol}")
async def fetch_symbol_news_api(symbol: str, req: NewsInstantRequest): return await proxy_post(8003, f"/api/news/fetch/{symbol}", {"model": req.model})

@app.post("/api/news/loop/start")
async def start_news_loop_api(req: NewsStartRequest): return await proxy_post(8003, "/api/news/loop/start", {"interval": req.interval, "model": req.model})

@app.post("/api/news/loop/stop")
async def stop_news_loop_api(): return await proxy_post(8003, "/api/news/loop/stop")

@app.get("/api/news/state")
async def get_news_state(): return await proxy_get(8003, "/state")

class SyncParquetRequest(BaseModel):
    symbols: list[str]

@app.post("/api/admin/sync-parquet")
async def sync_parquet_endpoint(req: SyncParquetRequest):
    from data_services.parquet_engine import sync_eod_parquet
    # Fire and forget
    asyncio.create_task(sync_eod_parquet(req.symbols))
    return {"status": "success", "message": "Parquet sync started in the background."}

@app.get("/api/search-token")
async def search_token_api(q: str):
    import scrip_master_engine
    results = await scrip_master_engine.search_scrip_tokens(q)
    return {"status": "success", "data": results}

@app.get("/api/watchlist")
async def get_watchlist(): return {"status": "success", "data": [{"token": k, "symbol": v["symbol"], "exchange": v["exchange"]} for k, v in _reload_watchlist().items()]}

class WatchlistUpdateRequest(BaseModel): items: list[dict]

@app.post("/api/watchlist")
async def update_watchlist(req: WatchlistUpdateRequest):
    global watchlist
    try:
        _reload_watchlist()       # "new" must mean new relative to the file, not a stale copy
        with open(watchlist_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Token", "Symbol", "Exchange"])
            for item in req.items: writer.writerow([item["token"], item["symbol"], item["exchange"]])
            
        new_symbols = []
        for item in req.items:
            if item["token"] not in watchlist:
                new_symbols.append(item["symbol"].split('-')[0])
                
        watchlist.clear()
        for item in req.items:
            watchlist[item["token"]] = {"symbol": item["symbol"], "exchange": item["exchange"]}
            
        if new_symbols:
            logger.info(f"Triggering background Parquet sync for new symbols: {new_symbols}")
            from data_services.parquet_engine import sync_eod_parquet
            asyncio.create_task(sync_eod_parquet(new_symbols))
            
    except Exception as e: return {"status": "error", "message": str(e)}
    return await proxy_post(8001, "/api/watchlist/update", {"items": req.items})

class WatchlistAddRequest(BaseModel):
    token: str
    symbol: str
    exchange: str

@app.post("/api/watchlist/add")
async def add_to_watchlist(req: WatchlistAddRequest):
    if req.token in _reload_watchlist():
        return {"status": "success", "message": "Already in watchlist."}
    
    current_items = [{"token": k, "symbol": v["symbol"], "exchange": v["exchange"]} for k, v in watchlist.items()]
    current_items.append({"token": req.token, "symbol": req.symbol, "exchange": req.exchange})
    
    update_req = WatchlistUpdateRequest(items=current_items)
    return await update_watchlist(update_req)

# The dashboard's breadth card is fed either by the real NIFTY-50 A/D ratio
# (macro_worker.fetch_market_breadth, every ~5 min) or, when that is absent or
# stale, by a watchlist proxy -- advances/declines over ~27 self-selected
# symbols, which is NOT NSE-wide breadth. 30 min tolerates several missed
# fetches without ever presenting yesterday's number as today's.
AD_RATIO_MAX_AGE_S = 1800


def select_ad_ratio(flow_state: dict, watchlist_ad: float, now=None):
    """(value, source) where source is "NIFTY_50" or "WATCHLIST_PROXY".

    The real value is used only when it carries a fetch timestamp AND that
    timestamp is inside the freshness window -- a stored ad_ratio with no
    ad_ratio_ts could be arbitrarily old, so it falls back rather than being
    shown under an NSE-wide label.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")

    raw = (flow_state or {}).get("ad_ratio")
    ts = (flow_state or {}).get("ad_ratio_ts")
    if raw is not None and ts:
        try:
            stamped = datetime.fromisoformat(str(ts))
            if stamped.tzinfo is None:
                stamped = stamped.replace(tzinfo=ist)
            ref = now or datetime.now(ist)
            if 0 <= (ref - stamped).total_seconds() <= AD_RATIO_MAX_AGE_S:
                return float(raw), "NIFTY_50"
        except Exception:
            pass
    return watchlist_ad, "WATCHLIST_PROXY"


def make_json_serializable(obj):
    import numpy as np, pandas as pd
    if isinstance(obj, dict): return {str(k): make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple, set)): return [make_json_serializable(v) for v in obj]
    elif isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)): return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32, np.float16)): return 0.0 if np.isnan(obj) or np.isinf(obj) else float(obj)
    elif isinstance(obj, np.ndarray): return make_json_serializable(obj.tolist())
    elif isinstance(obj, pd.Timestamp): return obj.isoformat()
    elif isinstance(obj, (pd.DataFrame, pd.Series)): return make_json_serializable(obj.to_dict())
    elif isinstance(obj, float) and (pd.isna(obj) or obj != obj): return 0.0
    return obj

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            TerminalDashboard.active_states = local_active_states
            TerminalDashboard.global_market_context = local_macro_context
            
            # Derive PCR from Nifty 50 state if available
            nifty_state = local_active_states.get("NSE_INDEX|Nifty 50", {})
            pcr = nifty_state.get('stock_pcr', local_macro_state.get('pcr', 1.0))
            
            fii_net = local_fii_dii_state.get('fii_net', 0)
            dii_net = local_fii_dii_state.get('dii_net', 0)
            date_str = local_fii_dii_state.get('date', local_macro_state.get('date', 'N/A'))
            
            dynamic_ad = _watchlist_ad()
            ad_ratio, ad_ratio_source = select_ad_ratio(local_fii_dii_state, dynamic_ad)
            enriched_states = {}
            for instrument_key, payload in local_active_states.items():
                symbol = instrument_key.split('|')[-1] if '|' in instrument_key else instrument_key
                if symbol == "Nifty 50": continue
                
                payload_copy = dict(payload)
                from pipeline_guard import is_market_open
                payload_copy["market_state"] = "LIVE" if is_market_open() else "CLOSED"
                payload_copy["symbol"] = symbol
                from views.market import change_pct
                payload_copy["change_pct"] = change_pct(payload.get("ltp"), payload.get("prev_close"))
                
                # Fetch catalyst from News feed state
                if symbol in local_catalyst_cache:
                    payload_copy["latest_catalyst"] = local_catalyst_cache[symbol]
                
                try:
                    payload_copy["structured_payload"] = ReasoningEngine.build_structured_payload(symbol, payload_copy)
                except Exception as e:
                    logger.error(f"Error building structured payload for {symbol}: {e}")
                    payload_copy["structured_payload"] = dict(payload_copy)

                # Attention rank (§4.6, Task 5.1): lets the Live Action grid
                # sort by ev_r x freshness and collapse below the top N
                # client-side without recomputing it in JS. -inf (rejected
                # setup) is not valid JSON -- send null so a missing rank
                # and a rejected one sort identically to the bottom.
                try:
                    rank = attention_rank(payload_copy.get("structured_payload") or {})
                    payload_copy["attention_rank"] = rank if rank != float("-inf") else None
                except Exception as e:
                    logger.error(f"Error computing attention rank for {symbol}: {e}")
                    payload_copy["attention_rank"] = None

                enriched_states[symbol] = payload_copy

            payload = {
                "global_market_context": local_macro_context,
                "global_state": enriched_states,
                "paper": _paper_live_block(),
                "macro_state": {"pcr": pcr, "fii_net": fii_net, "dii_net": dii_net, "date": date_str,
                               "ad_ratio": ad_ratio, "ad_ratio_source": ad_ratio_source}
            }
            await websocket.send_json(make_json_serializable(payload))
            await asyncio.sleep(0.5)
    except WebSocketDisconnect: pass
    except Exception as e: logger.error(f"WebSocket loop exception: {e}")

_PAPER_CLOCK: dict | None = None


def _paper_clock() -> dict:
    """The trade clock the blotter shows against, from the decision policy
    (read once; a policy change needs a restart anyway)."""
    global _PAPER_CLOCK
    if _PAPER_CLOCK is None:
        from core.policy_config import load_policy
        p = load_policy()
        _PAPER_CLOCK = {"failure_to_launch_min": p.gates.get("failure_to_launch_min"),
                        "stagnation_min": p.gates.get("stagnation_min"),
                        "entry_cutoff": p.horizon.get("entry_cutoff_ist"),
                        "square_off": p.horizon.get("square_off_ist")}
    return _PAPER_CLOCK


def _paper_live_block():
    """Live paper account for the dashboard socket; says so when it isn't running."""
    try:
        from paper import runtime as paper_rt
        b = paper_rt.broker()
        if b is None:
            return {"engine_ok": False, "running": False}
        return dict(b.summary(), running=True, clock=_paper_clock(),
                    stale_price_seconds=b.settings.options()["stale_price_seconds"])
    except Exception as e:
        return {"engine_ok": False, "running": False, "last_error": str(e)}


class LedgerOpenRequest(BaseModel):
    symbol: str
    direction: str
    entry_price: float
    entry_qty: int
    confidence: int
    reason: str
    target: str | None = None
    stoploss: str | None = None

class LedgerManageRequest(BaseModel):
    symbol: str
    action: str
    reason: str
    target: str | None = None
    stoploss: str | None = None

class LedgerCloseRequest(BaseModel):
    symbol: str
    exit_price: float
    exit_qty: int
    reason: str
    charges: float = 0.0

@app.post("/api/ledger/open")
def open_ledger_trade(req: LedgerOpenRequest):
    manager = HistoryManager()
    trade_id = manager.create_trade(req.symbol, req.direction, req.entry_price, req.entry_qty, req.confidence, req.reason, req.target, req.stoploss)
    return {"status": "success", "trade_id": trade_id}

@app.post("/api/ledger/manage")
def manage_ledger_trade(req: LedgerManageRequest):
    manager = HistoryManager()
    success = manager.add_management_log(req.symbol, req.action, req.reason, req.target, req.stoploss)
    return {"status": "success", "updated": success}

@app.post("/api/ledger/close")
def close_ledger_trade(req: LedgerCloseRequest):
    manager = HistoryManager()
    success = manager.add_exit(req.symbol, req.exit_price, req.exit_qty, req.reason, req.charges)
    return {"status": "success", "updated": success}

async def start_api_server():
    logger.info("Starting Web API Server (Port 8000)...")
    # 0.0.0.0 exposed this unauthenticated API to the whole LAN (E-1). If LAN
    # access is genuinely wanted, keep 0.0.0.0 but add a shared-secret header
    # dependency first — do not leave it open.
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning", ws_ping_interval=None)
    await uvicorn.Server(config).serve()
