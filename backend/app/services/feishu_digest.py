"""Signed daily Feishu digest. Credentials never enter payloads, receipts or errors."""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx


class DeliveryError(RuntimeError):
    pass


def signature(secret: str, timestamp: str) -> str:
    return base64.b64encode(hmac.new(f"{timestamp}\n{secret}".encode(), digestmod=hashlib.sha256).digest()).decode()


def send(url: str, secret: str, text: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "open.feishu.cn" or not parsed.path.startswith("/open-apis/bot/v2/hook/") or parsed.query or parsed.fragment:
        raise DeliveryError("机器人地址格式不符合要求")
    if not secret:
        raise DeliveryError("未配置签名密钥，不发送未签名消息")
    timestamp = str(int(time.time()))
    body = {"msg_type": "text", "content": {"text": text}, "timestamp": timestamp, "sign": signature(secret, timestamp)}
    try:
        # No redirects: credentials must only be transmitted to the configured host.
        response = httpx.post(url, json=body, timeout=20, follow_redirects=False)
        response.raise_for_status()
        data = response.json()
        if data.get("code", data.get("StatusCode", -1)) != 0:
            raise DeliveryError("飞书拒绝消息，请核对关键词、签名或机器人配置")
    except DeliveryError:
        raise
    except Exception:
        # httpx exceptions can include the complete credential-bearing URL.
        raise DeliveryError("飞书发送未确认成功；未自动重试，以免产生重复消息") from None


def daily_text(catalog: dict, account: dict, brief: dict, now: datetime, site: str) -> str:
    day = catalog["cutoff"]
    valid = datetime.fromisoformat(catalog["valid_until"].replace("Z", "+00:00"))
    plan = account.get("next_open_plan")
    if (catalog.get("stale") or now > valid or account.get("data_date") != day
            or not plan or plan.get("signal_date") != day or plan.get("next_open_date", "") <= day):
        raise DeliveryError("行情、账户或操作计划过期/日期不齐，不推送买卖意向")
    lines = [f"53 · 杠杆ETF晨间短讯｜美东 {day} 收盘", f"下一开盘：{plan['next_open_date']}（美东）"]
    try:
        brief_valid = datetime.fromisoformat(brief["valid_until"].replace("Z", "+00:00"))
        usable = brief.get("market_ready") and brief.get("market_date") == day and now <= brief_valid
    except (KeyError, ValueError, TypeError):
        usable = False
    if usable:
        a = brief["assessment"]
        lines += [f"市场：{a['trend']} / {a['short_term']} / 波动与急跌风险 {a['risk']}"]
        for q in brief.get("quotes", []):
            if q.get("symbol") in {"SPY", "QQQ", "SOXX"} and q.get("fresh"):
                lines.append(f"{q['symbol']} 当日 {q['return_1d_pct']:+.2f}%｜近5日 {q['return_5d_pct']:+.2f}%")
        if a.get("risk_reasons"):
            lines.append("风险依据：" + "；".join(a["risk_reasons"]))
    else:
        lines.append("市场晨报未齐或过期：环境判断待核验；以下计划来自已核验的独立策略账户。")
    lines.append("\n下一开盘模拟计划（未成交）：")
    if not plan.get("actions"):
        lines.append("今日无买卖计划，继续按规则等待。")
    for action in plan.get("actions", []):
        if action.get("side") not in {"buy", "sell"}:
            raise DeliveryError("存在未知操作方向，停止发送")
        side = "拟买入" if action["side"] == "buy" else "拟卖出"
        lines.append(f"{action['symbol']} {side}｜{action.get('reason', '按既定规则')}")
        if action["side"] == "buy" and account.get("buy_budget_usd"):
            lines.append(f"每笔模拟预算 ${account['buy_budget_usd']:,.2f}")
    lines += ["\n信号来自已完成的收盘数据，计划不代表实际成交。", site]
    return "\n".join(lines)


def run(root: Path, *, failure: bool = False, now: datetime | None = None) -> str:
    url, secret = os.environ.get("FEISHU_WEBHOOK_URL", ""), os.environ.get("FEISHU_WEBHOOK_SECRET", "")
    if not url or not secret:
        return "skipped: 请配置两项飞书 Actions Secrets"
    now = now or datetime.now(timezone.utc)
    repo = os.environ.get("GITHUB_REPOSITORY", "53Wusan/etf53")
    site = f"https://{repo.split('/')[0].lower()}.github.io/{repo.split('/')[1]}/"
    if failure:
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        send(url, secret, f"53 · 杠杆ETF更新异常\n本次数据更新、日报发送或网页构建未正常完成。请核对运行结果，勿将旧计划当作最新操作。\nhttps://github.com/{repo}/actions/runs/{run_id}")
        return "sent: 更新异常提醒"
    public = root / "frontend/public/data"
    def read(name):
        return json.loads((public / f"{name}.json").read_text(encoding="utf8"))
    catalog, account, brief = read("catalog-rank2"), read("account"), read("market-brief")
    text = daily_text(catalog, account, brief, now, site)
    target = hashlib.sha256(url.encode()).hexdigest()[:16]
    receipt = root / "research/output/forward_data/feishu_receipts" / f"{catalog['cutoff']}-{target}.json"
    if receipt.exists():
        return "skipped: 该交易日已发送"
    send(url, secret, text)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({"data_date": catalog["cutoff"], "sent_at_utc": now.isoformat(), "run_id": os.environ.get("GITHUB_RUN_ID"), "message_sha256": hashlib.sha256(text.encode()).hexdigest()}, indent=2), encoding="utf8")
    return "sent: 每日短讯及模拟计划"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--failure", action="store_true")
    args = parser.parse_args()
    try:
        print(run(Path(__file__).resolve().parents[3], failure=args.failure))
    except Exception:
        raise SystemExit("飞书日报未确认送达；请核对配置、数据日期及网络。敏感信息已省略。") from None
