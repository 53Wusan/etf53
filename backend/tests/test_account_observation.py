import json
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.services.account_observation import (
    WEIGHTS,
    append_record,
    benchmark_curve,
    build_summary,
    record_publication,
)
from app.services.execution_research import ExecutionRule
from app.services.shadow_account import calculate


def payload(day="2026-10-12", next_day="2026-10-13", actions=None, fills=None):
    return {"data_date": day, "equity_usd": 100, "fills": fills or [],
            "next_open_plan": {"signal_date": day, "next_open_date": next_day, "actions": actions or []}}


def test_benchmark_budgets_include_fees_and_do_not_rebalance():
    dates = pd.to_datetime(["2026-10-12", "2026-10-13"])
    frames = {s: pd.DataFrame({"adjusted_open": [100., 100.], "adjusted_close": [100., 200.]}, index=dates) for s in WEIGHTS}
    result = benchmark_curve(dates, frames, 1000, .001)
    assert result[0]["benchmark_usd"] == pytest.approx(1000 / 1.001)
    assert result[1]["benchmark_usd"] == pytest.approx(2000 / 1.001)
    frames["SOXL"].loc[dates[1], "adjusted_close"] = float("nan")
    with pytest.raises(ValueError, match="不填补"):
        benchmark_curve(dates, frames, 1000, .001)


def test_same_day_retry_cannot_retime_a_late_plan(tmp_path):
    late = datetime(2026, 10, 13, 14, tzinfo=timezone.utc)
    first = append_record(tmp_path, {"code": "v1"}, payload(), late)
    retry = append_record(tmp_path, {"code": "v1"}, payload(), datetime(2026, 10, 14, tzinfo=timezone.utc))
    assert first["latest"] == retry["latest"]
    assert retry["recorded_sessions"] == 1
    assert retry["late_signals"] == 1


def test_changed_definition_or_same_day_data_is_rejected(tmp_path):
    append_record(tmp_path, {"code": "v1"}, payload(), datetime(2026, 10, 13, 10, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="规则或代码"):
        append_record(tmp_path, {"code": "v2"}, payload(), datetime.now(timezone.utc))
    changed = payload()
    changed["equity_usd"] = 101
    with pytest.raises(ValueError, match="不覆盖"):
        append_record(tmp_path, {"code": "v1"}, changed, datetime.now(timezone.utc))


def test_tampering_with_latest_record_is_detected(tmp_path):
    append_record(tmp_path, {}, payload(), datetime(2026, 10, 13, 10, tzinfo=timezone.utc))
    path = tmp_path / "2026-10-12.json"
    record = json.loads(path.read_text())
    record["payload"]["equity_usd"] = 101
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="哈希链"):
        append_record(tmp_path, {}, payload(), datetime.now(timezone.utc))


def test_fills_must_match_timely_prior_plan(tmp_path, monkeypatch):
    action = {"symbol": "TQQQ", "side": "buy", "reason": "恐惧"}
    append_record(tmp_path, {}, payload(actions=[action]), datetime(2026, 10, 13, 10, tzinfo=timezone.utc))
    monkeypatch.setattr("app.services.account_observation._directory", lambda: tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "test/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    record_publication("2026-10-12", datetime(2026, 10, 13, 10, 1, tzinfo=timezone.utc))
    bad = payload("2026-10-13", "2026-10-14")
    with pytest.raises(ValueError, match="成交与"):
        append_record(tmp_path, {}, bad, datetime(2026, 10, 14, 10, tzinfo=timezone.utc))
    good = deepcopy(bad)
    good["fills"] = [action]
    result = append_record(tmp_path, {}, good, datetime(2026, 10, 14, 10, tzinfo=timezone.utc))
    assert result["latest"]["fills_match_prior_plan"] is True
    assert result["unverified_fill_sessions"] == 0


def test_missing_sessions_and_unlinked_fills_are_visible(tmp_path):
    now = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)
    append_record(tmp_path, {}, payload(), now)
    result = append_record(tmp_path, {}, payload("2026-10-14", "2026-10-15", fills=[{"symbol": "FAS"}]), datetime(2026, 10, 15, 10, tzinfo=timezone.utc))
    assert result["missing_sessions"] == ["2026-10-13"]
    assert result["unverified_fill_sessions"] == 1
    with pytest.raises(ValueError, match="事后插入"):
        append_record(tmp_path, {}, payload("2026-10-13", "2026-10-14"), datetime(2026, 10, 15, 10, tzinfo=timezone.utc))


def test_incorrect_next_trading_day_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="交易日不一致"):
        append_record(tmp_path, {}, payload(next_day="2026-10-14"), datetime.now(timezone.utc))


def test_unclosed_day_cannot_be_registered(tmp_path):
    with pytest.raises(ValueError, match="尚未收盘"):
        append_record(tmp_path, {}, payload(), datetime(2026, 10, 12, 15, tzinfo=timezone.utc))


def test_timely_calculation_with_late_publication_is_not_forward_verified(tmp_path, monkeypatch):
    action = {"symbol": "TQQQ", "side": "buy", "reason": "恐惧"}
    append_record(tmp_path, {}, payload(actions=[action]), datetime(2026, 10, 13, 10, tzinfo=timezone.utc))
    monkeypatch.setattr("app.services.account_observation._directory", lambda: tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "test/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    original = record_publication("2026-10-12", datetime(2026, 10, 13, 14, tzinfo=timezone.utc))
    retry = record_publication("2026-10-12", datetime(2026, 10, 14, 10, tzinfo=timezone.utc))
    assert retry == original
    result = append_record(tmp_path, {}, payload("2026-10-13", "2026-10-14", fills=[action]), datetime(2026, 10, 14, 10, tzinfo=timezone.utc))
    assert result["late_signals"] == 0
    assert result["late_publications"] == 1
    assert result["unverified_fill_sessions"] == 1
    assert result["latest"]["fills_match_prior_plan"] is True
    assert result["latest"]["fills_linked_to_prior_plan"] is False


def test_account_replay_and_symbol_contributions_reconcile(monkeypatch):
    dates = pd.bdate_range("2026-10-01", "2026-10-12")
    data = {"dates": dates, "symbols": ("TQQQ",), "open": np.full((len(dates), 1), 100.),
            "close": np.full((len(dates), 1), 100.), "score": np.zeros((len(dates), 1))}
    config = {"activation_date": "2026-10-05", "initial_usd": 45000,
              "rule": asdict(ExecutionRule(buy_score=0))}
    account = calculate(config, data)
    monkeypatch.setattr("app.services.account_observation.load_arrays", lambda: data)
    monkeypatch.setattr("app.services.account_observation.benchmark_frame",
                        lambda symbol: pd.DataFrame({"adjusted_open": 100., "adjusted_close": 100.}, index=dates))
    result = build_summary(config, account)
    assert sum(p["total_usd"] for p in result["by_symbol"]) == pytest.approx(account["equity_usd"]-45000)
    assert result["curve"][-1]["benchmark_usd"] == pytest.approx(45000/1.001)
    account["equity_usd"] += 1
    with pytest.raises(ValueError, match="回放与现网账户"):
        build_summary(config, account)
