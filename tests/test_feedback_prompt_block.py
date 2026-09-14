"""The HISTORICAL CALIBRATION prompt block must actually reach the LLM.

Found by the final whole-branch review of Phase 5. The block is assembled
inside a bare ``except Exception: pass``, so a single missing key does not
raise -- it silently deletes the calibration evidence from the prompt and
leaves no trace. That is exactly what happened: Task 3.4 retired the 60m
checkpoint and ``get_feedback_payload`` stopped returning ``win_rate_60m``,
while the f-string kept subscripting ``feedback['win_rate_60m']``. The
KeyError was swallowed on every call, so the block had not been injected
since Phase 4 and nothing reported it.

These tests pin the contract in both directions: every key the block reads
must be one the payload actually produces, and the block must survive an
undefined profit factor (now ``None``, since Phase 5 removed the 999.0
sentinel) without interpolating the literal string "None".
"""
import re

import pytest


# Mirrors get_feedback_payload's real return shape (performance_analyzer.py).
_PAYLOAD_KEYS = {
    "total_signals", "legacy_excluded", "primary_horizon_min",
    "win_rate_primary", "win_rate_30m", "profit_factor",
    "best_regime", "best_regime_wr", "worst_regime", "worst_regime_wr",
    "regime_accuracy",
}


def _feedback_block_source() -> str:
    """The literal source of the calibration block, read from the file.

    Asserting against source is deliberate: the block lives deep inside an
    async LLM call that cannot be invoked in a test, and the defect being
    guarded is precisely that its failure is invisible at runtime.
    """
    import inspect
    import reasoning_engine
    src = inspect.getsource(reasoning_engine)
    start = src.index("HISTORICAL CALIBRATION")
    end = src.index("conviction_modifier accordingly", start)
    return src[start:end]


def test_block_only_reads_keys_the_payload_actually_returns():
    """A key the payload does not return raises inside a bare except and
    silently removes the whole block from the prompt."""
    block = _feedback_block_source()
    referenced = set(re.findall(r"feedback(?:\.get\(|\[)['\"](\w+)['\"]", block))
    assert referenced, "no feedback keys found -- did the block move?"

    unknown = referenced - _PAYLOAD_KEYS
    assert not unknown, (
        f"the calibration block reads {sorted(unknown)}, which "
        f"get_feedback_payload does not return. Inside its bare "
        f"`except Exception: pass` this raises and silently deletes the "
        f"block from the prompt -- the exact regression that hid "
        f"win_rate_60m for an entire phase.")


def test_block_does_not_subscript_feedback_directly():
    """`.get` degrades to None and still renders; `[...]` raises and erases
    the block. After this fix every read must be a .get."""
    block = _feedback_block_source()
    assert not re.search(r"feedback\[", block), (
        "direct subscripting reintroduces the silent-KeyError failure mode; "
        "use feedback.get(...)")


def test_profit_factor_none_is_rendered_as_unmeasured_not_the_word_none():
    """Phase 5 replaced the 999.0 sentinel with None. Interpolating that
    straight into the prompt would hand the LLM the literal string 'None'."""
    block = _feedback_block_source()
    assert "unmeasured" in block, (
        "an undefined profit factor must be described, not interpolated raw")


def test_payload_key_set_matches_the_real_producer():
    """Guards the fixture above: if get_feedback_payload's shape changes, this
    test's _PAYLOAD_KEYS must change with it, or the check above goes stale."""
    import inspect
    from performance_analyzer import PerformanceAnalyzer
    src = inspect.getsource(PerformanceAnalyzer.get_feedback_payload)
    body = src[src.index("payload = {"):]
    produced = set(re.findall(r"^\s*\"(\w+)\":", body, re.M))
    assert produced == _PAYLOAD_KEYS, (
        f"get_feedback_payload now returns {sorted(produced)}; update "
        f"_PAYLOAD_KEYS so the prompt-block check stays meaningful.")
