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
