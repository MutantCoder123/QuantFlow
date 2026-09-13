"""`fetch_market_breadth` control flow (Task 5.6 fix-round 1).

The property that matters: it NEVER writes a fabricated value. The legacy
implementation stored ad_ratio = 1.0 when no NIFTY 50 row was found; this port
returns without writing instead, and that guarantee should not rest on code
reading alone.
"""
import asyncio

import pytest

from data_services import macro_worker
from data_services.macro_worker import InstitutionalFlowTracker, fetch_market_breadth

# A captured allIndices-shaped body, trimmed to the fields the parser reads.
ALL_INDICES = {"data": [
    {"index": "NIFTY BANK", "advances": "3", "declines": "9"},
    {"index": "NIFTY 50", "advances": "34", "declines": "16"},
    {"index": "NIFTY IT", "advances": "1", "declines": "9"},
]}


class _FakeGet:
    """aiohttp's get() is both awaitable and an async context manager."""

    def __init__(self, resp):
        self._resp = resp

    def __await__(self):
        async def _noop():
            return self._resp
        return _noop().__await__()

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _FakeResponse:
    def __init__(self, status, payload=None, body=""):
        self.status = status
        self._payload = payload
        self._body = body

    async def json(self):
        return self._payload

    async def text(self):
        return self._body


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, timeout=None):
        # The first GET is the Akamai handshake against the base URL; the
        # payload response is only for the api/allIndices call.
        return _FakeGet(self._resp)


@pytest.fixture
def writes(monkeypatch):
    """Capture save_ad_ratio instead of touching the real state file."""
    captured = []
    monkeypatch.setattr(InstitutionalFlowTracker, "save_ad_ratio",
                        staticmethod(lambda v: captured.append(v)))
    # The port keeps the legacy 3s anti-bot pause; don't pay it in tests.
    real_sleep = asyncio.sleep
    monkeypatch.setattr(macro_worker.asyncio, "sleep",
                        lambda s: real_sleep(0))
    return captured


def _run(monkeypatch, response, writes):
    monkeypatch.setattr(macro_worker.aiohttp, "ClientSession",
                        lambda *a, **k: _FakeSession(response))
    asyncio.run(fetch_market_breadth())
    return writes


def test_computes_the_nifty50_ad_ratio(monkeypatch, writes):
    out = _run(monkeypatch, _FakeResponse(200, ALL_INDICES), writes)
    assert out == [pytest.approx(34 / 16)]


def test_non_200_writes_nothing(monkeypatch, writes):
    """No fallback value, no stale re-stamp -- the stored ad_ratio is left
    exactly as it was, so the UI can see it age out."""
    out = _run(monkeypatch, _FakeResponse(503, body="upstream error"), writes)
    assert out == []


def test_missing_nifty50_row_writes_nothing(monkeypatch, writes):
    """The legacy implementation wrote a fabricated 1.0 here."""
    body = {"data": [{"index": "NIFTY BANK", "advances": "3", "declines": "9"}]}
    out = _run(monkeypatch, _FakeResponse(200, body), writes)
    assert out == []


def test_empty_body_writes_nothing(monkeypatch, writes):
    out = _run(monkeypatch, _FakeResponse(200, {}), writes)
    assert out == []


def test_an_exception_writes_nothing(monkeypatch, writes):
    def _boom(*a, **k):
        raise ConnectionError("NSE unreachable")
    monkeypatch.setattr(macro_worker.aiohttp, "ClientSession", _boom)
    asyncio.run(fetch_market_breadth())
    assert writes == []


def test_zero_declines_does_not_divide_by_zero(monkeypatch, writes):
    body = {"data": [{"index": "NIFTY 50", "advances": "50", "declines": "0"}]}
    out = _run(monkeypatch, _FakeResponse(200, body), writes)
    assert len(out) == 1 and out[0] > 1.0


def test_a_flat_index_reads_as_neutral(monkeypatch, writes):
    body = {"data": [{"index": "NIFTY 50", "advances": "0", "declines": "0"}]}
    out = _run(monkeypatch, _FakeResponse(200, body), writes)
    assert out == [1.0]


def test_breadth_loop_does_not_poll_when_the_market_is_shut(monkeypatch):
    """`ad_ratio_ts` records fetch time, so off-hours polling would re-stamp
    the previous session's close as if it were minutes old."""
    calls = []
    monkeypatch.setattr(macro_worker, "fetch_market_breadth",
                        lambda: calls.append(1) or asyncio.sleep(0))
    monkeypatch.setattr("pipeline_guard.is_market_open", lambda: False)

    async def _one_pass():
        task = asyncio.ensure_future(macro_worker.breadth_poller_loop())
        await asyncio.sleep(0)          # let it reach the first gate
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_one_pass())
    assert calls == []


def test_breadth_loop_polls_while_the_market_is_open(monkeypatch):
    calls = []
    monkeypatch.setattr(macro_worker, "fetch_market_breadth",
                        lambda: calls.append(1) or asyncio.sleep(0))
    monkeypatch.setattr("pipeline_guard.is_market_open", lambda: True)

    async def _one_pass():
        task = asyncio.ensure_future(macro_worker.breadth_poller_loop())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_one_pass())
    assert calls == [1]
