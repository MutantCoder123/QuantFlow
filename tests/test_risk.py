from core.risk import size, RiskLimits, Portfolio
from core.types import Proposal

LIM = RiskLimits(capital=1_000_000, risk_per_trade_pct=0.5,
                 max_daily_loss_pct=2.0, max_cluster_risk_pct=1.0,
                 max_adv_participation=0.02)
P = Proposal(symbol="SAIL", bias="LONG", entry=100.0, stop=98.0,
             target=105.0, composite=0.34, regime="TREND_EXPANSION")


def test_quantity_risks_exactly_the_configured_fraction():
    sp = size(P, Portfolio(), LIM, adv_shares=10_000_000)
    assert sp.qty == 2500                       # 5000 risk / 2.0 per share
    assert sp.risk_amount == 5000.0


def test_liquidity_cap_binds_on_thin_names():
    sp = size(P, Portfolio(), LIM, adv_shares=50_000)
    assert sp.qty == 1000                       # 2% of 50,000


def test_cluster_limit_blocks_the_seventh_correlated_signal():
    book = Portfolio()
    book.add_open("ADANIENT", cluster="ADANI", risk_amount=9_500.0)
    p = Proposal(symbol="ADANIGREEN", bias="LONG", entry=100.0, stop=98.0,
                 target=105.0, composite=0.34, regime="TREND_EXPANSION")
    out = size(p, book, LIM, adv_shares=10_000_000)
    assert out.qty <= 250                       # only 500 of headroom remains


def test_daily_loss_limit_rejects_outright():
    book = Portfolio(); book.realized_loss_today = 20_000.0
    out = size(P, book, LIM, adv_shares=10_000_000)
    assert getattr(out, "reason", "") == "DAILY_LOSS_LIMIT"


def test_degenerate_stop_is_rejected():
    p = Proposal(symbol="X", bias="LONG", entry=100.0, stop=100.0, target=105.0,
                 composite=0.3, regime="TREND_EXPANSION")
    assert getattr(size(p, Portfolio(), LIM, 1e7), "reason", "") == "DEGENERATE_STOP"


# -- value caps (2026-09-29: Rs 64 lakh held at once on Rs 10 lakh capital) ---
NBCC = Proposal(symbol="NBCC", bias="LONG", entry=79.33, stop=79.08,
                target=81.6, composite=0.30, regime="MEAN_REVERSION")


def test_a_tight_stop_no_longer_buys_more_than_capital():
    """Risk alone wanted 20,000 NBCC (Rs 15.9 lakh); one position is capped at 1x capital."""
    sp = size(NBCC, Portfolio(), LIM, adv_shares=10_000_000)
    assert sp.qty == 12_605                               # int(10,00,000 / 79.33)
    assert sp.qty * NBCC.entry <= LIM.capital
    assert sp.risk_amount < 5000                          # the value cap bound, not the risk budget


def test_the_value_cap_leaves_ordinary_trades_alone():
    sp = size(P, Portfolio(), LIM, adv_shares=10_000_000)
    assert sp.qty == 2500                                 # Rs 2.5 lakh: well inside 1x


def test_open_positions_share_a_total_value_limit():
    book = Portfolio()
    for i in range(4):                                    # Rs 45 lakh already open
        book.add_open(f"S{i}", cluster=f"C{i}", risk_amount=1000, value=1_125_000)
    sp = size(NBCC, book, LIM, adv_shares=10_000_000)
    assert sp.qty == int(500_000 / 79.33)                 # only Rs 5 lakh of room left
    book.add_open("S5", cluster="C5", risk_amount=1000, value=500_000)
    assert getattr(size(NBCC, book, LIM, 1e7), "reason", "") == "OPEN_VALUE_LIMIT"


def test_the_caps_scale_with_their_settings():
    lim = RiskLimits(capital=1_000_000, max_position_value_x=2.0, max_open_value_x=5.0)
    assert size(NBCC, Portfolio(), lim, 1e7).qty == 20_000   # 2x lets the full risk size through


# -- slivers (2026-09-29: the last Rs 255 of open-value room bought 1 SAIL) ---
SAIL = Proposal(symbol="SAIL", bias="LONG", entry=184.02, stop=182.89,
                target=187.0, composite=0.30, regime="TREND_EXPANSION")


def test_the_last_sliver_of_open_value_room_is_not_traded():
    book = Portfolio()
    book.add_open("S0", cluster="C0", risk_amount=1000, value=4_999_745)
    assert getattr(size(SAIL, book, LIM, 1e7), "reason", "") == "SIZE_TOO_SMALL"


def test_the_last_sliver_of_a_cluster_budget_is_not_traded():
    book = Portfolio()
    book.add_open("MCX", cluster="FINANCIALS", risk_amount=9_988.0)
    p = Proposal(symbol="360ONE", bias="LONG", entry=1037.51, stop=1028.23,
                 target=1055.0, composite=0.30, regime="TREND_EXPANSION")
    assert getattr(size(p, book, LIM, 1e7), "reason", "") == "SIZE_TOO_SMALL"


def test_a_trade_at_the_floor_still_opens():
    """Rs 500 of risk is 10% of the Rs 5,000 budget: allowed."""
    book = Portfolio()
    book.add_open("ADANIENT", cluster="ADANI", risk_amount=9_500.0)
    p = Proposal(symbol="ADANIGREEN", bias="LONG", entry=100.0, stop=98.0,
                 target=105.0, composite=0.34, regime="TREND_EXPANSION")
    assert size(p, book, LIM, 1e7).risk_amount == 500.0


def test_the_floor_follows_its_setting():
    book = Portfolio()
    book.add_open("S0", cluster="C0", risk_amount=1000, value=4_999_745)
    lim = RiskLimits(capital=1_000_000, min_trade_risk_frac=0.0)
    assert size(SAIL, book, lim, 1e7).qty == 1
