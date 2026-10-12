"""Guard the frontend's development data against API drift and impossible UI states."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ValidationError

from airalert.config import FIXTURES_DIR, HORIZONS, TEST_END_OFFSET_H
from airalert.schemas import (
    AuthStatus,
    ForecastResponse,
    HistoryResponse,
    LiveState,
    Meta,
    NewsFeed,
    Range,
    Report,
    Settings,
    SystemStatus,
)


@pytest.mark.parametrize(
    ("filename", "model"),
    [
        ("meta.json", Meta),
        ("forecast_replay.json", ForecastResponse),
        ("forecast_latest.json", ForecastResponse),
        ("history.json", HistoryResponse),
        ("report.json", Report),
        ("live.json", LiveState),
        ("live_stale.json", LiveState),
        ("system.json", SystemStatus),
        ("settings.json", Settings),
        *[(name, AuthStatus) for name in (
            "auth_logged_out.json", "auth_logged_in.json", "auth_missing.json", "auth_pending.json"
        )],
        *[(name, NewsFeed) for name in (
            "news.json", "news_off.json", "news_needs_codex.json", "news_needs_login.json",
            "news_starting.json", "news_stale.json", "news_error.json", "news_quota_limited.json"
        )],
    ],
)
def test_fixture_schema(filename: str, model: type[BaseModel]) -> None:
    fixture = json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    validated = model.model_validate(fixture)
    assert json.loads(validated.model_dump_json()) == fixture


def test_forecast_and_map_consistency() -> None:
    meta = Meta.model_validate_json((FIXTURES_DIR / "meta.json").read_text())
    geo = json.loads((FIXTURES_DIR / "raions.geojson").read_text())
    assert geo["type"] == "FeatureCollection"
    assert len(geo["features"]) == 139
    map_ids: set[str] = set()
    for feature in geo["features"]:
        assert feature["type"] == "Feature"
        properties = feature["properties"]
        assert set(properties) == {"raion_id", "oblast_id", "name", "display_key"}
        assert isinstance(properties["display_key"], str) and properties["display_key"]
        assert isinstance(properties["oblast_id"], str) and properties["oblast_id"]
        assert isinstance(properties["name"], str) and properties["name"]
        if properties["raion_id"] is not None:
            assert isinstance(properties["raion_id"], str)
            assert properties["raion_id"] not in map_ids
            map_ids.add(properties["raion_id"])
        assert feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
        polygons = (
            feature["geometry"]["coordinates"]
            if feature["geometry"]["type"] == "MultiPolygon"
            else [feature["geometry"]["coordinates"]]
        )
        for ring in (ring for polygon in polygons for ring in polygon):
            assert len(ring) >= 4 and ring[0] == ring[-1]
            assert all(len(point) == 2 for point in ring)
    assert any(feature["properties"]["raion_id"] is None for feature in geo["features"])
    assert tuple(meta.horizons) == HORIZONS
    assert meta.latest_at == meta.snapshot
    assert meta.replay == meta.splits["test"]
    assert meta.replay.end == meta.snapshot - timedelta(hours=TEST_END_OFFSET_H)
    all_ids: list[set[str]] = []
    for mode in ("replay", "latest"):
        response = ForecastResponse.model_validate_json(
            (FIXTURES_DIR / f"forecast_{mode}.json").read_text()
        )
        assert response.mode == mode
        assert response.onset_model == meta.onset_model
        assert response.duration_model == meta.duration_model
        assert len(response.rows) == meta.raion_count
        row_ids = {row.raion_id for row in response.rows}
        assert len(row_ids) == len(response.rows)
        assert map_ids <= row_ids
        all_ids.append(row_ids)
        if mode == "latest":
            assert response.at == meta.latest_at
        else:
            assert meta.replay.start <= response.at <= meta.replay.end
        for row in response.rows:
            assert 0 <= row.p_1 <= row.p_3 <= row.p_12 <= 1
            ends = (row.end_p10, row.end_p50, row.end_p90)
            if row.active:
                assert row.active_since is not None and row.active_since < response.at
                assert all(end is not None for end in ends)
                assert row.end_p10 is not None
                assert row.end_p50 is not None
                assert row.end_p90 is not None
                assert row.active_since < row.end_p10 <= row.end_p50 <= row.end_p90
            else:
                assert row.active_since is None and all(end is None for end in ends)
            actual = (row.actual_onset_1, row.actual_onset_3, row.actual_onset_12)
            if mode == "latest":
                assert actual == (None, None, None)
            else:
                assert all(isinstance(value, bool) for value in actual)
                assert actual[0] <= actual[1] <= actual[2]
    assert all_ids[0] == all_ids[1]


def test_live_display_keys_and_news_adjustments_cover_real_ui_states() -> None:
    """The maps share crosswalk keys; news deltas stay separate from probabilities."""
    geo = json.loads((FIXTURES_DIR / "raions.geojson").read_text())
    live = LiveState.model_validate_json((FIXTURES_DIR / "live.json").read_text())
    keys = [feature["properties"]["display_key"] for feature in geo["features"]]
    assert len(keys) == len(set(keys)) == 139
    assert {unit.display_key for unit in live.units} == set(keys)
    assert any(unit.red_since and unit.yellow_since for unit in live.units)
    assert any(unit.yellow_since and not unit.red_since for unit in live.units)
    assert any(unit.red_since and not unit.yellow_since for unit in live.units)
    assert {unit.oblast_id for unit in live.units if unit.coverage == "inherited"} == {
        "autonomous-republic-of-crimea", "luhanska"
    }
    assert {unit.oblast_id for unit in live.units if unit.coverage == "unknown"} == {
        "kyivska", "sevastopol-city"
    }
    stale = LiveState.model_validate_json((FIXTURES_DIR / "live_stale.json").read_text())
    assert stale.stale and stale.units == live.units
    news = NewsFeed.model_validate_json((FIXTURES_DIR / "news.json").read_text())
    base = ForecastResponse.model_validate_json((FIXTURES_DIR / "forecast_latest.json").read_text())
    assert news.status.state == "running" and news.base_at is not None
    ids = {event.id for event in news.events}
    base_ids = {row.raion_id for row in base.rows}
    for row in news.adjustments:
        assert row.raion_id in base_ids
        assert set(row.event_ids) <= ids
        assert 0 <= row.p_1 <= row.p_3 <= row.p_12 <= 1


def test_history_is_seven_hourly_days() -> None:
    history = HistoryResponse.model_validate_json((FIXTURES_DIR / "history.json").read_text())
    replay = ForecastResponse.model_validate_json(
        (FIXTURES_DIR / "forecast_replay.json").read_text()
    )
    assert history.raion_id in {row.raion_id for row in replay.rows}
    assert history.end == replay.at
    assert history.end - history.start == timedelta(days=7)
    assert len(history.series) == 7 * 24
    assert [point.t for point in history.series] == [
        history.start + timedelta(hours=i) for i in range(7 * 24)
    ]
    for point in history.series:
        assert 0 <= point.p_1 <= point.p_3 <= point.p_12 <= 1
    assert 2 <= len(history.episodes) <= 3
    for episode in history.episodes:
        assert history.start <= episode.start < history.end
        assert episode.end is None or episode.start < episode.end <= history.end
    row = next(row for row in replay.rows if row.raion_id == history.raion_id)
    assert row.active_since == history.episodes[-1].start


def test_report_has_comparable_complete_scores() -> None:
    report = Report.model_validate_json((FIXTURES_DIR / "report.json").read_text())
    scores = {(score.model, score.horizon, score.split): score for score in report.onset}
    assert set(scores) == {
        (model, horizon, split)
        for model in ("B0", "B1", "M1")
        for horizon in HORIZONS
        for split in ("val", "test")
    }
    assert len(report.onset) == len(scores)
    for score in report.onset:
        baseline = scores["B0", score.horizon, score.split]
        assert score.n == baseline.n and score.base_rate == baseline.base_rate
        assert score.row_set == baseline.row_set == "full"
        assert score.bss == pytest.approx(1 - score.brier / baseline.brier, abs=1e-6)
        assert 0 <= score.base_rate <= 1 and 0 <= score.brier <= 1
        assert score.logloss >= 0 and 0 <= score.pr_auc <= 1
        assert len(score.reliability) == 10
        assert sum(bin_.count for bin_ in score.reliability) == score.n
        positives = 0.0
        for index, bin_ in enumerate(score.reliability):
            assert bin_.lo == index / 10 and bin_.hi == (index + 1) / 10
            if bin_.count:
                assert bin_.mean_pred is not None and bin_.frac_pos is not None
                assert bin_.lo <= bin_.mean_pred <= bin_.hi
                assert 0 <= bin_.frac_pos <= 1
                positives += bin_.count * bin_.frac_pos
            else:
                assert bin_.mean_pred is None and bin_.frac_pos is None
        assert positives / score.n == pytest.approx(score.base_rate, abs=1e-6)
        assert set(score.by_state) == {"quiet", "active"}
        for slices in (score.by_oblast, score.by_state):
            assert sum(slice_.n for slice_ in slices.values()) == score.n
            assert sum(s.n * s.base_rate for s in slices.values()) / score.n == pytest.approx(
                score.base_rate, abs=1e-6
            )
            assert sum(s.n * s.brier for s in slices.values()) / score.n == pytest.approx(
                score.brier, abs=1e-6
            )
    assert {(score.model, score.split) for score in report.duration} == {
        (model, split) for model in ("D0", "D1") for split in ("val", "test")
    }
    for score in report.duration:
        assert score.n > 0 and 0 <= score.coverage_80 <= 1
        assert min(score.pinball_q10, score.pinball_q50, score.pinball_q90) >= 0
        assert score.pinball_q50 == pytest.approx(score.mae_q50_min / 2)
    assert report.selection is not None
    assert report.selection.onset in {score.model for score in report.onset}
    assert report.selection.duration in {score.model for score in report.duration}


def test_datetime_contract_normalizes_offsets_and_rejects_naive_input() -> None:
    range_ = Range(start="2026-09-06T06:00:00+03:00", end="2026-09-06T07:00:00+03:00")
    assert range_.start == datetime(2026, 9, 6, 3, tzinfo=UTC)
    assert json.loads(range_.model_dump_json()) == {
        "start": "2026-09-06T03:00:00Z",
        "end": "2026-09-06T04:00:00Z",
    }
    with pytest.raises(ValidationError, match="Datetime must include a timezone"):
        Range(start="2026-09-06T03:00:00", end="2026-09-06T04:00:00Z")
