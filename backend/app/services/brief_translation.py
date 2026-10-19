"""Best-effort public headline translation; no account data is sent externally."""

import html
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
    errors = []
    for provider in ("Google", "MyMemory"):
        try:
            if provider == "Google":
                response = httpx.get(
                    ENDPOINT,
                    params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": title},
                    timeout=6,
                )
                response.raise_for_status()
                value = "".join(part[0] for part in response.json()[0] if part and part[0])
            else:
                if len(title.encode("utf8")) > 500:
                    raise ValueError("title exceeds fallback byte limit")
                response = httpx.get(
                    "https://api.mymemory.translated.net/get",
                    params={"q": title, "langpair": "en|zh-CN"},
                    timeout=6,
                )
                response.raise_for_status()
                data = response.json()
                if data.get("responseStatus") != 200 or data.get("quotaFinished"):
                    raise ValueError("translation quota or response unavailable")
                value = html.unescape(data["responseData"]["translatedText"])
            if (
                not isinstance(value, str)
                or not re.search(r"[\u4e00-\u9fff]", value)
                or len(value) > 800
            ):
                raise ValueError("invalid translation")
            return value
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            errors.append(
                f"{provider}: HTTP {status}" if status else f"{provider}: {type(exc).__name__}"
            )
    raise ValueError("; ".join(errors))


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
            except Exception as exc:
                item.update(
                    title_zh=None,
                    translation_status="unavailable",
                    translation_error=str(exc)[:160],
                )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(translate, items))
    return all(item["translation_status"] != "unavailable" for item in items)
