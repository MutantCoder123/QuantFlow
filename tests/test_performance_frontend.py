"""The Performance tab's wiring: its files are served, index.html carries the
agreed hooks and nothing else of it, and the live block has what the status
line needs."""
import re

import pytest
from fastapi.testclient import TestClient

import api_server
from paper import runtime as paper_rt
from test_paper_broker_open import make, sig

client = TestClient(api_server.app)


@pytest.mark.parametrize("path", ["/static/performance/main.js", "/static/performance/performance.css",
                                  "/static/performance/core/format.js",
                                  "/static/performance/components/statement.js"])
def test_tab_files_are_served(path):
    r = client.get(path)
    assert r.status_code == 200 and r.text
    assert r.headers["cache-control"] == "no-cache"          # always revalidated


def test_static_serves_nothing_outside_its_directory():
    assert client.get("/static/../api_server.py").status_code == 404
    assert client.get("/static/%2e%2e/api_server.py").status_code == 404


def test_index_carries_the_hooks_and_only_the_hooks():
    page = client.get("/").text
    assert '<section id="tab-performance" class="hidden"' in page
    assert 'id="tab-btn-performance"' in page
    assert '<script type="module" src="/static/performance/main.js"></script>' in page
    assert 'href="/static/performance/performance.css"' in page
    assert "window.dispatchEvent(new CustomEvent('qf:ws', { detail: payload }));" in page
    assert "window.dispatchEvent(new CustomEvent('qf:tab', { detail: tabId }));" in page
    assert "IBM+Plex+Sans" in page and "Doto" in page
    # the tab's markup and logic live in /static, not here
    body = page.split('<section id="tab-performance"', 1)[1].split("</section>", 1)[0]
    assert re.fullmatch(r'\s*class="hidden" aria-label="Performance">\s*', body)
    assert "pf-" not in page


def test_live_block_says_running_and_when_prices_were_marked(tmp_path, monkeypatch):
    b, clock, _ = make(tmp_path)
    monkeypatch.setattr(paper_rt, "_broker", b)
    b.on_signal(sig())
    b.mark("SAIL", 101.0)
    blk = api_server._paper_live_block()
    assert blk["running"] is True and blk["failed_at"] is None
    assert blk["open"][0]["last_mark_ts"] == clock.t


def test_a_persistence_failure_records_when(tmp_path, monkeypatch):
    b, clock, _ = make(tmp_path)

    def broken(event):
        raise OSError("disk full")
    monkeypatch.setattr(b.store, "append", broken)
    b.on_signal(sig())
    t0 = clock.t
    clock.t += 60
    b.on_signal(sig(symbol="IDEA"))            # a second failure keeps the first time
    s = b.summary()
    assert s["engine_ok"] is False and s["failed_at"] == t0 and "disk full" in s["last_error"]


def test_metrics_say_what_the_return_is_measured_against(tmp_path, monkeypatch):
    b, _, _ = make(tmp_path)
    monkeypatch.setattr(paper_rt, "_broker", b)
    h = client.get("/api/paper/metrics?range=all").json()["headline"]
    assert h["start_equity"]["value"] == 1_000_000


def test_settings_say_what_use_default_restores(tmp_path, monkeypatch):
    b, _, _ = make(tmp_path)
    monkeypatch.setattr(paper_rt, "_broker", b)
    body = client.post("/api/paper/settings", json={"risk_per_trade_pct": 0.4}).json()
    assert body["settings"]["risk_per_trade_pct"] == 0.4
    assert body["defaults"]["risk_per_trade_pct"] == 0.5 and body["sources"]["risk_per_trade_pct"] == "override"
    assert set(body["defaults"]) == set(body["editable"])


def test_live_block_carries_the_trade_clock(tmp_path, monkeypatch):
    b, _, _ = make(tmp_path)
    monkeypatch.setattr(paper_rt, "_broker", b)
    blk = api_server._paper_live_block()
    from core.policy_config import load_policy
    p = load_policy()
    assert blk["clock"] == {"failure_to_launch_min": p.gates["failure_to_launch_min"],
                            "stagnation_min": p.gates["stagnation_min"],
                            "entry_cutoff": p.horizon["entry_cutoff_ist"], "square_off": p.horizon["square_off_ist"]}
    assert blk["stale_price_seconds"] == 15


def test_equity_lists_session_days_and_the_starting_equity(tmp_path, monkeypatch):
    from test_performance_metrics import NOW, book
    b, _, _ = make(tmp_path, t=NOW)
    for e in book():
        b.store.append(e)
    monkeypatch.setattr(paper_rt, "_broker", b)
    eq = client.get("/api/paper/equity?range=today").json()
    assert eq["session_days"] == ["2026-09-22"]
    # capital 10 lakh (make()'s risk.yaml) plus Monday's banked +500
    assert eq["start_equity"] == 1_000_500
    empty = client.get("/api/paper/equity?range=custom&from=2026-01-01&to=2026-01-02").json()
    assert empty["session_days"] == [] and empty["start_equity"] == 1_000_000
