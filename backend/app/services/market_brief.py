"""Read-only market context, public news and calendars; no AI and no trade hooks."""

from __future__ import annotations

import calendar
import html
import io
import json
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
import pandas_market_calendars as mcal

from app.services.market_data import YahooChartProvider
from app.services.public_history import PublicHistoryProvider
from app.services.research_cockpit import _research_output
from app.services.research_data_refresh import last_completed_us_session

VERSION = "market-context-v1"
CORE = ("SPY", "QQQ", "IWM")
LABELS = {
    "SPY": "标普500",
    "QQQ": "纳指100",
    "IWM": "美国小盘",
    "RSP": "标普等权",
    "SOXX": "半导体",
    "GLD": "黄金ETF",
    "UUP": "美元ETF",
    "^TNX": "美债10年收益率",
}
VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
FEEDS = {
    "WSJ市场": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "美联储政策": "https://www.federalreserve.gov/feeds/press_monetary.xml",
}
CALENDARS = {
    "美联储会议": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "BEA经济数据": "https://www.bea.gov/news/schedule",
    "BLS经济数据": "https://www.bls.gov/schedule/news_release/bls.ics",
}
TOPICS = {
    "科技与AI": r"\b(ai|artificial intelligence|tech|technology|nvidia|intel|micron|amd|broadcom|microsoft|apple|alphabet|google|meta|amazon|tesla|semiconductor|chips?|nasdaq)\b",
    "利率与通胀": r"\b(fed|fomc|yields?|bonds?|rates?|inflation|cpi|ppi|pce|monetary)\b",
    "经济与就业": r"\b(economy|economic|growth|gdp|jobs?|employment|payrolls?|recession)\b",
    "市场与风险": r"\b(stock market|markets?|wall street|s&p|tariffs?|trade war|oil|gold|dollar|credit|earnings)\b",
}


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _get(url: str) -> str:
    response = httpx.get(url, timeout=20, follow_redirects=True)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"来源暂不可读取（HTTP {response.status_code}）") from exc
    if len(response.content) > 2_000_000:
        raise ValueError("来源内容超过预期大小")
    return response.text.lstrip("\ufeff")


def price_metrics(frame: pd.DataFrame, symbol: str, completed: date) -> dict:
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index)
    frame = frame.loc[: str(completed)]
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing or len(frame) < 253:
        raise ValueError("历史日期重复、乱序或不足253日")
    if (
        not np.isfinite(frame[["close", "adjusted_close"]].to_numpy()).all()
        or (frame[["close", "adjusted_close"]] <= 0).any().any()
    ):
        raise ValueError("收盘价缺失或无效")
    series = frame.adjusted_close
    if symbol != "^TNX" and (series.pct_change().abs() > 0.5).any():
        raise ValueError("复权价格单日变动超过50%，需核对来源")
    recent = frame.tail(253)
    expected = (
        mcal.get_calendar("NYSE")
        .valid_days(start_date=recent.index[0], end_date=recent.index[-1])
        .tz_localize(None)
    )
    if not recent.index.equals(expected):
        raise ValueError("近期历史缺少完整交易日")
    return {
        "symbol": symbol,
        "label": LABELS[symbol],
        "date": str(frame.index[-1].date()),
        "fresh": frame.index[-1].date() == completed,
        "close": float(frame.close.iloc[-1]),
        **{
            f"return_{n}d_pct": float((series.iloc[-1] / series.iloc[-n - 1] - 1) * 100)
            for n in (1, 5, 20)
        },
        "distance_ma50_pct": float((series.iloc[-1] / series.tail(50).mean() - 1) * 100),
        "distance_ma200_pct": float((series.iloc[-1] / series.tail(200).mean() - 1) * 100),
        "drawdown_252_pct": float((series.iloc[-1] / series.tail(252).max() - 1) * 100),
        "change_5d_bp": float((frame.close.iloc[-1] - frame.close.iloc[-6]) * 100)
        if symbol == "^TNX"
        else None,
    }


def _quote(symbol: str, completed: date) -> dict:
    last_error = None
    providers = [YahooChartProvider()] + (
        [] if symbol.startswith("^") else [PublicHistoryProvider()]
    )
    for provider in providers:
        try:
            frame = provider.history(symbol, completed - timedelta(days=800), completed)
            metrics = price_metrics(frame, symbol, completed)
            if not metrics["fresh"]:
                raise ValueError(f"行情仅到{metrics['date']}")
            return {
                **metrics,
                "provider": provider.name,
                "source_url": f"https://finance.yahoo.com/quote/{symbol}/history/"
                if provider.name == "yahoo"
                else f"https://stockanalysis.com/etf/{symbol.lower()}/history/",
            }
        except Exception as exc:
            last_error = str(exc)
    raise ValueError(f"{symbol} 行情未就绪：{last_error}")


def parse_vix(text: str, completed: date) -> dict:
    frame = pd.read_csv(io.StringIO(text))
    days = pd.to_datetime(frame.DATE, format="%m/%d/%Y")
    frame = frame.set_index(days).loc[str(completed - timedelta(days=800)) : str(completed)]
    values = pd.to_numeric(frame.CLOSE, errors="coerce")
    if (
        frame.index.has_duplicates
        or not frame.index.is_monotonic_increasing
        or len(frame) < 253
        or not np.isfinite(values).all()
        or (values <= 0).any()
    ):
        raise ValueError("VIX历史无效或不足253日")
    if frame.index[-1].date() != completed:
        raise ValueError(f"VIX仅到{frame.index[-1].date()}")
    expected = (
        mcal.get_calendar("NYSE")
        .valid_days(start_date=frame.index[0], end_date=frame.index[-1])
        .tz_localize(None)
    )
    if len(expected.difference(frame.index)):
        raise ValueError("VIX近期历史缺少交易日")
    excluded = int((~frame.index.isin(expected)).sum())
    frame = frame.loc[frame.index.isin(expected)]
    values = pd.to_numeric(frame.CLOSE, errors="coerce")
    if len(values) < 253:
        raise ValueError("VIX有效交易日不足253日")
    return {
        "date": str(completed),
        "close": float(values.iloc[-1]),
        "change_5d_pct": float((values.iloc[-1] / values.iloc[-6] - 1) * 100),
        "prior252_percentile": float((values.iloc[-253:-1] <= values.iloc[-1]).mean() * 100),
        "source_url": VIX_URL,
        "excluded_non_session_rows": excluded,
    }


def classify(quotes: dict, vix: dict | None) -> dict:
    if not all(s in quotes and quotes[s]["fresh"] for s in CORE):
        return {
            "trend": "数据待核验",
            "short_term": "数据待核验",
            "risk": "待核验",
            "summary": "主要市场行情未到齐，暂停环境判断。",
        }
    rows = [quotes[s] for s in CORE]
    up = sum(r["distance_ma50_pct"] > 0 and r["distance_ma200_pct"] > 0 for r in rows)
    down = sum(r["distance_ma50_pct"] < 0 and r["distance_ma200_pct"] < 0 for r in rows)
    trend = "中期上行" if up >= 2 else "中期下行" if down >= 2 else "震荡分化"
    five = [r["return_5d_pct"] for r in rows]
    short = (
        "短期回撤"
        if np.mean(five) <= -1 and sum(v < 0 for v in five) >= 2
        else "短期回升"
        if np.mean(five) >= 1 and sum(v > 0 for v in five) >= 2
        else "短期震荡"
    )
    q, s = quotes["QQQ"]["return_5d_pct"], quotes["SPY"]["return_5d_pct"]
    risk = (
        "待核验"
        if vix is None
        else "很高"
        if vix["close"] >= 35 or q <= -10 or s <= -6
        else "偏高"
        if vix["close"] >= 25 or vix["change_5d_pct"] >= 25 or q <= -5 or s <= -3
        else "常态"
    )
    tech = (
        "科技回撤较大"
        if q <= -3 or quotes.get("SOXX", {}).get("return_5d_pct", 0) <= -5
        else "科技走势未触发急跌观察线"
    )
    return {
        "trend": trend,
        "short_term": short,
        "risk": risk,
        "technology": tech,
        "summary": f"{trend}，{short}。纳指100近5日{q:+.1f}%，标普500近5日{s:+.1f}%；{tech}。",
    }


def parse_feed(text: str, source: str, now: datetime) -> list[dict]:
    root = ET.fromstring(text.lstrip("\ufeff"))
    if root.tag.split("}")[-1] not in ("rss", "RDF"):
        raise ValueError("新闻来源未返回RSS")
    if not root.findall(".//item"):
        raise ValueError("订阅源没有可核验的新闻条目")
    items = []
    for item in root.findall(".//item"):
        title, link = _text(item.findtext("title", "")), item.findtext("link", "").strip()
        stamp = item.findtext("pubDate", "")
        try:
            published = parsedate_to_datetime(stamp)
            if published.tzinfo is None:
                continue
            published = published.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            continue
        if (
            not stamp
            or not title
            or urlparse(link).scheme != "https"
            or not now - timedelta(hours=72) <= published <= now + timedelta(minutes=5)
        ):
            continue
        tags = [tag for tag, pattern in TOPICS.items() if re.search(pattern, title, re.I)]
        if not tags and source != "美联储政策":
            continue
        items.append(
            {
                "title": title[:220],
                "url": link,
                "source": source,
                "published_at_utc": published.isoformat(),
                "tags": tags or ["利率与通胀"],
            }
        )
    return sorted(items, key=lambda item: item["published_at_utc"], reverse=True)


def select_news(news: list[dict]) -> list[dict]:
    """Prioritize market-wide/policy stories; avoid the newest routine sector roundups."""

    def priority(item):
        title = item["title"]
        rank = (
            0
            if re.search(
                r"stock market|wall street|nasdaq|s&p|federal reserve|\bfed\b|\bfomc\b", title, re.I
            )
            else 1
            if any(t in item["tags"] for t in ("利率与通胀", "经济与就业"))
            else 2
            if "科技与AI" in item["tags"]
            else 3
        )
        return rank, -datetime.fromisoformat(item["published_at_utc"]).timestamp()

    seen, selected, per_source = set(), [], {}
    for item in sorted(news, key=priority):
        if re.search(
            r"roundup|market talk|^(my |your |dear |meet the |how to |should i )",
            item["title"],
            re.I,
        ):
            continue
        key = re.sub(r"\W", "", item["title"].casefold())
        if key in seen or per_source.get(item["source"], 0) >= 4:
            continue
        seen.add(key)
        selected.append(item)
        per_source[item["source"]] = per_source.get(item["source"], 0) + 1
        if len(selected) == 8:
            break
    return sorted(selected, key=lambda item: item["published_at_utc"], reverse=True)


def parse_fomc(text: str, today: date) -> list[dict]:
    sections = list(re.finditer(r"(20\d{2}) FOMC Meetings", text))
    events = []
    pairs = 0
    for i, section in enumerate(sections):
        year = int(section.group(1))
        part = text[section.end() : sections[i + 1].start() if i + 1 < len(sections) else len(text)]
        for month, days in re.findall(
            r"fomc-meeting__month[^>]*>(.*?)</div>\s*<div[^>]*fomc-meeting__date[^>]*>(.*?)</div>",
            part,
            re.S,
        ):
            month, days = _text(month), _text(days)
            if month not in calendar.month_name or not re.fullmatch(
                r"\d{1,2}(?:-\d{1,2})?\*?", days
            ):
                continue
            day = date(
                year, list(calendar.month_name).index(month), int(re.findall(r"\d+", days)[-1])
            )
            pairs += 1
            if today <= day <= today + timedelta(days=14):
                events.append(
                    {
                        "date": str(day),
                        "time_utc": None,
                        "title": "FOMC会议结束日",
                        "source": "美联储会议",
                        "url": CALENDARS["美联储会议"],
                        "timing_note": "美东日期，具体公布时间以官方为准",
                    }
                )
    if not pairs:
        raise ValueError("未识别到会议日程，不能当作没有会议")
    return events


def parse_bea(text: str, today: date) -> list[dict]:
    events, count, year = [], 0, None
    for token in re.finditer(
        r'Year (20\d{2})</th>|<tr class="scheduled-releases-type-press">(.*?)</tr>', text, re.S
    ):
        if token.group(1):
            year = token.group(1)
            continue
        row = token.group(2)
        month_day = re.search(r'<div class="release-date">(.*?)</div>', row, re.S)
        title = re.search(r'<td class="release-title[^>]*>(.*?)</td>', row, re.S)
        if not month_day or not title or year is None:
            continue
        day = datetime.strptime(f"{_text(month_day.group(1))} {year}", "%B %d %Y").date()
        count += 1
        if today <= day <= today + timedelta(days=14):
            # Keep a verified date only; do not guess an unverified timezone or release hour.
            title = _text(title.group(1))
            label = (
                "GDP发布"
                if title.startswith("GDP")
                else "个人收入与支出（含PCE）"
                if title.startswith("Personal Income and Outlays")
                else title
            )
            events.append(
                {
                    "date": str(day),
                    "time_utc": None,
                    "title": label,
                    "original_title": title,
                    "source": "BEA经济数据",
                    "url": CALENDARS["BEA经济数据"],
                    "timing_note": "官方发布日期；具体时间查看原日历",
                }
            )
    if not count:
        raise ValueError("未识别到BEA日程，不能当作没有事件")
    return events


def parse_bls(text: str, today: date) -> list[dict]:
    if not text.startswith("BEGIN:VCALENDAR"):
        raise ValueError("未返回有效经济日历")
    text = re.sub(r"\r?\n[ \t]", "", text)
    events, count = [], 0
    for item in text.split("BEGIN:VEVENT")[1:]:
        fields = {}
        for line in item.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key] = value
        start = next(((k, v) for k, v in fields.items() if k.startswith("DTSTART")), None)
        if not start:
            continue
        key, value = start
        try:
            if value.endswith("Z"):
                moment = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            elif "TZID=" in key:
                zone = key.split("TZID=", 1)[1].strip('"')
                moment = datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=ZoneInfo(zone))
            else:
                continue
        except (ValueError, KeyError):
            continue
        count += 1
        day = moment.astimezone(ZoneInfo("America/New_York")).date()
        title = fields.get("SUMMARY", "").replace(r"\,", ",")
        if today <= day <= today + timedelta(days=14) and re.search(
            r"consumer price|producer price|employment situation|job openings|employment cost|productivity",
            title,
            re.I,
        ):
            events.append(
                {
                    "date": str(day),
                    "time_utc": moment.astimezone(timezone.utc).isoformat(),
                    "title": title,
                    "source": "BLS经济数据",
                    "url": CALENDARS["BLS经济数据"],
                    "timing_note": "时间来自官方日历",
                }
            )
    if not count:
        raise ValueError("日历没有可核验的带时区事件")
    return events


def refresh(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    completed = last_completed_us_session(now)
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    quotes, news, events, checks = {}, [], [], []
    jobs = [(symbol, "quote", lambda s=symbol: _quote(s, completed)) for symbol in LABELS]
    jobs += [("VIX", "quote", lambda: parse_vix(_get(VIX_URL), completed))]
    jobs += [
        (name, "news", lambda n=name, u=url: parse_feed(_get(u), n, now))
        for name, url in FEEDS.items()
    ]
    parsers = (parse_fomc, parse_bea, parse_bls)
    jobs += [
        (name, "calendar", lambda p=parser, u=url: p(_get(u), today))
        for (name, url), parser in zip(CALENDARS.items(), parsers, strict=True)
    ]
    vix = None

    def execute(job):
        name, kind, fn = job
        try:
            return name, kind, fn(), None
        except Exception as exc:
            return name, kind, None, str(exc)[:220]

    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, kind, value, error in pool.map(execute, jobs):
            url = (
                FEEDS.get(name)
                or CALENDARS.get(name)
                or (
                    VIX_URL if name == "VIX" else f"https://finance.yahoo.com/quote/{name}/history/"
                )
            )
            if isinstance(value, dict):
                url = value.get("source_url", url)
            checks.append(
                {
                    "name": name,
                    "kind": kind,
                    "ok": error is None,
                    "message": error,
                    "url": url,
                    "data_date": value.get("date") if isinstance(value, dict) else None,
                }
            )
            if error:
                continue
            if name == "VIX":
                vix = value
            elif kind == "quote":
                quotes[name] = value
            elif kind == "news":
                news.extend(value)
            else:
                events.extend(value)
    selected = select_news(news)
    next_session = (
        mcal.get_calendar("NYSE")
        .schedule(start_date=completed + timedelta(days=1), end_date=completed + timedelta(days=14))
        .iloc[0]
    )
    aligned = all(s in quotes for s in CORE)
    result = {
        "version": VERSION,
        "status": "partial" if any(not c["ok"] for c in checks) else "ok",
        "market_date": str(completed),
        "generated_at_utc": now.isoformat(),
        "valid_until": (next_session.market_close + pd.Timedelta(minutes=30)).isoformat(),
        "market_ready": aligned,
        "assessment": classify(quotes, vix),
        "quotes": list(quotes.values()),
        "vix": vix,
        "breadth_relative_20d_pp": quotes["RSP"]["return_20d_pct"] - quotes["SPY"]["return_20d_pct"]
        if "RSP" in quotes and "SPY" in quotes
        else None,
        "news": selected,
        "events": sorted(events, key=lambda e: (e["date"], e["title"])),
        "calendar_complete": all(c["ok"] for c in checks if c["kind"] == "calendar"),
        "sources": checks,
        "note": "规则描述已发生的收盘状态，不预测下一日方向；新闻按主题和时间筛选，不是AI解读，不参与现网买卖。",
    }
    directory = _research_output() / "market_brief"
    captures = directory / "captures"
    captures.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    (captures / now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%S%fZ.json")).write_text(
        text, encoding="utf8"
    )
    temporary = directory / "latest.tmp"
    temporary.write_text(text, encoding="utf8")
    os.replace(temporary, directory / "latest.json")
    return result


def status() -> dict:
    path = _research_output() / "market_brief/latest.json"
    return (
        json.loads(path.read_text(encoding="utf8")) if path.exists() else {"status": "not_started"}
    )


if __name__ == "__main__":
    value = refresh()
    print(
        json.dumps(
            {k: value[k] for k in ("status", "market_date", "assessment")}, ensure_ascii=False
        )
    )
