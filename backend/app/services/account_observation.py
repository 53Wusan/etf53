"""Versioned, append-only observations of the deployed paper account."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal

from app.services.execution_research import FX, CapitalPolicy, ExecutionRule, load_arrays, simulate
from app.services.research_cockpit import (
    BENCHMARK_SYMBOLS,
    _active_data,
    _research_output,
    benchmark_frame,
)

VERSION = "rank2-account-observation-v1"
WEIGHTS = dict(zip(BENCHMARK_SYMBOLS, (.4, .3, .3), strict=True))


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _directory() -> Path:
    return _research_output() / "forward_data" / "account_observation" / VERSION


def _exclusive(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf8"))


def _publication(directory: Path, record: dict) -> dict | None:
    path = directory / "publications" / f"{record['payload']['data_date']}.json"
    if not path.exists():
        return None
    value = _read(path)
    content = {k: v for k, v in value.items() if k != "sha256"}
    if value["sha256"] != digest(content) or value["record_sha256"] != record["sha256"]:
        raise ValueError("发布回执与观察记录不一致")
    published = datetime.fromisoformat(value["published_at_utc"])
    if published < datetime.fromisoformat(record["recorded_at_utc"]):
        raise ValueError("发布回执时间早于计算记录")
    return {**value, "before_next_open": published < datetime.fromisoformat(record["next_open_utc"])}


def record_publication(day: str, now: datetime | None = None) -> dict:
    """Called only by the workflow after deploy-pages has succeeded."""
    directory = _directory()
    record = _read(directory / f"{day}.json")
    content = {k: v for k, v in record.items() if k != "sha256"}
    if record["sha256"] != digest(content) or record["payload"]["data_date"] != day:
        raise ValueError("待确认发布的记录哈希不符")
    existing = _publication(directory, record)
    if existing:
        return existing
    published = now or datetime.now(timezone.utc)
    if published < datetime.fromisoformat(record["recorded_at_utc"]):
        raise ValueError("发布回执时间早于计算记录")
    value = {"data_date": day, "record_sha256": record["sha256"],
             "published_at_utc": published.isoformat(),
             "github_run_url": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"}
    value["sha256"] = digest(value)
    _exclusive(directory / "publications" / f"{day}.json", value)
    return _publication(directory, record)


def benchmark_curve(dates, frames, initial: float, fee: float) -> list[dict]:
    """Buy each weight at first adjusted open, fees inside its budget; no rebalance."""
    values = np.zeros(len(dates))
    for symbol, weight in WEIGHTS.items():
        frame = frames[symbol].reindex(dates)
        opening, closing = frame.adjusted_open.to_numpy(float), frame.adjusted_close.to_numpy(float)
        if not np.isfinite(opening).all() or not np.isfinite(closing).all() or (opening <= 0).any() or (closing <= 0).any():
            raise ValueError(f"{symbol} 对照行情缺失或无效，不填补")
        quantity = initial * weight / (1 + fee) / opening[0]
        values += quantity * closing
    return [{"date": str(day.date()), "benchmark_usd": float(value)} for day, value in zip(dates, values, strict=True)]


def build_summary(config: dict, account: dict) -> dict:
    data = load_arrays()
    rule = ExecutionRule(**config["rule"])
    capital = CapitalPolicy(initial_usd=float(config["initial_usd"]), reserve_hkd=0)
    result = simulate(data, rule, start=account["activation_date"], end=account["data_date"], detail=True, capital=capital)
    last = result["daily"][-1]
    if (abs(last["equity_hkd"] / FX - account["equity_usd"]) > 1e-6
            or result["trades"] != account["trades"]):
        raise ValueError("观察回放与现网账户不一致")
    dates = pd.DatetimeIndex([row["date"] for row in result["daily"]])
    expected = mcal.get_calendar("NYSE").valid_days(start_date=dates[0], end_date=dates[-1]).tz_localize(None)
    if not dates.equals(expected):
        raise ValueError("账户曲线缺少交易日")
    benchmark = benchmark_curve(dates, {s: benchmark_frame(s) for s in WEIGHTS}, capital.initial_usd, rule.fee_rate)
    curve = [{**b, "equity_usd": d["equity_hkd"] / FX, "cash_usd": d["cash_hkd"] / FX}
             for d, b in zip(result["daily"], benchmark, strict=True)]
    by_symbol = []
    for symbol in data["symbols"]:
        trades = [t for t in account["trades"] if t["symbol"] == symbol]
        position = account["positions"].get(symbol, {})
        realized = sum(t["realized_pnl_hkd"] for t in trades) / FX
        floating = position.get("pnl_hkd", 0) / FX
        if trades or position:
            by_symbol.append({"symbol": symbol, "realized_usd": realized, "floating_usd": floating,
                              "total_usd": realized + floating,
                              "fees_usd": sum(t["fee_hkd"] for t in trades) / FX,
                              "buys": sum(t["side"] == "buy" for t in trades),
                              "sells": sum(t["side"] == "sell" for t in trades)})
    profit = account["equity_usd"] - capital.initial_usd
    if abs(sum(row["total_usd"] for row in by_symbol) - profit) > 1e-6:
        raise ValueError("逐品种盈亏与账户盈亏不平")
    values = np.array([p["benchmark_usd"] for p in curve])
    return {"version": VERSION, "data_date": account["data_date"], "activation_date": account["activation_date"],
            "initial_usd": capital.initial_usd, "rule": asdict(rule), "capital": asdict(capital),
            "symbols": list(data["symbols"]), "benchmark_weights": WEIGHTS,
            "benchmark_return_pct": (values[-1] / capital.initial_usd - 1) * 100,
            "benchmark_drawdown_pct": float((values / np.maximum.accumulate(np.maximum(values, capital.initial_usd)) - 1).min() * 100),
            "strategy_return_pct": result["metrics"]["return_pct"],
            "strategy_drawdown_pct": result["metrics"]["max_drawdown_pct"],
            "mean_exposure_pct": result["metrics"]["mean_exposure_pct"],
            "fees_usd": result["metrics"]["fees_hkd"] / FX,
            "curve": curve, "by_symbol": sorted(by_symbol, key=lambda row: row["total_usd"], reverse=True),
            "curve_kind": "historical_replay_not_proof_of_preopen_signals"}


def append_record(directory: Path, definition: dict, payload: dict, now: datetime) -> dict:
    """Idempotent same-day runs; fail on definition changes, tampering or history rewrites."""
    schedule = mcal.get_calendar("NYSE").schedule(start_date=payload["data_date"], end_date=pd.Timestamp(payload["data_date"]) + pd.Timedelta(days=14))
    if pd.Timestamp(payload["data_date"]) not in schedule.index or now < schedule.loc[payload["data_date"], "market_close"].to_pydatetime():
        raise ValueError("行情日尚未收盘，不可登记完整日线观察")
    following = schedule.loc[schedule.index > pd.Timestamp(payload["data_date"])]
    next_day = str(following.index[0].date())
    plan = payload["next_open_plan"]
    if plan["next_open_date"] != next_day or plan["signal_date"] != payload["data_date"]:
        raise ValueError("下一开盘计划与交易日不一致")
    deadline = following.iloc[0].market_open.to_pydatetime()
    directory.mkdir(parents=True, exist_ok=True)
    kickoff_path = directory / "kickoff.json"
    if not kickoff_path.exists():
        _exclusive(kickoff_path, {"definition": definition, "started_at_utc": now.isoformat(),
                                 "first_data_date": payload["data_date"]})
    kickoff = _read(kickoff_path)
    if kickoff["definition"] != definition:
        raise ValueError("观察版本的规则或代码已变化，须显式建立新版本")
    previous_hash = digest(kickoff)
    records = []
    for path in sorted(directory.glob("????-??-??.json")):
        record = _read(path)
        content = {k: v for k, v in record.items() if k != "sha256"}
        if record["sha256"] != digest(content) or record["previous_sha256"] != previous_hash or path.stem != record["payload"]["data_date"]:
            raise ValueError("每日观察哈希链不一致")
        records.append(record)
        previous_hash = record["sha256"]
    matching = next((r for r in records if r["payload"]["data_date"] == payload["data_date"]), None)
    if matching:
        if matching["payload"] != payload:
            raise ValueError("已登记的当日计划或账户数据变化，不覆盖")
    else:
        if records and payload["data_date"] < records[-1]["payload"]["data_date"]:
            raise ValueError("不允许事后插入旧日期观察")
        prior = records[-1] if records else None
        prior_plan = prior["payload"]["next_open_plan"] if prior else None
        prior_publication = _publication(directory, prior) if prior else None
        follows_plan = bool(prior_plan and prior_plan["next_open_date"] == payload["data_date"])
        linked = bool(follows_plan and prior["signal_before_next_open"]
                      and prior_publication and prior_publication["before_next_open"])
        match = None
        if follows_plan:
            def key(t):
                return t["symbol"], t["side"], t["reason"]
            match = sorted(map(key, prior_plan["actions"])) == sorted(map(key, payload["fills"]))
            if not match:
                raise ValueError("模拟成交与上次预先登记的计划不一致")
        record = {"recorded_at_utc": now.isoformat(), "previous_sha256": previous_hash,
                  "signal_before_next_open": now < deadline, "next_open_utc": deadline.isoformat(),
                  "fills_linked_to_prior_plan": linked, "fills_match_prior_plan": match,
                  "payload": payload}
        record["sha256"] = digest(record)
        _exclusive(directory / f"{payload['data_date']}.json", record)
        records.append(record)
    calendar = mcal.get_calendar("NYSE").valid_days(start_date=kickoff["first_data_date"], end_date=payload["data_date"])
    recorded_dates = {r["payload"]["data_date"] for r in records}
    publications = {r["payload"]["data_date"]: _publication(directory, r) for r in records}
    return {"started_at_utc": kickoff["started_at_utc"], "first_data_date": kickoff["first_data_date"],
            "recorded_sessions": len(records), "late_signals": sum(not r["signal_before_next_open"] for r in records),
            "confirmed_publications": sum(p is not None for p in publications.values()),
            "late_publications": sum(p is not None and not p["before_next_open"] for p in publications.values()),
            "missing_sessions": [str(d.date()) for d in calendar if str(d.date()) not in recorded_dates],
            "unverified_fill_sessions": sum(bool(r["payload"]["fills"]) and not r["fills_linked_to_prior_plan"] for r in records),
            "latest": records[-1], "records": [{"data_date": r["payload"]["data_date"],
                "recorded_at_utc": r["recorded_at_utc"], "signal_before_next_open": r["signal_before_next_open"],
                "fills_linked_to_prior_plan": r["fills_linked_to_prior_plan"],
                "fills_match_prior_plan": r["fills_match_prior_plan"], "fill_count": len(r["payload"]["fills"]),
                "publication": publications[r["payload"]["data_date"]],
                "next_open_plan": r["payload"]["next_open_plan"]} for r in records]}


def capture(config: dict, account: dict) -> dict:
    if account["profile"] != "rank2" or config.get("profile", "rank2") != "rank2":
        raise ValueError("该观察版本仅对应现网方案②")
    summary = build_summary(config, account)
    services = Path(__file__).parent
    names = ("execution_research.py", "shadow_account.py", "research_cockpit.py", "research_data_refresh.py",
             "execution_data_refresh.py", "account_observation.py")
    definition = {k: summary[k] for k in ("version", "activation_date", "initial_usd", "rule", "capital", "symbols", "benchmark_weights")}
    definition["code_sha256"] = {name: hashlib.sha256((services / name).read_bytes()).hexdigest() for name in names}
    active, _, _ = _active_data()
    payload = {"data_date": account["data_date"], "cash_usd": account["cash_usd"],
               "equity_usd": account["equity_usd"], "positions": account["positions"],
               "fills": [t for t in account["trades"] if t["date"] == account["data_date"]],
               "trades_sha256": digest({"trades": account["trades"]}),
               "next_open_plan": account["next_open_plan"],
               "benchmark_usd": summary["curve"][-1]["benchmark_usd"],
               "source_manifest_sha256": hashlib.sha256((active / "manifest.json").read_bytes()).hexdigest() if active else None}
    directory = _directory()
    summary["audit"] = append_record(directory, definition, payload, datetime.now(timezone.utc))
    summary["definition_sha256"] = digest(definition)
    summary["code_sha256"] = definition["code_sha256"]
    schedule = mcal.get_calendar("NYSE").schedule(start_date=account["next_open_plan"]["next_open_date"],
                                                 end_date=account["next_open_plan"]["next_open_date"])
    summary["valid_until"] = (schedule.iloc[0].market_close + pd.Timedelta(minutes=30)).isoformat()
    summary["run"] = {"status": "calculation_and_recording_ok", "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                      "github_run_url": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}" if os.environ.get("GITHUB_REPOSITORY") and os.environ.get("GITHUB_RUN_ID") else None}
    temporary = directory / "summary.tmp"
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")
    os.replace(temporary, directory / "summary.json")
    return summary


def status() -> dict:
    path = _directory() / "summary.json"
    return _read(path) if path.exists() else {"version": VERSION, "state": "not_started"}
