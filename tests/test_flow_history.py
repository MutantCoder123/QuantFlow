"""F2 (2026-09-29): Upstox returns 30 daily FII/DII rows; the macro worker kept
only the first, undated (the socket showed date "N/A"). The Market tab's flow
bars need the days, so the rows are kept, dated by their own time_stamp."""
import json

from data_services import macro_worker
from data_services.macro_worker import InstitutionalFlowTracker, daily_nets, flow_history


class _Row:
    def __init__(self, **kw):
        self.kw = kw

    def to_dict(self):
        return dict(self.kw)


# time_stamp is IST midnight in ms: 28 Sep 2026 and 25 Sep 2026
MON, FRI = 1790533800000, 1790274600000


def test_daily_nets_are_dated_and_skip_bad_rows():
    rows = [_Row(time_stamp=MON, buy_amount=9047.56, sell_amount=14400.78),
            {"time_stamp": FRI, "buy_amount": 12327.36, "sell_amount": 16021.29},
            {"time_stamp": None, "buy_amount": 1, "sell_amount": 2},
            {"buy_amount": 1}]
    assert daily_nets(rows) == {"2026-09-28": -5353.22, "2026-09-25": -3693.93}
    assert daily_nets(None) == {}


def test_history_pairs_fii_and_dii_by_day_oldest_first():
    fii = [{"time_stamp": MON, "buy_amount": 1, "sell_amount": 3}, {"time_stamp": FRI, "buy_amount": 5, "sell_amount": 1}]
    dii = [{"time_stamp": MON, "buy_amount": 10, "sell_amount": 4}]
    assert flow_history(fii, dii) == [{"date": "2026-09-25", "fii_net": 4.0, "dii_net": None},
                                      {"date": "2026-09-28", "fii_net": -2.0, "dii_net": 6.0}]
    assert len(flow_history(fii, dii, keep=1)) == 1


def test_saving_history_keeps_what_the_file_had_and_dates_it(tmp_path, monkeypatch):
    f = tmp_path / "institutional_flow.json"
    f.write_text(json.dumps({"fii_net": -5353.22, "ad_ratio": 0.56, "ad_ratio_ts": "x"}))
    monkeypatch.setattr(macro_worker, "STATE_FILE", f)
    InstitutionalFlowTracker.save_history([{"date": "2026-09-28", "fii_net": -5353.22, "dii_net": 5189.02}])
    InstitutionalFlowTracker.save_ad_ratio(0.16, {"advances": 7, "declines": 43, "unchanged": 0})
    state = json.loads(f.read_text())
    assert state["date"] == "2026-09-28" and state["flow_history"][0]["dii_net"] == 5189.02
    assert state["fii_net"] == -5353.22 and state["ad_ratio"] == 0.16
    assert state["breadth"] == {"advances": 7, "declines": 43, "unchanged": 0}
    InstitutionalFlowTracker.save_history([])          # nothing to store: nothing written
    assert json.loads(f.read_text())["date"] == "2026-09-28"
