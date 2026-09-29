"""Discovery as a background job in the feed process (replaces A-7's
request-scoped, LLM-narrated playbook).

The job owns three promises: a start never blocks the caller for the length
of a universe scan; two starts never run two scans; and the status it
reports is only ever what the scan and the live-apply step actually did.
"""
import asyncio

import pytest

from discovery_job import DiscoveryJob


def _pick(sym, token=None):
    return {"token": token or f"T-{sym}", "symbol": sym, "exchange": "NSE",
            "score": 50, "directional_bias": "LONG", "rs": 1.2}


async def _drain(job):
    while job.status()["state"] == "running":
        await asyncio.sleep(0)


async def test_initial_status_is_idle_with_nothing_reported():
    st = DiscoveryJob().status()
    assert st["state"] == "idle"
    assert st["picks"] is None and st["live_added"] is None
    assert st["started_at"] is None


async def test_start_returns_before_the_scan_finishes():
    gate = asyncio.Event()

    async def scan(progress):
        await gate.wait()
        return [_pick("SAIL")], [_pick("SAIL")]

    async def apply(selected):
        return [], []

    job = DiscoveryJob()
    assert job.start(scan, apply) is True
    await asyncio.sleep(0)
    assert job.status()["state"] == "running"
    gate.set()
    await _drain(job)
    assert job.status()["state"] == "done"


async def test_a_second_start_while_running_does_not_launch_a_second_scan():
    calls = []
    gate = asyncio.Event()

    async def scan(progress):
        calls.append(1)
        await gate.wait()
        return [], []

    async def apply(selected):
        return [], []

    job = DiscoveryJob()
    assert job.start(scan, apply) is True
    await asyncio.sleep(0)
    assert job.start(scan, apply) is False
    gate.set()
    await _drain(job)
    assert calls == [1]


async def test_done_reports_picks_and_what_the_apply_step_actually_did():
    async def scan(progress):
        progress(2, 4)
        progress(4, 4)
        return [_pick("SAIL"), _pick("BHEL")], [_pick("SAIL")]

    seen = {}

    async def apply(selected):
        seen["selected"] = [s["symbol"] for s in selected]
        return ["T-SAIL"], [{"token": "T-OLD", "symbol": "OLD"}]

    job = DiscoveryJob()
    job.start(scan, apply)
    await _drain(job)
    st = job.status()
    assert st["state"] == "done"
    assert [p["symbol"] for p in st["picks"]] == ["SAIL", "BHEL"]
    assert seen["selected"] == ["SAIL"]          # apply gets the bounded set
    assert st["live_added"] == ["T-SAIL"]
    assert st["removals_deferred"] == [{"token": "T-OLD", "symbol": "OLD"}]
    assert st["scanned"] == 4 and st["total"] == 4
    assert st["finished_at"] is not None


async def test_no_bounded_selection_means_nothing_is_applied():
    """run_scan leaves watchlist.csv untouched when selection fails; the
    live feed must not be changed either."""
    applied = []

    async def scan(progress):
        return [_pick("SAIL")], None

    async def apply(selected):
        applied.append(selected)
        return ["X"], []

    job = DiscoveryJob()
    job.start(scan, apply)
    await _drain(job)
    st = job.status()
    assert st["state"] == "done"
    assert applied == []
    assert st["live_added"] == [] and st["removals_deferred"] == []


async def test_a_failed_scan_is_reported_as_an_error_not_as_empty_results():
    async def scan(progress):
        raise RuntimeError("upstox 401")

    async def apply(selected):
        return [], []

    job = DiscoveryJob()
    job.start(scan, apply)
    await _drain(job)
    st = job.status()
    assert st["state"] == "error"
    assert "upstox 401" in st["error"]
    assert st["picks"] is None                   # not [] -- nothing was measured


async def test_a_new_run_clears_the_previous_result():
    async def scan_ok(progress):
        return [_pick("SAIL")], [_pick("SAIL")]

    async def scan_fail(progress):
        raise RuntimeError("boom")

    async def apply(selected):
        return ["T-SAIL"], []

    job = DiscoveryJob()
    job.start(scan_ok, apply)
    await _drain(job)
    job.start(scan_fail, apply)
    await _drain(job)
    st = job.status()
    assert st["state"] == "error"
    assert st["picks"] is None and st["live_added"] is None


# -- AI analysis of the top picks ------------------------------------------
async def _ok_scan(progress):
    return [_pick("SAIL"), _pick("BHEL")], [_pick("SAIL")]


async def _no_apply(selected):
    return [], []


async def test_analysis_runs_on_the_picks_after_the_scan():
    seen = []

    async def analyze(picks):
        seen.append([p["symbol"] for p in picks])
        return {"items": {"SAIL": {"thesis": "t"}}, "missing": ["BHEL"]}

    job = DiscoveryJob()
    job.start(_ok_scan, _no_apply, analyze)
    await _drain(job)
    st = job.status()
    assert seen == [["SAIL", "BHEL"]]
    assert st["state"] == "done"
    assert st["analysis"]["items"]["SAIL"]["thesis"] == "t"
    assert st["analysis_error"] is None


async def test_a_failed_analysis_does_not_discard_the_scan():
    async def analyze(picks):
        raise RuntimeError("429 quota")

    job = DiscoveryJob()
    job.start(_ok_scan, _no_apply, analyze)
    await _drain(job)
    st = job.status()
    assert st["state"] == "done"
    assert [p["symbol"] for p in st["picks"]] == ["SAIL", "BHEL"]
    assert st["analysis"] is None
    assert "429 quota" in st["analysis_error"]


async def test_picks_are_visible_while_the_analysis_is_still_running():
    gate = asyncio.Event()

    async def analyze(picks):
        await gate.wait()
        return {"items": {}, "missing": []}

    job = DiscoveryJob()
    job.start(_ok_scan, _no_apply, analyze)
    for _ in range(5):
        await asyncio.sleep(0)
    st = job.status()
    assert st["state"] == "running" and st["phase"] == "analyzing"
    assert [p["symbol"] for p in st["picks"]] == ["SAIL", "BHEL"]
    gate.set()
    await _drain(job)


async def test_without_an_analyzer_there_is_no_analysis_and_no_error():
    job = DiscoveryJob()
    job.start(_ok_scan, _no_apply)
    await _drain(job)
    st = job.status()
    assert st["analysis"] is None and st["analysis_error"] is None


# -- the live-apply step failing after watchlist.csv was written -----------
async def test_an_apply_failure_keeps_the_picks_and_still_analyses_them():
    """run_scan has already rewritten watchlist.csv by the time apply runs.
    Reporting the whole run as failed would discard real results, and the
    UI's 'watchlist was not changed' would be false."""
    analysed = []

    async def apply(selected):
        raise RuntimeError("WebSocket is not open.")

    async def analyze(picks):
        analysed.append(len(picks))
        return {"items": {}, "missing": []}

    job = DiscoveryJob()
    job.start(_ok_scan, apply, analyze)
    await _drain(job)
    st = job.status()
    assert st["state"] == "done"
    assert [p["symbol"] for p in st["picks"]] == ["SAIL", "BHEL"]
    assert "WebSocket is not open" in st["apply_error"]
    assert st["watchlist_written"] is True
    assert st["live_added"] is None                  # unknown, not "none added"
    assert analysed == [2]


async def test_pending_subscriptions_are_reported():
    async def apply(selected):
        return ["T-SAIL"], [], ["NSE_EQ|SAIL"]

    job = DiscoveryJob()
    job.start(_ok_scan, apply)
    await _drain(job)
    st = job.status()
    assert st["live_added"] == ["T-SAIL"]
    assert st["subscription_pending"] == ["NSE_EQ|SAIL"]
    assert st["watchlist_written"] is True and st["apply_error"] is None


async def test_no_selection_means_the_watchlist_was_not_written():
    async def scan(progress):
        return [_pick("SAIL")], None

    job = DiscoveryJob()
    job.start(scan, _no_apply)
    await _drain(job)
    assert job.status()["watchlist_written"] is False


async def test_the_whole_scored_universe_is_published_with_the_picks():
    """F4 (2026-09-29): only the top 20 left the scan, so Discovery could not
    show whether the picks were real outliers. The job now carries every
    scored stock; a scan that returns only picks and a selection still works."""
    universe = [{"symbol": s, "score": sc, "rs": 0.1, "adv_crore": 50.0, "directional_bias": "LONG"}
                for s, sc in (("SAIL", 70), ("IDEA", 12), ("NMDC", 5))]

    async def scan(progress):
        return [_pick("SAIL")], None, universe

    async def apply(selected):
        return [], []

    job = DiscoveryJob()
    job.start(scan, apply)
    await _drain(job)
    assert job.status()["universe"] == universe

    async def old_scan(progress):
        return [_pick("SAIL")], None

    job2 = DiscoveryJob()
    job2.start(old_scan, apply)
    await _drain(job2)
    assert job2.status()["state"] == "done" and job2.status()["universe"] is None
