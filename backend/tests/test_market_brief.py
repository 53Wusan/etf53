from datetime import date, datetime, timezone
from xml.sax.saxutils import escape

import pandas as pd
import pandas_market_calendars as mcal
import pytest

from app.services import market_brief as m


def row(five=-1, above=True):
    return {
        "fresh": True,
        "return_5d_pct": five,
        "distance_ma50_pct": 1 if above else -1,
        "distance_ma200_pct": 1 if above else -1,
    }


def test_uptrend_can_coexist_with_short_term_stress():
    quotes = {s: row(-6) for s in m.CORE}
    result = m.classify(quotes, {"close": 19, "change_5d_pct": 10})
    assert result["trend"] == "中期上行"
    assert result["short_term"] == "短期回撤"
    assert result["risk"] == "很高"  # SPY <= -6% is independently severe.
    assert m.classify(quotes, None)["risk"] == "待核验"


def test_incomplete_market_cannot_be_classified_as_normal():
    quotes = {s: row(2) for s in m.CORE}
    quotes["QQQ"]["fresh"] = False
    result = m.classify(quotes, {"close": 12, "change_5d_pct": 0})
    assert result["trend"] == "数据待核验"
    assert result["risk"] == "待核验"


def test_price_returns_use_adjusted_prices_and_exclude_intraday():
    dates = (
        mcal.get_calendar("NYSE")
        .valid_days(start_date="2025-01-01", end_date="2026-10-19")
        .tz_localize(None)
    )
    frame = pd.DataFrame({"close": 100.0, "adjusted_close": 100.0}, index=dates)
    frame.loc["2026-10-16", "close"] = 50.0  # Raw split alone must not create a crash.
    frame.loc["2026-10-19", ["close", "adjusted_close"]] = 1
    metrics = m.price_metrics(frame, "SPY", date(2026, 10, 16))
    assert metrics["fresh"]
    assert metrics["return_1d_pct"] == 0
    assert metrics["close"] == 50.0
    frame = frame.drop(pd.Timestamp("2026-10-15"))
    with pytest.raises(ValueError, match="缺少完整交易日"):
        m.price_metrics(frame, "SPY", date(2026, 10, 16))


def rss(items):
    return (
        "<rss><channel>"
        + "".join(
            f"<item><title>{escape(title)}</title><link>{escape(url)}</link><pubDate>{stamp}</pubDate></item>"
            for title, url, stamp in items
        )
        + "</channel></rss>"
    )


def test_feed_filters_future_stale_irrelevant_and_unsafe_links():
    now = datetime(2026, 10, 19, 16, tzinfo=timezone.utc)
    text = rss(
        [
            ("Tech stocks fall", "https://example.com/a", "Mon, 19 Oct 2026 15:00:00 GMT"),
            ("Tech future", "https://example.com/b", "Tue, 20 Oct 2026 15:00:00 GMT"),
            ("Market old", "https://example.com/c", "Mon, 12 Oct 2026 15:00:00 GMT"),
            (
                "My sister inherited a house",
                "https://example.com/d",
                "Mon, 19 Oct 2026 15:00:00 GMT",
            ),
            ("Tech unsafe", "javascript:alert(1)", "Mon, 19 Oct 2026 15:00:00 GMT"),
        ]
    )
    result = m.parse_feed(text, "test", now)
    assert [x["title"] for x in result] == ["Tech stocks fall"]
    with pytest.raises(ValueError):
        m.parse_feed("<html><body>Access denied</body></html>", "test", now)


def test_fomc_years_are_scoped_and_date_is_not_given_an_invented_time():
    text = '2026 FOMC Meetings<div class="fomc-meeting__month"><strong>October</strong></div><div class="fomc-meeting__date">27-28*</div>2025 FOMC Meetings<div class="fomc-meeting__month">October</div><div class="fomc-meeting__date">28-29</div>'
    events = m.parse_fomc(text, date(2026, 10, 19))
    assert len(events) == 1
    assert events[0]["date"] == "2026-10-28"
    assert events[0]["time_utc"] is None


def test_news_selection_keeps_market_context_instead_of_routine_roundups():
    now = datetime(2026, 10, 19, 16, tzinfo=timezone.utc)
    text = rss(
        [
            (
                "Stock Market Today: Tech Stocks Rebound",
                "https://example.com/main",
                "Mon, 19 Oct 2026 08:00:00 GMT",
            ),
            (
                "Auto Roundup: Market Talk",
                "https://example.com/routine",
                "Mon, 19 Oct 2026 15:59:00 GMT",
            ),
            (
                "Meet the homebuyers facing higher rates",
                "https://example.com/feature",
                "Mon, 19 Oct 2026 15:58:00 GMT",
            ),
        ]
    )
    selected = m.select_news(m.parse_feed(text, "test", now) * 2)
    assert [item["url"] for item in selected] == ["https://example.com/main"]


def test_bea_calendar_respects_year_rollover():
    text = 'Year 2026</th><tr class="scheduled-releases-type-press"><div class="release-date">December 30</div><td class="release-title">GDP test</td></tr>Year 2027</th><tr class="scheduled-releases-type-press"><div class="release-date">January 3</div><td class="release-title">GDP test</td></tr>'
    assert [e["date"] for e in m.parse_bea(text, date(2026, 12, 27))] == [
        "2026-12-30",
        "2027-01-03",
    ]


def test_bls_folded_calendar_preserves_its_timezone():
    text = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART;TZID=America/New_York:20261021T083000\nSUMMARY:Consumer Price\n Index\nEND:VEVENT\nEND:VCALENDAR"
    events = m.parse_bls(text, date(2026, 10, 19))
    assert events[0]["time_utc"].startswith("2026-10-21T12:30:00")
    with pytest.raises(ValueError):
        m.parse_bls("<html>403</html>", date(2026, 10, 19))


def test_vix_is_ranked_against_prior_days_only():
    dates = (
        mcal.get_calendar("NYSE")
        .valid_days(start_date="2025-01-01", end_date="2026-10-16")
        .tz_localize(None)
    )
    frame = pd.DataFrame({"DATE": dates.strftime("%m/%d/%Y"), "CLOSE": 20.0})
    frame.loc[frame.index[-1], "CLOSE"] = 25.0
    result = m.parse_vix(frame.to_csv(index=False), date(2026, 10, 16))
    assert result["prior252_percentile"] == 100
    with pytest.raises(ValueError, match="仅到"):
        m.parse_vix(frame.iloc[:-1].to_csv(index=False), date(2026, 10, 16))


def test_vix_holiday_records_are_excluded_without_masking_missing_sessions():
    dates = (
        mcal.get_calendar("NYSE")
        .valid_days(start_date="2025-01-01", end_date="2026-10-16")
        .tz_localize(None)
    )
    frame = pd.DataFrame({"DATE": dates, "CLOSE": 20.0})
    frame = pd.concat(
        [frame, pd.DataFrame({"DATE": [pd.Timestamp("2026-09-07")], "CLOSE": [999.0]})]
    ).sort_values("DATE")
    frame["DATE"] = frame["DATE"].dt.strftime("%m/%d/%Y")
    result = m.parse_vix(frame.to_csv(index=False), date(2026, 10, 16))
    assert result["excluded_non_session_rows"] == 1
    assert result["change_5d_pct"] == 0
    with pytest.raises(ValueError, match="缺少交易日"):
        m.parse_vix(frame.loc[frame.DATE != "10/15/2026"].to_csv(index=False), date(2026, 10, 16))


def test_refresh_publishes_partial_data_without_hiding_source_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_research_output", lambda: tmp_path)
    monkeypatch.setattr(m, "last_completed_us_session", lambda now: date(2026, 10, 16))
    monkeypatch.setattr(
        m, "_quote", lambda s, d: {**row(), "symbol": s, "date": str(d), "return_20d_pct": 1.0}
    )

    def broken(url):
        raise ValueError("source unavailable")

    monkeypatch.setattr(m, "_get", broken)
    result = m.refresh(datetime(2026, 10, 19, 16, tzinfo=timezone.utc))
    assert result["market_ready"]
    assert result["assessment"]["risk"] == "待核验"
    assert not result["calendar_complete"]
    assert result["status"] == "partial"
    assert (tmp_path / "market_brief/latest.json").exists()
    assert len(list((tmp_path / "market_brief/captures").glob("*.json"))) == 1
