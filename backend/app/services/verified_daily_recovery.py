"""Apply repository-reviewed, session-specific close evidence; never infer a price."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal

RECOVERY_DIR = Path(__file__).resolve().parents[3] / "research/data_recoveries"


def recover_reviewed_closes(frame: pd.DataFrame, payload: dict, symbol: str, end: date) -> pd.DataFrame:
    # Deliberately restricted: this is a reviewed incident repair, not automatic imputation.
    if symbol not in ("GDXU", "CONL", "LABU", "NAIL", "DFEN"):
        return frame
    meta = payload.get("meta", {})
    for path in sorted((RECOVERY_DIR / symbol).glob("*.json")):
        evidence = json.loads(path.read_text(encoding="utf8"))
        day = date.fromisoformat(evidence["date"])
        if day > end or day not in frame.index:
            continue
        row = frame.loc[day]
        # Supplier corrections take precedence. Never replace a valid close or adjustment.
        if pd.notna(row.close) or pd.notna(row.adjusted_close):
            continue
        if (evidence["version"] != 1 or evidence["symbol"] != symbol or path.stem != evidence["date"]
                or meta.get("symbol") != symbol or meta.get("currency") != evidence["currency"]
                or meta.get("exchangeTimezoneName") != "America/New_York"):
            raise ValueError("Reviewed recovery identity does not match Yahoo history")
        prior = frame.loc[frame.index < day, ["close", "adjusted_close"]].dropna().tail(5)
        if len(prior) < 5 or not np.allclose(prior.adjusted_close / prior.close, 1, rtol=0, atol=1e-6):
            raise ValueError("Reviewed close recovery requires an unchanged unit adjustment ratio")
        for category in ("splits", "dividends", "capitalGains"):
            for event in payload.get("events", {}).get(category, {}).values():
                event_day = pd.Timestamp(event["date"], unit="s", tz="UTC").tz_convert("America/New_York").date()
                if event_day == day:
                    raise ValueError("Corporate action requires a separately reviewed adjustment")
        for field in ("open", "high", "low", "volume"):
            if not np.isfinite(row[field]) or not np.isclose(row[field], evidence["expected_daily"][field], rtol=0, atol=1e-5):
                raise ValueError(f"Reviewed recovery daily {field} no longer matches supplier data")
        schedule = mcal.get_calendar("NYSE").schedule(start_date=day, end_date=day)
        if len(schedule) != 1:
            raise ValueError("Reviewed recovery is not an NYSE session")
        closing = int(schedule.iloc[0].market_close.timestamp())
        proof, price = evidence["evidence"], float(evidence["close"])
        if (closing != proof["closing_timestamp_utc"] or not closing <= proof["regular_market_time"] <= closing + 60
                or datetime.fromisoformat(proof["captured_at_utc"]).timestamp() <= closing
                or datetime.now(timezone.utc).timestamp() <= closing
                or proof["regular_session_5m_bars"] != int((schedule.iloc[0].market_close - schedule.iloc[0].market_open).total_seconds() / 300)
                or not np.isclose(price, proof["regular_market_price"], rtol=0, atol=.005)
                or not np.isclose(price, evidence["adjusted_close"], rtol=0, atol=1e-8)
                or not row.low <= price <= row.high or price <= 0 or row.volume <= 0):
            raise ValueError("Reviewed session-close evidence failed validation")
        for field in ("open", "high", "low", "close"):
            if not np.isclose(proof["closing_marker"][field], price, rtol=0, atol=1e-8):
                raise ValueError("Reviewed closing marker is inconsistent")
        frame = frame.copy()
        frame.loc[day, ["close", "adjusted_close"]] = price
        if "close_source" not in frame:
            frame["close_source"] = "yahoo_daily"
            frame["close_recovery_evidence"] = ""
        frame.loc[day, "close_source"] = "reviewed_yahoo_5m_session_close"
        frame.loc[day, "close_recovery_evidence"] = f"research/data_recoveries/{symbol}/{path.name}"
    return frame
