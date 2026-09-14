"""The display path and the decision path must score the SAME object.

Found by the final whole-branch review of Phase 5, and invisible to every
task-scoped review before it because the question spans two tasks.

`build_structured_payload` keys two process-wide registries -- the
ConvictionScorer and the RegimeManager -- per symbol. Its two callers hand it
different spellings of the same stock:

  * the decision loop (reasoning_engine.analysis_loop) passes
    `payload['symbol']`, falling back to the instrument key "NSE_EQ|SAIL";
  * the 2 Hz display refresh (api_server's websocket handler) passes the bare
    "SAIL".

Keying the registries by the raw string gave each surface its own scorer and
its own regime manager. Only the decision path ever calls with
`advance_state=True`, so the display copy's regime never advanced past the
constructor default -- and every number shipped to the operator (composite,
directional bias, expectancy, Task 5.2's provenance decomposition, Task 5.1's
attention rank) was computed under weights the gatekeeper never used.

Both call sites must therefore resolve to one registry entry per stock.
"""
import pytest

from reasoning_engine import ReasoningEngine
from conviction_scorer import ConvictionScorerRegistry
from regime_manager import RegimeManagerRegistry


# Every spelling of one stock that reaches build_structured_payload in
# production. They must all collapse to the same registry key.
_SPELLINGS = ["NSE_EQ|SAIL", "SAIL", "NSE_EQ|SAIL-EQ", "SAIL-EQ"]


@pytest.fixture(autouse=True)
def _clean_registries():
    scorers = dict(ConvictionScorerRegistry._scorers)
    managers = dict(RegimeManagerRegistry._managers)
    ConvictionScorerRegistry._scorers.clear()
    RegimeManagerRegistry._managers.clear()
    yield
    ConvictionScorerRegistry._scorers.clear()
    ConvictionScorerRegistry._scorers.update(scorers)
    RegimeManagerRegistry._managers.clear()
    RegimeManagerRegistry._managers.update(managers)


def test_every_spelling_of_a_symbol_maps_to_one_registry_key():
    keys = {ReasoningEngine._normalize_symbol(s) for s in _SPELLINGS}
    assert keys == {"SAIL"}, (
        "build_structured_payload keys its registries by _normalize_symbol; if "
        "two spellings diverge here, the decision path and the display path get "
        "separate ConvictionScorer/RegimeManager objects again.")


def test_both_call_sites_share_one_scorer_and_one_regime_manager():
    """The decision loop's key and the websocket handler's key must fetch the
    identical objects -- not merely equal ones."""
    decision_key = ReasoningEngine._normalize_symbol("NSE_EQ|SAIL")
    display_key = ReasoningEngine._normalize_symbol("SAIL")

    assert ConvictionScorerRegistry.get_or_create(decision_key) is \
        ConvictionScorerRegistry.get_or_create(display_key)
    assert RegimeManagerRegistry.get_or_create(decision_key) is \
        RegimeManagerRegistry.get_or_create(display_key)

    # One stock, one entry -- not two.
    assert len(ConvictionScorerRegistry._scorers) == 1
    assert len(RegimeManagerRegistry._managers) == 1


def test_advancing_state_via_one_spelling_is_visible_through_the_other():
    """The regression that actually hurt: the display path read a regime that
    never advanced, because it was reading a different object."""
    scorer = ConvictionScorerRegistry.get_or_create(
        ReasoningEngine._normalize_symbol("NSE_EQ|SAIL"))
    scorer.previous_bias = "LONG"
    scorer.polarity_flips_today = 2

    seen_by_display = ConvictionScorerRegistry.get_or_create(
        ReasoningEngine._normalize_symbol("SAIL"))
    assert seen_by_display.previous_bias == "LONG"
    assert seen_by_display.polarity_flips_today == 2


def test_rolling_state_engine_resolves_a_bare_symbol_for_the_payload():
    """`payload['symbol']` is what the decision loop keys off. The old lookup
    used a lowercase 'symbol' field against a map keyed by numeric CSV token,
    so it missed on both counts and returned '' for every stock -- which is
    what forced the decision path onto the instrument key in the first place.
    """
    from rolling_state_engine import RollingStateEngine

    eng = RollingStateEngine.__new__(RollingStateEngine)
    eng.watchlist = {"2963": {"Token": "2963", "Symbol": "SAIL-EQ", "Exchange": "NSE"}}

    # Keyed by the numeric CSV token, as upstox_feed builds it.
    assert eng._resolve_symbol("2963") == "SAIL"
    # And a websocket instrument key, which is what actually flows through
    # process_tick -- must still degrade to the bare name, never to "".
    assert eng._resolve_symbol("NSE_EQ|SAIL") == "SAIL"
    assert eng._resolve_symbol("NSE_EQ|SAIL-EQ") == "SAIL"
