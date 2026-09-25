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
