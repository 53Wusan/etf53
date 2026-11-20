import copy
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.services import verified_daily_recovery as recovery

DAY = date(2026, 11, 19)
PATH = Path(__file__).resolve().parents[2] / "research/data_recoveries/GDXU/2026-11-19.json"


def sample(symbol="GDXU"):
    path = PATH.parents[1] / symbol / PATH.name
    evidence = json.loads(path.read_text(encoding="utf8"))
    dates = [d.date() for d in pd.bdate_range("2026-11-12", "2026-11-19")]
    frame = pd.DataFrame({"open": 60., "high": 61., "low": 59., "close": 60.,
                          "adjusted_close": 60., "volume": 100., "split_factor": 1.}, index=dates)
    for field, value in evidence["expected_daily"].items():
        frame.loc[DAY, field] = float("nan") if value is None else value
    frame.loc[DAY, "adjusted_close"] = float("nan")
    payload = {"meta": {"symbol": symbol, "currency": "USD", "exchangeTimezoneName": "America/New_York"}}
    return frame, payload


def test_recovers_only_missing_reviewed_session_and_preserves_prior_history():
    frame, payload = sample()
    result = recovery.recover_reviewed_closes(frame, payload, "GDXU", DAY)
    assert result.loc[DAY, "close"] == pytest.approx(47.16)
    assert result.loc[DAY, "adjusted_close"] == result.loc[DAY, "close"]
    pd.testing.assert_frame_equal(result.loc[result.index < DAY, frame.columns], frame.loc[frame.index < DAY])
    for field in ("open", "high", "low", "volume"):
        assert result.loc[DAY, field] == frame.loc[DAY, field]
    assert result.loc[DAY, "close_source"] == "reviewed_yahoo_5m_session_close"


def test_supplier_correction_is_not_overridden():
    frame, payload = sample()
    frame.loc[DAY, ["close", "adjusted_close"]] = 47.2
    pd.testing.assert_frame_equal(recovery.recover_reviewed_closes(frame, payload, "GDXU", DAY), frame)


def test_cent_rounded_edge_cache_fields_and_small_volume_revision_are_preserved():
    frame, payload = sample()
    for field in ("open", "high", "low"):
        frame.loc[DAY, field] = round(frame.loc[DAY, field], 2)
    frame.loc[DAY, "volume"] += 10
    result = recovery.recover_reviewed_closes(frame, payload, "GDXU", DAY)
    assert result.loc[DAY, "close"] == pytest.approx(47.16)
    for field in ("open", "high", "low", "volume"):
        assert result.loc[DAY, field] == frame.loc[DAY, field]


def test_material_volume_revision_requires_review():
    frame, payload = sample()
    frame.loc[DAY, "volume"] *= 1.1
    with pytest.raises(ValueError, match="volume differs"):
        recovery.recover_reviewed_closes(frame, payload, "GDXU", DAY)


@pytest.mark.parametrize("symbol", ["GDXU", "CONL", "LABU", "NAIL", "DFEN"])
def test_each_incident_product_has_matching_reviewed_close(symbol):
    frame, payload = sample(symbol)
    evidence = json.loads((PATH.parents[1] / symbol / PATH.name).read_text(encoding="utf8"))
    result = recovery.recover_reviewed_closes(frame, payload, symbol, DAY)
    assert result.loc[DAY, "close"] == evidence["close"]
    assert result.loc[DAY, "close_recovery_evidence"].endswith(f"{symbol}/{PATH.name}")


@pytest.mark.parametrize("change", ["adjustment", "dividend", "daily_price", "identity"])
def test_rejects_changes_that_invalidate_reviewed_evidence(change):
    frame, payload = sample()
    if change == "adjustment":
        frame.iloc[0, frame.columns.get_loc("adjusted_close")] = 30
    elif change == "dividend":
        payload["events"] = {"dividends": {"x": {"date": 1795122000}}}
    elif change == "daily_price":
        frame.loc[DAY, "open"] = 55
    else:
        payload["meta"]["currency"] = "CAD"
    with pytest.raises(ValueError):
        recovery.recover_reviewed_closes(frame, payload, "GDXU", DAY)


def test_no_general_imputation_when_reviewed_file_is_absent(tmp_path, monkeypatch):
    frame, payload = sample()
    monkeypatch.setattr(recovery, "RECOVERY_DIR", tmp_path)
    pd.testing.assert_frame_equal(recovery.recover_reviewed_closes(frame, payload, "GDXU", DAY), frame)


def test_after_hours_record_cannot_be_used_as_close(tmp_path, monkeypatch):
    evidence = copy.deepcopy(json.loads(PATH.read_text(encoding="utf8")))
    evidence["evidence"]["closing_timestamp_utc"] += 3600
    folder = tmp_path / "GDXU"
    folder.mkdir()
    (folder / PATH.name).write_text(json.dumps(evidence), encoding="utf8")
    monkeypatch.setattr(recovery, "RECOVERY_DIR", tmp_path)
    frame, payload = sample()
    with pytest.raises(ValueError):
        recovery.recover_reviewed_closes(frame, payload, "GDXU", DAY)
