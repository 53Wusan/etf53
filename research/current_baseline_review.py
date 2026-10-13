"""Reproducible attribution of the unchanged deployed rule; no parameter search."""
import hashlib
import json
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
from app.services.account_observation import WEIGHTS, benchmark_curve
from app.services.execution_research import (
    FX,
    CapitalPolicy,
    ExecutionRule,
    load_arrays,
    simulate,
)
from app.services.research_cockpit import benchmark_frame, input_frame

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research/output/baseline_review_20261014"


def review(data, rule, capital, start, end):
    result = simulate(data, rule, start=start, end=end, detail=True, capital=capital)
    dates = pd.DatetimeIndex([d["date"] for d in result["daily"]])
    expected = mcal.get_calendar("NYSE").valid_days(start_date=dates[0], end_date=dates[-1]).tz_localize(None)
    assert dates.equals(expected), "共同历史缺少交易日"
    benchmark = benchmark_curve(dates, {s: benchmark_frame(s) for s in WEIGHTS}, capital.initial_usd, rule.fee_rate)
    curve = pd.DataFrame([{**b, "strategy_usd": d["equity_hkd"] / FX,
                           "cash_usd": d["cash_hkd"] / FX} for b, d in zip(benchmark, result["daily"], strict=True)])
    b = curve.benchmark_usd.to_numpy()
    rows, entries, exits, queues = [], [], [], defaultdict(deque)
    for i, trade in enumerate(result["trades"]):
        symbol = trade["symbol"]
        t = data["dates"].get_loc(pd.Timestamp(trade["date"]))
        j = data["symbols"].index(symbol)
        item = {"id": i, "date": trade["date"], "signal_date": trade["signal_date"], "symbol": symbol,
                "side": trade["side"], "reason": trade["reason"], "price": trade["price"],
                "quantity": trade["quantity"], "fee_usd": trade["fee_hkd"] / FX,
                "realized_usd": trade["realized_pnl_hkd"] / FX}
        rows.append(item)
        if trade["side"] == "buy":
            item = dict(item)
            item["entry_type"] = "add" if trade["reason"] == "恐惧区内再跌后加仓" else "first"
            for horizon in (5, 20, 60):
                # N sessions counting execution day as session 1; no end-of-sample truncation.
                last = t + horizon - 1
                if last >= len(data["dates"]) or data["dates"][last] > dates[-1]:
                    item[f"net_return_{horizon}"] = None
                    item[f"worst_close_{horizon}"] = None
                else:
                    path = data["close"][t:last + 1, j]
                    item[f"net_return_{horizon}"] = float((path[-1] / trade["price"] * (1-rule.fee_rate) / (1+rule.fee_rate) - 1) * 100)
                    item[f"worst_close_{horizon}"] = float((path.min() / trade["price"] - 1) * 100)
            entries.append(item)
            queues[symbol].append(item)
        else:
            opening = queues[symbol].popleft()
            item = {**item, "buy_date": opening["date"], "holding_days": (pd.Timestamp(trade["date"])-pd.Timestamp(opening["date"])).days,
                    "net_return_pct": trade["realized_pnl_hkd"] / FX / (capital.initial_usd * capital.lot_fraction) * 100}
            for horizon in (5, 20):
                last = t + horizon - 1
                item[f"price_change_after_{horizon}"] = None if last >= len(data["dates"]) or data["dates"][last] > dates[-1] else float((data["close"][last,j]/trade["price"]-1)*100)
            exits.append(item)
    by_symbol = []
    final = result["daily"][-1]["positions"]
    for symbol in data["symbols"]:
        trades = [t for t in rows if t["symbol"] == symbol]
        realized = sum(t["realized_usd"] for t in trades)
        floating = final.get(symbol, {}).get("pnl_hkd", 0) / FX
        by_symbol.append({"symbol": symbol, "realized_usd": realized, "floating_usd": floating,
                          "total_usd": realized+floating, "fees_usd": sum(t["fee_usd"] for t in trades),
                          "buys": sum(t["side"] == "buy" for t in trades), "sells": sum(t["side"] == "sell" for t in trades)})
    pnl = result["metrics"]["ending_equity_hkd"] / FX - capital.initial_usd
    assert abs(sum(r["total_usd"] for r in by_symbol) - pnl) < 1e-6
    for day in result["daily"]:
        assert abs(day["cash_hkd"] + sum(p["market_value_hkd"] for p in day["positions"].values()) - day["equity_hkd"]) < 1e-6
    stats = {"start": result["start"], "end": result["end"], **result["metrics"],
             "benchmark_return_pct": float((b[-1] / capital.initial_usd - 1) * 100),
             "benchmark_drawdown_pct": float((b / np.maximum.accumulate(np.maximum(b, capital.initial_usd))-1).min()*100),
             "by_symbol": sorted(by_symbol, key=lambda r:r["total_usd"], reverse=True)}
    return stats, curve, pd.DataFrame(rows), pd.DataFrame(entries), pd.DataFrame(exits)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / "research/configs/baseline_review_20261014.json").read_text(encoding="utf8"))
    rule = ExecutionRule(**config["rule"])
    capital = CapitalPolicy(initial_usd=config["initial_usd"], reserve_hkd=0)
    data = load_arrays()
    assert list(data["symbols"]) == config["symbols"], "现网品种与研究冻结版本不同"
    selected = data["dates"] <= pd.Timestamp(config["data_cutoff"])
    data = {k: v[selected] if k != "symbols" else v for k, v in data.items()}
    assert str(data["dates"][-1].date()) == config["data_cutoff"], "冻结截止日缺失"
    # GDXU only has a complete score from 2021-12-02; never invent earlier inputs.
    scenarios = {"continuous_from_20211203": ("2021-12-03", str(data["dates"][-1].date())),
                 "bear_with_dec2021_positions": ("2021-12-03", "2022-12-30"),
                 "2022_cash_start": ("2022-01-03", "2022-12-30"),
                 "2023_cash_start": ("2023-01-03", "2023-12-29"),
                 "recent_continuous": ("2024-04-25", str(data["dates"][-1].date())),
                 "2026_cash_start": ("2026-01-02", str(data["dates"][-1].date()))}
    summaries = {}
    for name, (start,end) in scenarios.items():
        stats, curve, trades, entries, exits = review(data, rule, capital, start, end)
        summaries[name] = stats
        for kind, frame in (("curve",curve),("trades",trades),("entries",entries),("exits",exits)):
            frame.to_csv(OUT / f"{name}_{kind}.csv", index=False)
    provenance = {"rule": asdict(rule), "capital": asdict(capital), "symbols": list(data["symbols"]),
                  "benchmark": WEIGHTS, "cutoff": str(data["dates"][-1].date()),
                  "input_sha256": {s: hashlib.sha256(input_frame(s).loc[:config["data_cutoff"]].to_csv().encode()).hexdigest() for s in data["symbols"]},
                  "benchmark_sha256": {s: hashlib.sha256(benchmark_frame(s).loc[:config["data_cutoff"]].to_csv().encode()).hexdigest() for s in WEIGHTS},
                  "code_sha256": hashlib.sha256((ROOT / "backend/app/services/execution_research.py").read_bytes()).hexdigest(),
                  "limitations": ["此前反复研究过的历史，仅作开发与诊断，不是独立样本外", "复权小数份额，次日开盘，未计滑点、最低收费、税或现金利息", "未匹配两组波动风险，不把收益差解释为择时的因果优势", "事件窗口重叠；前瞻收益仅作描述，不计算独立样本显著性", "所有情景单独现金起步；continuous与bear_with_prior_positions内部连续持仓，不逐年重置", "尾部不足完整窗口记空值，不按零计算", "未来路径只作事后诊断，不用于交易决策"]}
    (OUT / "summary.json").write_text(json.dumps({"provenance":provenance,"scenarios":summaries},ensure_ascii=False,indent=2,allow_nan=False),encoding="utf8")
    print(json.dumps({k:{x:v[x] for x in ("return_pct","max_drawdown_pct","benchmark_return_pct","benchmark_drawdown_pct","mean_exposure_pct")} for k,v in summaries.items()},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
