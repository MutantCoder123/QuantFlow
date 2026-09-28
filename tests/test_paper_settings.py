"""Paper account settings: risk.yaml defaults, UI overrides, never written back.

The operator edits capital, slippage and risk limits from the dashboard.
Defaults come from risk.yaml; overrides live in a machine-owned file; the
live risk config is never touched. A bad value is refused with a reason and
the previous setting kept.
"""
import pytest
import yaml

from paper.settings import EDITABLE, PaperSettings


def _files(tmp_path, overrides=None):
    risk = tmp_path / "risk.yaml"
    risk.write_text(yaml.safe_dump({
        "capital": 1_000_000, "risk_per_trade_pct": 0.5, "max_daily_loss_pct": 2.0,
        "max_cluster_risk_pct": 1.0, "max_adv_participation": 0.02}))
    paper = tmp_path / "paper.yaml"
    paper.write_text(yaml.safe_dump({
        "enabled": True, "autonomous": True, "judge_model": "gemini-2.5-flash",
        "fill": {"slippage_pct": 0.03}, "stale_price_seconds": 15,
        "metrics_min_n": {"rates": 20}}))
    ov = tmp_path / "paper_overrides.yaml"
    if overrides is not None:
        ov.write_text(yaml.safe_dump(overrides))
    return PaperSettings(risk_path=risk, paper_path=paper, overrides_path=ov), risk, ov


def test_defaults_come_from_risk_yaml_and_paper_yaml(tmp_path):
    s, _, _ = _files(tmp_path)
    eff = s.effective()
    assert eff["capital"] == 1_000_000
    assert eff["risk_per_trade_pct"] == 0.5
    assert eff["slippage_pct"] == 0.03
    assert s.sources()["capital"] == "risk.yaml"
    assert s.sources()["slippage_pct"] == "paper.yaml"


def test_an_override_wins_and_is_labelled(tmp_path):
    s, _, _ = _files(tmp_path, overrides={"capital": 500_000})
    assert s.effective()["capital"] == 500_000
    assert s.sources()["capital"] == "override"


def test_update_writes_overrides_only_never_risk_yaml(tmp_path):
    s, risk, ov = _files(tmp_path)
    before = risk.read_text()
    ok, errors = s.update({"capital": 750_000, "slippage_pct": 0.05})
    assert ok and errors == {}
    assert risk.read_text() == before
    assert yaml.safe_load(ov.read_text()) == {"capital": 750_000, "slippage_pct": 0.05}
    assert s.effective()["capital"] == 750_000


@pytest.mark.parametrize("field, value", [
    ("capital", 0), ("capital", -5), ("capital", "lots"),
    ("slippage_pct", -0.01), ("slippage_pct", 1.5),
    ("risk_per_trade_pct", 0), ("risk_per_trade_pct", 11),
    ("max_daily_loss_pct", 0), ("max_cluster_risk_pct", 101),
])
def test_invalid_values_are_refused_with_a_reason_and_nothing_changes(tmp_path, field, value):
    s, _, ov = _files(tmp_path)
    ok, errors = s.update({field: value})
    assert not ok and field in errors and errors[field]
    assert not ov.exists()
    assert s.effective()[field] == _files(tmp_path)[0].effective()[field]


def test_one_bad_field_rejects_the_whole_update(tmp_path):
    s, _, ov = _files(tmp_path)
    ok, errors = s.update({"capital": 900_000, "slippage_pct": 9})
    assert not ok and set(errors) == {"slippage_pct"}
    assert not ov.exists()


def test_unknown_fields_are_refused(tmp_path):
    s, _, _ = _files(tmp_path)
    ok, errors = s.update({"judge_model": "x"})
    assert not ok and "judge_model" in errors
    assert "judge_model" not in EDITABLE


def test_reset_to_default_removes_the_override(tmp_path):
    s, _, ov = _files(tmp_path, overrides={"capital": 500_000, "slippage_pct": 0.05})
    ok, _ = s.update({"capital": None})
    assert ok
    assert s.effective()["capital"] == 1_000_000
    assert yaml.safe_load(ov.read_text()) == {"slippage_pct": 0.05}


def test_risk_limits_object_uses_effective_values(tmp_path):
    s, _, _ = _files(tmp_path, overrides={"capital": 200_000, "risk_per_trade_pct": 1.0})
    lim = s.risk_limits()
    assert lim.capital == 200_000 and lim.risk_per_trade_pct == 1.0
    assert lim.max_adv_participation == 0.02
