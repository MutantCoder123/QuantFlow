"""The one-command launcher (run.py) and the local default model."""
import datetime as dt
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run  # noqa: E402

IST = run.IST


@pytest.fixture(autouse=True)
def _quiet_logs(tmp_path, monkeypatch):
    """say() appends to logs/run/launcher.log: keep tests out of the real one."""
    monkeypatch.setattr(run, "LOG_DIR", tmp_path / "logs")


def at(h, m, day=29):                    # 2026-09-29 is a Tuesday
    return dt.datetime(2026, 9, day, h, m, tzinfo=IST)


def test_market_hours_decide_whether_the_services_wait_for_the_backfill():
    assert run.market_hours(at(9, 20)) and run.market_hours(at(15, 44))
    assert not run.market_hours(at(8, 59)) and not run.market_hours(at(15, 45))
    assert not run.market_hours(at(10, 0, day=27))          # Sunday


def test_only_an_after_close_backfill_counts_as_storing_the_day(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "EOD_STAMP", tmp_path / ".last_eod_backfill")
    monkeypatch.setattr(run, "run_step", lambda *a, **k: True)
    monkeypatch.setattr(run, "now_ist", lambda: at(9, 20))
    assert run.backfill(full=True) and not run.eod_done_today()     # mid-session: the day is not in yet
    monkeypatch.setattr(run, "now_ist", lambda: at(15, 50))
    assert run.backfill(full=True) and run.eod_done_today()
    monkeypatch.setattr(run, "now_ist", lambda: at(9, 20, day=30))
    assert not run.eod_done_today()


def test_a_failed_backfill_is_not_stamped(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "EOD_STAMP", tmp_path / ".last_eod_backfill")
    monkeypatch.setattr(run, "run_step", lambda *a, **k: False)
    monkeypatch.setattr(run, "now_ist", lambda: at(16, 0))
    assert not run.backfill(full=True) and not run.eod_done_today()


def test_the_local_qwen_is_the_default_model_and_the_judge():
    import llm
    assert llm.DEFAULT_MODEL == "ollama:qwen2.5:7b"
    paper = yaml.safe_load((ROOT / "trading_copilot" / "config" / "paper.yaml").read_text(encoding="utf-8"))
    assert paper["judge_model"] == "ollama:qwen2.5:7b"
    from paper import runtime
    assert runtime.judge_model.__defaults__ == ("ollama:qwen2.5:7b",)


def test_the_web_ui_starts_after_the_feed():
    order = [name for name, *_ in run.SERVICES]
    web = next(s for s in run.SERVICES if s[0] == "web")
    assert web[3] == 8001 and order.index("feed") < order.index("web")


@pytest.mark.parametrize("clock,full_now", [(at(22, 53), True), (at(11, 0), False)])
def test_the_services_never_wait_for_the_options_history(monkeypatch, clock, full_now):
    """2026-09-29 22:53: a 429 in the options backfill held the whole start
    for a 30-minute cooldown. Only the quick price catch-up runs first."""
    order = []
    monkeypatch.setattr(run, "now_ist", lambda: clock)
    monkeypatch.setattr(run, "checks", lambda: True)
    monkeypatch.setattr(run, "check_ports", lambda: True)
    monkeypatch.setattr(run, "backfill", lambda full: order.append(("backfill", full)) or True)
    monkeypatch.setattr(run, "start_services", lambda verbose: order.append(("services",)) or [])
    monkeypatch.setattr(run, "supervise", lambda s, h, full_now=False: order.append(("supervise", full_now)))
    monkeypatch.setattr(sys, "argv", ["run.py"])
    run.main()
    assert order == [("backfill", False), ("services",), ("supervise", full_now)]
