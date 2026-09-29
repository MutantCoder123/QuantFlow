"""The redesign's "nothing lost" check (dev-notes/app-redesign PRD, Architecture
§5): every control and view the old 3,261-line page had still has a home in
the new modules, found here by the endpoint it calls or the action it names.
A later refactor that drops one fails this test instead of passing silently."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server

STATIC = Path(__file__).resolve().parents[1] / "trading_copilot" / "static"
TEMPLATE = Path(__file__).resolve().parents[1] / "trading_copilot" / "templates" / "index.html"


def src(*parts) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for part in parts for p in sorted((STATIC / part).rglob("*.js")))


# (what the old page had, where it lives now, the markers that prove it)
HOMES = [
    ("alerts bell and tray, mark read", "shell", ["/api/alerts/unread", "/api/alerts/history", "/api/alerts/mark-read/"]),
    ("connection / stale-feed status", "shell", ["feedHealth", "Prices stopped updating"]),
    ("tab navigation, incl. on phones", "shell", ["mountBottom", "#inspect/", "#settings"]),
    ("macro weather: PCR, FII, DII, breadth", "market", ["/api/market/summary", "breadthView", "flowsView"]),
    ("global market context (AI summary)", "market", ["globalRead"]),
    ("market matrix, every column", "market", ["Max pain", "IV rank", "PCR", "RSI", "Delta", "Pattern", "Book"]),
    ("news engine: instant fetch", "market", ["/api/news/instant"]),
    ("news engine: auto, interval, model, status", "settings", ["/api/news/loop/start", "/api/news/loop/stop", "/api/news/state"]),
    ("prompt settings: edit, save, reset", "settings", ["save-prompt", "reset-confirm"]),
    ("watchlist: search, add, remove, save", "settings", ["/api/search-token", "'/api/watchlist'", "remove", "save-watchlist"]),
    ("end-of-day parquet sync", "settings", ["/api/admin/sync-parquet"]),
    ("score reliability", "review", ["/api/review", "reliabilityView"]),
    ("session review: date, staleness, groups", "review", ["/api/session/review", "groupsView", "data-act=\"date\""]),
    ("cluster exposure", "signals", ["/api/risk/exposure", "sectorRisk"]),
    ("live action cards: accept, dismiss, show more", "signals", ["/api/signals/queue", "'log'", "'dismiss'", "FILTERS"]),
    ("global auto-analyze and its interval", "shared", ["/api/reasoning/loop/start", "/api/reasoning/loop/stop", "global_analyze_interval"]),
    ("LLM trigger count", "signals", ["ai_calls"]),
    ("discovery: model, run, status, cards, AI commentary, add", "discovery",
     ["/api/discovery/run", "/api/discovery/view", "/api/watchlist/add", "/api/ai/models", "thesis"]),
    ("AI modal: instant, auto, model, report, action plan", "inspector",
     ["/api/reasoning/instant/", "/api/reasoning/report/", "/api/reasoning/loop/start", "/api/ai/models", "planOf"]),
    ("AI modal: proposed-trade intent, inject and copy", "inspector", ["parseIntent", "copy-ai", "copyText"]),
    ("AI modal: news catalyst and force fetch", "inspector", ["/api/news/fetch/", "newsOf"]),
    ("AI modal: score provenance", "inspector", ["waterfall", "contributions"]),
    ("AI modal: raw telemetry and copy", "inspector", ["copy-json", "rawView"]),
    ("position modal: edit and clear a manual position", "inspector", ["pos-edit", "pos-clear", "savePosition"]),
    ("ledger modal: log open, close, hold", "inspector", ["/api/ledger/open", "/api/ledger/close", "/api/ledger/manage"]),
    ("manual positions resent on load", "shared", ["/api/reasoning/position/sync_all", "/api/reasoning/position/save"]),
]


@pytest.mark.parametrize("what,part,markers", HOMES, ids=[h[0] for h in HOMES])
def test_every_old_control_has_a_home(what, part, markers):
    code = src(part)
    missing = [m for m in markers if m not in code]
    assert not missing, f"{what}: {missing} not found under static/{part}/"


def test_the_page_is_only_the_shell():
    page = TEMPLATE.read_text(encoding="utf-8")
    assert len(page.splitlines()) < 60
    assert "tailwind" not in page.lower()
    assert not re.search(r"\son[a-z]+=", page)                    # no inline handlers
    assert "<script>" not in page                                  # no inline code
    for tab in ("market", "signals", "discovery", "performance", "review"):
        assert f'<section id="tab-{tab}"' in page
    assert TestClient(api_server.app).get("/").text == page


EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def test_no_emoji_icons_and_no_inline_handlers_in_the_modules():
    for p in STATIC.rglob("*.js"):
        text = p.read_text(encoding="utf-8")
        assert not EMOJI.search(text), f"emoji in {p.relative_to(STATIC)}"
        assert not re.search(r"\son(click|change|input|keyup|submit)=", text), f"inline handler in {p.relative_to(STATIC)}"
