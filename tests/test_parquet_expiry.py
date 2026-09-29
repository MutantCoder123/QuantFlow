"""The option chain wants the expiry as YYYY-MM-DD, not the datetime the
contracts API hands back (that went out as an ISO timestamp: 400)."""
from datetime import datetime

from data_services.parquet_engine import _expiry_str


def test_expiry_is_sent_as_a_plain_date():
    assert _expiry_str(datetime(2026, 9, 29)) == "2026-09-29"
    assert _expiry_str("2026-10-27T00:00:00") == "2026-10-27"
    assert _expiry_str("2026-10-27") == "2026-10-27"
