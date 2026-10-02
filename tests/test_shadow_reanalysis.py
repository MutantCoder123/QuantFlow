"""The shadow AI re-analysis: which journal moments it asks about, the
prompt, reading the answer, and scoring answers against what happened."""
import pytest

from shadow.reanalysis import CHECK_EVERY_S, build_prompt, parse_decision, pending_moments
from shadow.report import score_record, summarize, to_markdown

from test_paper_broker_open import COSTS as _REAL
COSTS = {k: 0.0 if isinstance(v, (int, float)) else v for k, v in _REAL.items()}   # zero charges: exact rupees
T0 = 1_790_830_000.0
OPEN = {"type": "OPEN", "pos_id": "p1", "symbol": "SAIL", "token": "NSE_EQ|SAIL", "side": "LONG", "qty": 100,
        "entry_price": 100.0, "stop": 98.0, "target": 104.0, "ts": T0,
        "context": {"composite": 0.6, "regime": "TREND_EXPANSION", "rationale": "flow confirms"}}


def review(i, kind, ts, **kw):
    return {"type": "REVIEW", "id": f"r{i}", "pos_id": "p1", "symbol": "SAIL", "kind": kind, "ts": ts,
            "note": kind.lower(), "r_now": 0.5, "best_r": 0.8, "last": 101.0, "stop": 98.0, "target": 104.0, **kw}


# ------------------------------------------------------------------ moments
def test_it_asks_at_decision_moments_once_and_samples_checks():
    ev = [OPEN, review(1, "CHECK", T0 + 300), review(2, "CHECK", T0 + 600), review(3, "TRAIL", T0 + 700),
          review(4, "RUN_ON", T0 + 900), review(5, "CHECK", T0 + 300 + CHECK_EVERY_S)]
    got = [m["id"] for m, o, j in pending_moments(ev, set(), {})]
    assert got == ["r1", "r4", "r5"]                                   # TRAIL is not asked; checks sampled
    again = pending_moments(ev, {"r1", "r4", "r5"}, {})
    assert [m["id"] for m, o, j in again] == ["r2"]                    # a fresh process: r2 is next


def test_the_journal_handed_over_is_everything_up_to_the_moment():
    ev = [OPEN, review(1, "CHECK", T0 + 300), review(2, "THESIS_AGAINST", T0 + 900), review(3, "CHECK", T0 + 1200)]
    (m, o, j), = [x for x in pending_moments(ev, {"r1"}, {}) if x[0]["id"] == "r2"]
    assert [r["id"] for r in j] == ["r1", "r2"] and o["pos_id"] == "p1"


def test_a_closed_position_is_not_asked_about():
    ev = [OPEN, review(1, "RUN_ON", T0 + 300), {"type": "CLOSE", "pos_id": "p1", "ts": T0 + 400, "net": 1.0}]
    assert pending_moments(ev, set(), {}) == []


# ------------------------------------------------------------------ prompt and answer
def test_the_prompt_carries_entry_thinking_the_journal_and_the_question():
    j = [review(1, "CHECK", T0 + 300), review(2, "RUN_ON", T0 + 900)]
    p = build_prompt(j[-1], OPEN, j, bars=[{"timestamp": "10:15", "open": 1, "high": 2, "low": 1, "close": 2}])
    assert "SAIL" in p and "LONG" in p and "flow confirms" in p
    assert "RUN_ON" in p and "reached the target" in p
    assert '"decision": "EXIT_NOW" | "HOLD" | "TIGHTEN_TO_BREAKEVEN"' in p


@pytest.mark.parametrize("text,want", [
    ('{"decision": "EXIT_NOW", "confidence": 7, "reason": "momentum gone"}', "EXIT_NOW"),
    ('Sure.\n```json\n{"decision": "hold", "confidence": "6", "reason": "trend intact"}\n```', "HOLD"),
    ("I would TIGHTEN_TO_BREAKEVEN here because...", "TIGHTEN_TO_BREAKEVEN"),
    ("no idea", None),
])
def test_answers_are_read_from_json_or_words(text, want):
    d = parse_decision(text)
    assert (d or {}).get("decision") == want


# ------------------------------------------------------------------ scoring
def candles(*rows):
    """(minute offset from T0, low, high, close)"""
    return [(T0 + m * 60, c, h, l, c) for m, l, h, c in rows]


def rec(decision, moment="RUN_ON", engine="HOLD", at=10, price=103.0, **kw):
    return {"pos_id": "p1", "symbol": "SAIL", "decision": decision, "moment": moment, "engine_did": engine,
            "moment_ts": T0 + at * 60, "price": price, "stop_in_force": 98.0, **kw}


CLOSE = {"type": "CLOSE", "pos_id": "p1", "ts": T0 + 60 * 60, "net": 100.0, "exit_price": 101.0, "reason": "RUN_ON_STOP"}


def test_exit_now_is_scored_at_the_moments_price():
    s = score_record(rec("EXIT_NOW", price=103.0), OPEN, CLOSE, [], COSTS)
    assert s["cf_net"] == pytest.approx(300.0) and s["delta"] == pytest.approx(200.0) and not s["agreed"]


def test_hold_where_the_engine_held_changes_nothing():
    s = score_record(rec("HOLD"), OPEN, CLOSE, [], COSTS)
    assert s["delta"] == 0 and s["agreed"]


def test_tighten_is_replayed_from_the_next_minute():
    c = candles((11, 100.5, 101.5, 101.0), (12, 99.5, 100.8, 100.0))     # minute 12 touches breakeven
    s = score_record(rec("TIGHTEN_TO_BREAKEVEN"), OPEN, CLOSE, c, COSTS)
    assert s["cf_net"] == pytest.approx(0.0) and s["delta"] == pytest.approx(-100.0)
    untouched = score_record(rec("TIGHTEN_TO_BREAKEVEN"), OPEN, CLOSE, candles((11, 100.5, 101.5, 101.0)), COSTS)
    assert untouched["delta"] == 0


def test_hold_against_a_fade_tighten_is_replayed_with_the_earlier_stop():
    be_close = dict(CLOSE, exit_price=100.0, net=0.0, reason="TRAIL_STOP")
    c = candles((20, 99.8, 100.5, 100.0), (30, 101.0, 103.0, 102.5))
    r = rec("HOLD", moment="FADE_TIGHTEN", engine="TIGHTEN_TO_BREAKEVEN", stop_before=98.0)
    s = score_record(r, OPEN, be_close, c, COSTS)
    # the earlier stop (98) is never touched, so the trade is held to 15:10: last close 102.5
    assert s["how"] == "held to the square-off" and not s["agreed"]
    assert s["cf_net"] == pytest.approx(250.0) and s["delta"] == pytest.approx(250.0)


def test_the_summary_and_report_count_agreement_and_who_was_right():
    rows = [score_record(rec("EXIT_NOW", price=103.0), OPEN, CLOSE, [], COSTS),
            score_record(rec("EXIT_NOW", price=99.0), OPEN, CLOSE, [], COSTS),
            score_record(rec("HOLD"), OPEN, CLOSE, [], COSTS)]
    for r in rows:
        r["day"] = "2026-10-02"
    s = summarize(rows)
    assert s["all"]["n"] == 3 and s["all"]["agreed"] == 1
    assert s["all"]["right"] == 1 and s["all"]["wrong"] == 1
    md = to_markdown(rows, T0)
    assert "Every disagreement" in md and "EXIT_NOW" in md
    assert "No scored answers yet" in to_markdown([], T0)


def test_the_shadow_package_cannot_place_orders():
    import pathlib
    import shadow
    src = "".join(p.read_text(encoding="utf-8") for p in pathlib.Path(shadow.__file__).parent.glob("*.py"))
    for forbidden in ("upstox_client", "place_order", "OrderApi", "paper.broker", "on_signal", "_close("):
        assert forbidden not in src
