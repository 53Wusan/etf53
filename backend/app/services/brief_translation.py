"""Best-effort public headline translation; no account data is sent externally."""

import re
from concurrent.futures import ThreadPoolExecutor

import httpx

ENDPOINT = "https://translate.googleapis.com/translate_a/single"
EVENT_NAMES = {
    "Consumer Price Index": "消费者价格指数（CPI）",
    "Producer Price Index": "生产者价格指数（PPI）",
    "Employment Situation": "非农就业报告",
    "Job Openings and Labor Turnover Survey": "职位空缺与劳动力流动调查（JOLTS）",
    "Employment Cost Index": "就业成本指数",
    "Productivity and Costs": "劳动生产率与成本",
    "U.S. Import and Export Price Indexes": "美国进出口价格指数",
}


def _translate(title: str) -> str:
    # Public, unofficial endpoint: bounded timeout, original text retained on failure.
    response = httpx.get(
        ENDPOINT,
        params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": title},
        timeout=6,
    )
    response.raise_for_status()
    value = "".join(part[0] for part in response.json()[0] if part and part[0])
    if not re.search(r"[\u4e00-\u9fff]", value) or len(value) > 800:
        raise ValueError("invalid translation")
    return value


def translate_items(news: list[dict], events: list[dict], previous: dict) -> bool:
    cache = {
        item["title"]: item["title_zh"]
        for item in previous.get("news", []) + previous.get("events", [])
        if item.get("title_zh") and item.get("translation_status") == "translated"
    }
    items = news + events

    def translate(item):
        title = item["title"]
        item["original_title"] = item.get("original_title", title)
        if re.search(r"[\u4e00-\u9fff]", title):
            item.update(title_zh=title, translation_status="original_zh")
        else:
            try:
                value = EVENT_NAMES.get(title) or cache.get(title) or _translate(title)
                item.update(title_zh=value, translation_status="translated")
            except Exception:
                item.update(title_zh=None, translation_status="unavailable")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(translate, items))
    return all(item["translation_status"] != "unavailable" for item in items)
