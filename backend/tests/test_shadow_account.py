import json
from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest

from app.services.execution_research import CapitalPolicy, ExecutionRule, simulate
from app.services.shadow_account import calculate, update


def test_deployment_account_starts_empty_and_does_not_book_future_plan():
    dates = pd.bdate_range("2026-09-28", "2026-10-02")
    data = {"dates": dates, "symbols": ("TQQQ",), "open": np.full((5, 1), 100.),
            "close": np.full((5, 1), 100.), "score": np.full((5, 1), -10.)}
    config = {"activation_date": "2026-10-05", "initial_usd": 60000,
              "rule": asdict(ExecutionRule(buy_score=0))}
    result = calculate(config, data)
    assert result["trades"] == []
    assert result["positions"] == {}
    assert result["cash_usd"] == 60000
    plan = result["next_open_plan"]
    assert plan["next_open_date"] == "2026-10-05"
    assert plan["actions"][0]["gross_hkd"] / 7.8 == pytest.approx(3000 / 1.001)


def test_new_capital_scales_account_without_importing_old_reserve():
    data = {"dates": pd.bdate_range("2026-10-01", periods=3), "symbols": ("TQQQ",),
            "open": np.full((3, 1), 100.), "close": np.full((3, 1), 100.),
            "score": np.full((3, 1), -10.)}
    result = simulate(data, ExecutionRule(buy_score=0), start="2026-10-02", detail=True,
                      capital=CapitalPolicy(initial_usd=60000, reserve_hkd=0))
    assert result["daily"][0]["cash_hkd"] / 7.8 == pytest.approx(57000)


@pytest.mark.parametrize("change", [
    {"equity_at_open_hkd": 350000.0000000001},
    {"equity_at_open_hkd": 350000.01},
    {"price": 101.0},
])
def test_update_preserves_recorded_trade_and_accepts_only_valuation_roundoff(tmp_path, monkeypatch, change):
    old_trade = {"date": "2026-11-25", "price": 100.0, "equity_at_open_hkd": 350000.0}
    new_trade = {**old_trade, "date": "2026-11-27"}
    account = {"activation_date": "2026-10-06", "data_date": "2026-11-25", "trades": [old_trade]}
    replay = {**account, "data_date": "2026-11-27", "trades": [{**old_trade, **change}, new_trade]}
    config, ledger = tmp_path / "config.json", tmp_path / "shadow.json"
    config.write_text("{}")
    ledger.write_text(json.dumps(account))
    monkeypatch.setattr("app.services.shadow_account.load_arrays", lambda: {})
    monkeypatch.setattr("app.services.shadow_account.calculate", lambda *_: replay)

    if change == {"equity_at_open_hkd": 350000.0000000001}:
        assert update(config, ledger)["trades"] == [old_trade, new_trade]
        assert json.loads(ledger.read_text())["trades"] == [old_trade, new_trade]
    else:
        with pytest.raises(ValueError, match="先对账"):
            update(config, ledger)
        assert json.loads(ledger.read_text()) == account
