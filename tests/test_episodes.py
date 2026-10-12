"""Synthetic coverage tests guard geography, strict gaps and running-end merging."""

import pandas as pd

from airalert.config import MERGE_GAP_MIN
from airalert.data import build_raions, expand_records, merge_episodes


def _source(rows: list[tuple[str, str | None, str, str, str | None]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["oblast", "raion", "level", "started_at", "finished_at"])


def _universe() -> pd.DataFrame:
    return build_raions(_source([
        ("Cherkaska oblast", "Umanskyi raion", "raion", "2026-01-01", "2026-01-02"),
        ("Cherkaska oblast", "Cherkaskyi raion", "hromada", "2026-01-01", "2026-01-02"),
        ("Odeska oblast", "Odeskyi raion", "raion", "2026-01-01", "2026-01-02"),
        ("Kyiv City", None, "oblast", "2026-01-01", "2026-01-02"),
    ]))


def test_oblast_expands_only_to_its_universe_and_special_city() -> None:
    records = expand_records(_source([
        ("Cherkaska oblast", None, "oblast", "2026-01-01", "2026-01-02"),
        ("Kyiv City", None, "oblast", "2026-01-01", "2026-01-02"),
    ]), _universe())
    assert set(records.raion_id) == {
        "cherkaska/umanskyi", "cherkaska/cherkaskyi", "kyiv-city/kyiv-city",
    }
    assert records.level.eq("oblast").all()
    assert str(records.started_at.dtype) == "datetime64[us, UTC]"


def test_hromada_maps_to_parent_and_keeps_duplicate_source_rows() -> None:
    row = ("Cherkaska oblast", "Umanskyi raion", "hromada", "2026-01-01", "2026-01-02")
    records = expand_records(_source([row, row]), _universe())
    assert records.raion_id.tolist() == ["cherkaska/umanskyi", "cherkaska/umanskyi"]
    assert records.level.tolist() == ["hromada", "hromada"]


def test_excluded_oblasts_produce_no_universe_units_or_records() -> None:
    source = _source([
        ("Luhanska oblast", "Svatovskyi raion", "raion", "2026-01-01", "2026-01-02"),
        ("Autonomous Republic of Crimea", None, "oblast", "2026-01-01", "2026-01-02"),
        ("Sevastopol City", None, "oblast", "2026-01-01", "2026-01-02"),
    ])
    assert build_raions(source).empty
    assert expand_records(source, _universe()).empty


def test_merge_uses_running_maximum_and_strict_gap_boundary() -> None:
    anchor = pd.Timestamp("2026-01-01", tz="UTC")
    intervals = [
        (0, 60), (10, 20), (50, 70),
        (70 + MERGE_GAP_MIN - 1, 80),
        (80 + MERGE_GAP_MIN, 90),
    ]
    records = pd.DataFrame({
        "raion_id": ["cherkaska/umanskyi"] * len(intervals),
        "oblast_id": ["cherkaska"] * len(intervals),
        "level": ["oblast", "raion", "hromada", "raion", "raion"],
        "started_at": [anchor + pd.Timedelta(minutes=start) for start, _ in intervals],
        "finished_at": [anchor + pd.Timedelta(minutes=end) for _, end in intervals],
    })
    episodes = merge_episodes(records.sample(frac=1, random_state=4))
    assert len(episodes) == 2
    assert episodes.start.tolist() == [
        anchor, anchor + pd.Timedelta(minutes=80 + MERGE_GAP_MIN),
    ]
    assert episodes.end.tolist() == [
        anchor + pd.Timedelta(minutes=80), anchor + pd.Timedelta(minutes=90),
    ]
    assert not episodes.censored.any()


def test_equal_starts_prefer_finer_level_and_null_end_censors_the_union() -> None:
    records = expand_records(_source([
        ("Cherkaska oblast", None, "oblast", "2026-01-01", "2026-01-01 01:00"),
        ("Cherkaska oblast", "Umanskyi raion", "raion", "2026-01-01", "2026-01-01 00:30"),
        ("Cherkaska oblast", "Umanskyi raion", "hromada", "2026-01-01", None),
        ("Cherkaska oblast", "Umanskyi raion", "raion", "2026-02-01", "2026-02-02"),
    ]), _universe())
    episodes = merge_episodes(records)
    u = episodes.loc[episodes.raion_id.eq("cherkaska/umanskyi")]
    assert len(u) == 1
    assert u.iloc[0].source_level == "hromada"
    assert u.iloc[0].censored
    assert pd.isna(u.iloc[0].end)
    other = episodes.loc[episodes.raion_id.eq("cherkaska/cherkaskyi")]
    assert len(other) == 1
    assert not other.iloc[0].censored
    assert str(episodes.end.dtype) == "datetime64[us, UTC]"
