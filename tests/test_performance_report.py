"""The evidence report (Phase 7): what it names, what it refuses to name,
the config-version comparison, the session trend, the end-of-day writer,
and that nothing in the performance package can write configuration."""
import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
from paper import runtime as paper_rt
from performance.report import build, inr, to_markdown
from test_paper_broker_open import make
from test_performance_metrics import LOW, NOW, REAL, at, book, scope, trade

client = TestClient(api_server.app)
PKG = Path(__file__).resolve().parents[1] / "trading_copilot" / "performance"


def section(rep, sid):
    return next(s for s in rep["sections"] if s["id"] == sid)


def test_rupees_use_indian_grouping():
    assert inr(1012480) == "+₹10,12,480"
    assert inr(-630) == "−₹630"
    assert inr(0) == "₹0"
    assert inr(123456789, signed=False) == "₹12,34,56,789"


def test_weakest_segments_are_named_with_n_and_knobs():
    rep = build(scope(), [], LOW, NOW)
    weak = section(rep, "weakest")["items"]
    assert weak, "the hand-computed book has losing segments"
    # every named segment loses on average, and says so with its sample and its knobs
    for it in weak:
        assert "average −" in it["text"] and it["n"] >= 1 and it["tunes"]
    assert len(weak) <= 3
    # IDEA (a −₹500 short) is the most rupees lost among single-trade segments... ranked by net
    nets = [it["text"] for it in weak]
    assert any("Shorts in lunch chop" in t or "IDEA" in t for t in nets)
    # T2 alone is "shorts in lunch chop", "IDEA" and cluster "C-IDEA": it is named once, not three times
    assert sum("−₹500" in t for t in nets) == 1


def test_below_the_minimums_it_names_nothing_and_says_what_is_missing():
    rep = build(scope(), [], REAL, NOW)
    assert section(rep, "weakest")["items"] == []
    assert "10 or more trades" in section(rep, "weakest")["empty"]
    assert section(rep, "exits")["items"] == []
    assert "Win rate needs 20 trades (have 4)." in rep["not_yet"]
    assert "Sharpe needs 20 sessions (have 2)." in rep["not_yet"]


def test_losing_rule_exits_are_listed_but_planned_stops_are_not():
    rep = build(scope(), [], LOW, NOW)
    texts = [it["text"] for it in section(rep, "exits")["items"]]
    assert texts == ["Square-off exits: −₹200 across 1 trade, average −0.40 R."]
    assert section(rep, "exits")["items"][0]["tunes"] == ["horizon.entry_cutoff_ist", "horizon.square_off_ist"]


def test_config_version_comparison():
    ev = book()
    for e in ev:                                   # T3 and T4 opened under policy v2
        if e["type"] == "OPEN" and e["pos_id"] in ("P3", "P4"):
            e["config_version"] = 2
    s = section(build(scope(ev), [], {**LOW, "rates": 2}, NOW), "versions")
    assert [it["text"] for it in s["items"]] == [
        "Policy v1: 2 trades, +₹500 net, average +0.50 R.",
        "Policy v2: 2 trades, +₹100 net, average +0.10 R.",
    ]
    assert s["verdict"] == "Policy v2 averages +0.10 R per trade against +0.50 R for v1: worse by 0.40 R."
    one = section(build(scope(), [], LOW, NOW), "versions")
    assert one["verdict"] is None and one["note"].startswith("Only one policy version has traded in this range")
    assert "_Only one policy version" in to_markdown(build(scope(), [], LOW, NOW))


def ten_sessions():
    ev, n = [], 0
    days = [7, 8, 9, 10, 11, 14, 15, 16, 17, 18]
    for i, d in enumerate(days):
        n += 1
        win = i >= 5                               # the last five sessions win
        ev += trade(n, "SAIL", "LONG", at(d, 10, 0), at(d, 11, 0), 600 if win else -400, 20,
                    1.16 if win else -0.84, 1.2 if win else -0.8, -0.3, 1.2, "TARGET" if win else "STOP",
                    0.4, "TREND_EXPANSION")
    return ev


def test_session_trend_compares_the_last_five_with_the_five_before():
    s = section(build(scope(ten_sessions()), [], {**LOW, "rates": 5}, at(18, 16, 0)), "trend")
    assert s["items"][0]["text"] == ("Last 5 sessions (2026-09-14 to 2026-09-18): +₹580 a session, 5 trades; "
                                     "the 5 before: −₹420 a session, 5 trades.")
    assert s["items"][1]["text"] == "Average trade moved from −0.84 R to +1.16 R."
    short = section(build(scope(), [], LOW, NOW), "trend")
    assert short["items"] == [] and "have 2" in short["empty"]


def test_markdown_carries_the_disclaimer_findings_and_knobs():
    md = to_markdown(build(scope(), [], LOW, NOW))
    assert md.startswith("# Paper account evidence report")
    assert "> This report recommends only." in md
    assert "## Weakest segments" in md and "`" in md          # knobs as code
    assert "## Did my change help?" in md


def test_the_end_of_day_report_is_written_once_after_the_square_off(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_rt, "_report_day", None)
    b, clock, _ = make(tmp_path, t=at(22, 15, 0))
    for e in book():
        b.store.append(e)
    out = tmp_path / "reports"
    assert paper_rt.write_day_report(b, out) is None               # 15:00: not yet
    clock.t = at(22, 15, 25)
    path = paper_rt.write_day_report(b, out)
    assert path and Path(path).name == "evidence_2026-09-22.md"
    assert json.loads((out / "evidence_2026-09-22.json").read_text(encoding="utf-8"))["trades"] == 4
    assert paper_rt.write_day_report(b, out) is None               # once a day
    monkeypatch.setattr(paper_rt, "_report_day", None)
    assert paper_rt.write_day_report(b, out) is None               # and not again after a restart
    clock.t = at(23, 15, 30)                                         # a day with no session: nothing
    assert paper_rt.write_day_report(b, out) is None
    assert sorted(p.name for p in out.iterdir()) == ["evidence_2026-09-22.json", "evidence_2026-09-22.md"]


async def test_a_failing_report_is_tried_once_a_day_and_never_breaks_the_loop(tmp_path, monkeypatch):
    b, clock, _ = make(tmp_path, t=at(22, 15, 30))
    monkeypatch.setattr(paper_rt, "_broker", b)
    monkeypatch.setattr(paper_rt, "_report_day", None)
    calls = []

    def boom(broker, reports_dir=None):
        calls.append(1)
        raise OSError("disk full")
    real = paper_rt.write_day_report
    monkeypatch.setattr(paper_rt, "write_day_report", boom)
    await paper_rt.tick(lambda s: (None, None))
    assert calls == [1] and b.error_count == 0
    assert paper_rt._report_day == "2026-09-22"
    monkeypatch.setattr(paper_rt, "write_day_report", real)
    assert paper_rt.write_day_report(b, tmp_path / "r") is None     # marked done for the day


def test_report_endpoint(tmp_path, monkeypatch):
    b, _, _ = make(tmp_path, t=NOW)
    for e in book():
        b.store.append(e)
    monkeypatch.setattr(paper_rt, "_broker", b)
    monkeypatch.setattr(api_server, "_paper_arms", lambda sc: [])
    body = client.get("/api/paper/report?range=all").json()
    assert body["status"] == "success" and body["report"]["trades"] == 4
    md = client.get("/api/paper/report?range=all&fmt=md")
    assert md.headers["content-type"].startswith("text/plain") and "evidence report" in md.text
    dl = client.get("/api/paper/report?range=today&fmt=md&download=1")
    assert "attachment" in dl.headers["content-disposition"]
    assert client.get("/api/paper/report?fmt=pdf").json()["status"] == "error"


def test_nothing_in_the_performance_package_can_write_configuration():
    """The report recommends only. No module here opens a file for writing,
    dumps YAML, or touches the settings/overrides writer."""
    for py in PKG.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                assert name not in ("write_text", "write_bytes", "safe_dump", "dump", "update_settings"), (py.name, name)
                if name == "open":
                    modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
                    modes += [k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
                    assert not any(set(m) & set("wax+") for m in modes), (py.name, modes)
    # and the only writer on the runtime side targets the paper data folder, not config
    src = (PKG.parent / "paper" / "runtime.py").read_text(encoding="utf-8")
    assert 'b.store.dir / "reports"' in src and "CONFIG_DIR" not in src


def test_a_winning_segment_is_never_called_weak():
    only_wins = [e for e in book() if e["pos_id"] in ("P1", "P3")]
    rep = build(scope(only_wins), [], LOW, NOW)
    assert section(rep, "weakest")["items"] == []
