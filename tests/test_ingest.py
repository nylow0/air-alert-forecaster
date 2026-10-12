"""Guard source decoding, both live colors, preservation on failure and day commits."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest

from airalert import ingest
from airalert.config import CROSSWALK_PATH, REPO_ROOT
from airalert.data import load_raions
from airalert.sources import aiu_api, aiu_web, mapping, siren, ubilling
from airalert.sources.locations import DISTRICT_IDS, OBLAST_IDS, Locations
from airalert.sources.storage import atomic_json, cache_response


def _sample(name: str) -> dict[str, object]:
    return mapping(json.loads(
        (REPO_ROOT / "docs/research/samples" / name).read_text(encoding="utf-8")
    ))


@pytest.fixture
def locations() -> Locations:
    """Use deterministic tree structure; compare its reviewed IDs to actual CSV geography."""
    states: list[dict[str, object]] = []
    for uid, oblast in OBLAST_IDS.items():
        children: list[dict[str, object]] = []
        for district_uid, rid in DISTRICT_IDS.items():
            if rid.split("/")[0] != oblast:
                continue
            district: dict[str, object] = {
                "regionId": district_uid, "regionName": rid,
                "regionType": "District", "regionChildIds": [],
            }
            if district_uid == "122":
                district["regionChildIds"] = [{
                    "regionId": "1313", "regionName": "Вовчанська територіальна громада",
                    "regionType": "Community",
                }]
            elif district_uid == "47":
                district["regionName"] = "Нікопольський район"
                district["regionChildIds"] = [{
                    "regionId": "353", "regionName": "Покровська територіальна громада",
                    "regionType": "Community",
                }]
            children.append(district)
        states.append({"regionId": uid, "regionName": oblast, "regionType": "State",
                       "regionChildIds": children})
    return Locations({"states": states}, load_raions())


def test_real_compact_sample_and_war_day_formula() -> None:
    sample = _sample("r3_web_day_1657.json")
    dictionary: list[object] = [{"title": "unused", "type": "unknown"}] * 174
    dictionary[130] = {"title": "Луганська область", "type": "oblast"}
    dictionary[172] = {"title": "Автономна Республіка Крим", "type": "oblast"}
    dictionary[2] = {"title": "Харківська область", "type": "oblast"}
    payload = {"meta": mapping(sample["metadata"])["meta"], "alerts": sample["sample_rows"]}
    events = aiu_web.decode(payload, dictionary)
    assert events[0].started_at == datetime(2022, 4, 4, 16, 45, 39, tzinfo=UTC)
    assert events[0].finished_at is None
    assert events[0].levels[0].severity == "unknown"
    assert not events[0].severity_explicit
    assert events[2].hazard == "artillery_shelling"
    assert events[3].hazard == "urban_fights"
    assert aiu_web.war_day(date(2026, 9, 5)) == 1655
    assert aiu_web.war_day(date(2026, 10, 11)) == 1691
    # One complete real row from the August 20 cached response, not a synthesized end.
    complete = {"u": 147204189, "s": 147172664, "i": 248106, "ni": 0,
                "t": "o", "src": 3, "f": 147204181, "luid": 31}
    dictionary[0] = {"title": "м. Київ", "type": "city"}
    event = aiu_web.decode({"meta": {"type": "compact"}, "alerts": [complete]}, dictionary)[0]
    assert event.finished_at == aiu_web.compact_time(147204181)
    assert event.origin_uid == "31" and event.upstream_source == 3
    yellow = aiu_web.decode({"meta": {"type": "compact"}, "alerts": [{**complete, "al": 1}]},
                            dictionary)[0]
    assert yellow.levels[0].severity == "yellow" and yellow.severity_explicit
    red = aiu_web.decode({"meta": {"type": "compact"}, "alerts": [{**complete, "al": 0}]},
                        dictionary)[0]
    assert red.levels[0].severity == "red" and red.severity_explicit
    alarm = _sample("r3_siren_history_122.json")["sample_rows"][0]["alarms"][0]
    assert siren.decode_history_end(alarm) is None


def test_aliases_cover_the_fixed_119_units(locations: Locations) -> None:
    mapped = {rid for loc in locations.by_uid.values() for rid in loc.raion_ids}
    assert mapped == set(load_raions().raion_id)
    assert len(mapped) == 119
    assert locations.resolve(name="Шептицький район").raion_ids == ("lvivska/chervonohradskyi",)
    assert locations.resolve(name="Самарівський район").raion_ids == (
        "dnipropetrovska/samarivskyi",
    )
    assert locations.resolve(name="Звягельський район").raion_ids == ("zhytomyrska/zviahelskyi",)
    assert locations.resolve("29").oblast_id == "autonomous-republic-of-crimea"


def test_real_red_and_yellow_share_one_alert_and_both_layers(locations: Locations) -> None:
    events = siren.decode(_sample("r3_siren_active.json")["sample_rows"])
    pokrov = [event for event in events if event.origin_uid == "56"]
    assert len(pokrov) == 1
    records = ingest.event_records(pokrov, locations)
    assert len(records) == 1
    polygons = ingest.polygon_state(
        events, locations, pd.read_csv(CROSSWALK_PATH, dtype=str),
        datetime(2026, 10, 12, tzinfo=UTC),
    )
    assert len(polygons) == 139
    row = next(polygon for polygon in polygons if polygon["raion_id"] == "donetska/pokrovskyi")
    assert row["red"]["active"] and row["yellow"]["active"]
    assert row["yellow"]["since"] == "2026-10-11T21:39:06.711045Z"
    chornobyl = next(
        row for row in polygons if row["oblast_id"] == "kyivska" and not row["raion_id"]
    )
    assert chornobyl["coverage"] == "unknown" and chornobyl["red"] is None
    oblast_alert = next(event for event in events if event.origin_uid == "16")
    kyivska = replace(oblast_alert, event_id="14:air_raid", origin_uid="14", covered_uid="14")
    inherited = ingest.polygon_state([kyivska], locations, pd.read_csv(CROSSWALK_PATH, dtype=str),
                                    datetime(2026, 10, 12, tzinfo=UTC))
    chornobyl = next(
        row for row in inherited if row["oblast_id"] == "kyivska" and not row["raion_id"]
    )
    assert chornobyl["coverage"] == "inherited" and chornobyl["red"]["active"]
    city = replace(oblast_alert, event_id="31:air_raid", origin_uid="31", covered_uid="31")
    city_state = ingest.polygon_state(
        [city], locations, pd.read_csv(CROSSWALK_PATH, dtype=str),
        datetime(2026, 10, 12, tzinfo=UTC),
    )
    kyiv_city = next(row for row in city_state if row["raion_id"] == "kyiv-city/kyiv-city")
    assert kyiv_city["coverage"] == "observed" and kyiv_city["red"]["active"]


def test_supported_api_proxy_preserves_scalar_threat_conflict(locations: Locations) -> None:
    events = aiu_api.decode(_sample("r3_ubilling_aiu_raw.json"))
    mixed = next(event for event in events if event.event_id == "262473")
    assert {level.severity for level in mixed.levels} == {"red", "yellow"}
    assert not mixed.levels[0].time_known
    # The sample's bogus location_oblast_uid is deliberately never used for parents.
    assert mixed.origin_uid == "353" and mixed.parent_name == "Нікопольський район"
    records = ingest.event_records(events, locations)
    assert len(records) == 1
    assert records.iloc[0].raion_id == "dnipropetrovska/nikopolskyi"
    assert records.iloc[0].severity == "red"


@pytest.mark.parametrize("failed_source", ["ubilling", "aiu_api", "siren"])
def test_failed_poll_keeps_layers_and_successful_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locations: Locations,
    failed_source: str,
) -> None:
    now = datetime(2026, 10, 12, 15, tzinfo=UTC)
    monkeypatch.setattr(ingest, "_locations", lambda root: locations)
    monkeypatch.setattr(ingest, "PROCESSED_DIR", tmp_path / "processed")
    monkeypatch.setattr(aiu_api, "token_from_env", lambda dotenv: "test-token")
    api = _sample("r3_ubilling_aiu_raw.json")
    monkeypatch.setattr(aiu_api, "fetch_alerts", lambda token: (api, json.dumps(api).encode()))
    sample = _sample("r3_siren_active.json")["sample_rows"]
    monkeypatch.setattr(siren, "fetch_alerts", lambda: (sample, json.dumps(sample).encode()))
    secondary = _sample("r3_ubilling_default.json")
    monkeypatch.setattr(ubilling, "fetch_alerts",
                        lambda: (secondary, json.dumps(secondary).encode()))
    before = ingest.live_state(now, root=tmp_path)
    assert len(list((tmp_path / "observations").glob("*.parquet"))) == 1
    assert before["stale"] is False
    api_unit = next(p for p in before["polygons"]
                    if p["raion_id"] == "dnipropetrovska/nikopolskyi")
    assert api_unit["red"]["active"] and api_unit["yellow"]["active"]
    sevastopol = next(p for p in before["polygons"] if p["oblast_id"] == "sevastopol-city")
    assert sevastopol["coverage"] == "inherited"
    assert sevastopol["red"] == {"active": True, "since": "2022-12-10T22:22:00Z"}
    assert sevastopol["yellow"] is None

    def fail() -> tuple[object, bytes]:
        raise TimeoutError("source unavailable")

    if failed_source == "aiu_api":
        monkeypatch.setattr(aiu_api, "fetch_alerts", lambda token: fail())
    else:
        monkeypatch.setattr(ubilling if failed_source == "ubilling" else siren,
                            "fetch_alerts", fail)
    secondary_failed = ingest.live_state(now + timedelta(seconds=30), root=tmp_path)
    sevastopol = next(
        p for p in secondary_failed["polygons"] if p["oblast_id"] == "sevastopol-city"
    )
    assert sevastopol["coverage"] == "inherited"
    assert sevastopol["red"] == {"active": True, "since": "2022-12-10T22:22:00Z"}
    assert secondary_failed["stale"] and secondary_failed["sources"][failed_source] == "failed"
    assert secondary_failed["fetched_at"] == before["fetched_at"]
    assert len(list((tmp_path / "observations").glob("*.parquet"))) == 1
    for old, new in zip(before["polygons"], secondary_failed["polygons"], strict=True):
        assert new["red"] == old["red"] and new["yellow"] == old["yellow"]
    before = secondary_failed
    monkeypatch.setattr(siren, "fetch_alerts", fail)
    after = ingest.live_state(now + timedelta(minutes=1), root=tmp_path)
    assert after["stale"] is True and after["fetched_at"] == before["fetched_at"]
    for old, new in zip(before["polygons"], after["polygons"], strict=True):
        assert new["stale"] is True
        assert new["red"] == old["red"] and new["yellow"] == old["yellow"]
        assert new["fetched_at"] == old["fetched_at"]
    transitions = pd.read_parquet(tmp_path / "processed/severity_transitions.parquet")
    assert transitions.loc[transitions.source.eq("siren"), "alert_start"].isna().all()
    assert {"red", "yellow"}.issubset(set(transitions.severity))


@pytest.fixture
def reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locations: Locations,
) -> Path:
    processed = tmp_path / "processed"
    processed.mkdir()
    monkeypatch.setattr(ingest, "PROCESSED_DIR", processed)
    monkeypatch.setattr(ingest, "SNAPSHOT_PATH", processed / "snapshot.json")
    monkeypatch.setattr(ingest, "RECORDS_PATH", processed / "records.parquet")
    monkeypatch.setattr(ingest, "EPISODES_PATH", processed / "episodes.parquet")
    monkeypatch.setattr(ingest, "_locations", lambda root: locations)
    monkeypatch.setattr(aiu_web, "fetch_dictionary", lambda root: [])
    monkeypatch.setattr(ingest, "_csv_sha256", lambda: "fixture")
    source = pd.DataFrame([{
        "oblast": "Kyiv City", "raion": None, "level": "oblast",
        "started_at": "2026-10-24T01:00:00Z", "finished_at": "2026-10-24T02:00:00Z",
    }])
    monkeypatch.setattr(ingest, "load_source", lambda: source)
    atomic_json(ingest.SNAPSHOT_PATH, {"snapshot": "2026-10-24T02:00:00Z",
                                     "csv_sha256": "fixture", "regime_switch": "2025-08-14"})
    gate_days = [{"day": f"2026-08-{day:02}", "matched": 100,
                  "csv_starts": 100, "archive_starts": 100} for day in range(1, 13)]
    atomic_json(tmp_path / "validation.json", {
        "passed": True, "days": gate_days, "csv_sha256": "fixture",
        "metric": "episode_starts", "normalization_window_seconds": 120,
    })
    atomic_json(tmp_path / "ledger.json", {
        "cutover": "2026-10-24T21:00:00Z", "watermark": "2026-10-24", "days": [], "gaps": [],
    })

    def fetch(day: date, root: Path, **kwargs: object) -> object:
        start = datetime(2026, 10, 24, 21, 30, tzinfo=UTC)
        finish = datetime(2026, 10, 26, 1, tzinfo=UTC)
        row = {"i": 1, "luid": 31, "t": "o", "s": int(start.timestamp()),
               "u": int(start.timestamp()), "al": 1}
        if day >= date(2026, 10, 26):
            row.update({"f": int(finish.timestamp()), "u": int(finish.timestamp())})
        return {"meta": {"type": "compact"}, "alerts": [{**row, "i": i} for i in range(100)]}

    monkeypatch.setattr(aiu_web, "fetch_day", fetch)
    return tmp_path


@pytest.mark.parametrize("failure", [
    "empty", "truncated", "missing_live_alert", "missing_live_interval",
])
def test_archive_completeness_blocks_bad_days_and_retries(
    reconciliation: Path, monkeypatch: pytest.MonkeyPatch, locations: Locations, failure: str,
) -> None:
    """The EMPTY_ARCHIVE probe must stop at the first day, then recover without intervention."""
    day = date(2026, 10, 25)
    now = datetime(2026, 10, 27, tzinfo=UTC)
    original_fetch = aiu_web.fetch_day
    healthy = original_fetch(day, reconciliation)
    ledger_path = reconciliation / "ledger.json"
    ledger = json.loads(ledger_path.read_text())
    if failure == "truncated":
        # A 100-row response clears the absolute floor but not 20% of this robust baseline.
        days = [(day - timedelta(days=i)).isoformat() for i in range(14, 0, -1)]
        ledger.update({"days": days, "cutover": ingest._iso(aiu_web.day_bounds(
            date.fromisoformat(days[0]))[0])})
        frame = ingest.event_records(aiu_web.decode(healthy, []), locations)
        directory = reconciliation / "normalized"
        directory.mkdir()
        for index, prior in enumerate(days):
            count = 10_000 if index == 0 else 1_000
            pd.concat([frame] * (count // len(frame)), ignore_index=True).assign(
                origin_event_id=[str(i) for i in range(count)]
            ).to_parquet(directory / f"{prior}.parquet", index=False)
        atomic_json(ledger_path, ledger)
    elif failure in {"missing_live_alert", "missing_live_interval"}:
        event = next(e for e in siren.decode(_sample("r3_siren_active.json")["sample_rows"])
                     if e.origin_uid == "56")
        event = replace(event, started_at=datetime(2026, 10, 25, 1, tzinfo=UTC))
        observed = ingest.event_records([event], locations)
        observed["observed_at"] = pd.Timestamp("2026-10-25T01:30:00Z")
        directory = reconciliation / "observations"
        directory.mkdir()
        observed.to_parquet(directory / "20261025T013000Z.parquet", index=False)
    before_snapshot = ingest.SNAPSHOT_PATH.read_bytes()
    requested: list[date] = []
    refreshed: list[bool] = []
    repaired = False

    def fetch(candidate: date, root: Path, **kwargs: object) -> object:
        requested.append(candidate)
        refreshed.append(bool(kwargs.get("refresh")))
        if repaired:
            rows = [{**healthy["alerts"][0], "i": i} for i in range(400)]
            rows.append({"i": 99999, "luid": 56, "t": "r", "s": 1792890000,
                         "u": 1792890000})  # 2026-10-25 01:00Z, before the live receipt.
            return {"meta": {"type": "compact"}, "alerts": rows}
        rows = [] if failure == "empty" else list(healthy["alerts"])
        if failure == "missing_live_interval":
            rows.append({"i": 99999, "luid": 56, "t": "r", "s": 1792886400,
                         "f": 1792889940, "u": 1792889940})  # Ends 31m before the receipt.
        return {"meta": {"type": "compact"}, "alerts": rows}

    monkeypatch.setattr(aiu_web, "fetch_day", fetch)
    with pytest.raises(ValueError, match="Archive completeness"):
        ingest.catch_up(now, root=reconciliation)
    failed = json.loads(ledger_path.read_text())
    assert failed["watermark"] == "2026-10-24" and failed["days"] == ledger["days"]
    assert requested == [day]
    assert failed["gaps"][0]["day"] == day.isoformat()
    assert failed["gaps"][0]["start"] == "2026-10-24T21:00:00Z"
    assert not (reconciliation / "normalized/2026-10-25.parquet").exists()
    assert ingest.SNAPSHOT_PATH.read_bytes() == before_snapshot
    repaired = True
    recovered = ingest.reconcile_days([day], root=reconciliation, now=now)
    assert refreshed[-1] and recovered["watermark"] == day.isoformat()
    assert recovered["gaps"] == []


@pytest.mark.parametrize("shared_file", ["hazards.parquet", "severity_transitions.parquet"])
def test_poll_rows_survive_interleaved_nightly_publication(
    reconciliation: Path, monkeypatch: pytest.MonkeyPatch, locations: Locations, shared_file: str,
) -> None:
    """Pause nightly after its shared-file read, as in the audit's LOST_UPDATE probe."""
    ingest.reconcile_days([date(2026, 10, 25)], root=reconciliation,
                          now=datetime(2026, 10, 27, tzinfo=UTC))
    ledger = json.loads((reconciliation / "ledger.json").read_text())
    events = siren.decode(_sample("r3_siren_active.json")["sample_rows"])
    air = next(e for e in events if e.origin_uid == "56")
    hazard = replace(air, event_id="probe-hazard", hazard="artillery_shelling")
    observed = ingest.event_records([hazard], locations)
    read = pd.read_parquet
    nightly_read, resume, poll_started, poll_finished = (threading.Event() for _ in range(4))
    nightly_ident: list[int] = []

    def paused_read(path: Path, *args: object, **kwargs: object) -> pd.DataFrame:
        frame = read(path, *args, **kwargs)
        if Path(path).name == shared_file and threading.get_ident() in nightly_ident:
            nightly_read.set()
            assert resume.wait(5), "nightly probe was not released"
        return frame

    def nightly() -> None:
        nightly_ident.append(threading.get_ident())
        ingest._publish(ledger, reconciliation)

    def poll() -> None:
        poll_started.set()
        ingest._save_hazards(observed)
        ingest._save_level_observations([air], locations, datetime(2026, 10, 25, tzinfo=UTC))
        poll_finished.set()

    monkeypatch.setattr(pd, "read_parquet", paused_read)
    with ThreadPoolExecutor(max_workers=2) as workers:
        publishing = workers.submit(nightly)
        try:
            assert nightly_read.wait(5)
            polling = workers.submit(poll)
            assert poll_started.wait(5)
            # Old code lets the poll finish here, then erases it. A shared lock defers it.
            poll_finished.wait(0.5)
        finally:
            resume.set()
        publishing.result(timeout=5)
        polling.result(timeout=5)
    expected = 1 if shared_file == "hazards.parquet" else len(air.levels)
    assert len(read(ingest.PROCESSED_DIR / shared_file)) == expected
    assert len(read(ingest.PROCESSED_DIR / "hazards.parquet")) == 1
    assert len(read(ingest.PROCESSED_DIR / "severity_transitions.parquet")) == len(air.levels)


@pytest.mark.parametrize("legacy_bad_cache", [False, True])
def test_schema_invalid_archive_is_retried_after_upstream_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_bad_cache: bool,
) -> None:
    """A schema-invalid HTTP 200 must not poison normal cache-based catch-up forever."""
    day = date(2026, 10, 25)
    path = tmp_path / f"{day.isoformat()}.json.gz"
    invalid = {"meta": {"type": "compact"}, "alerts": [{"i": 1, "luid": 31}]}
    valid = {"meta": {"type": "compact"}, "alerts": [
        {"i": 1, "luid": 31, "s": 1792890000, "u": 1792890000}
    ]}
    responses = iter([invalid, valid])
    requests: list[str] = []

    def get(url: str, **kwargs: object) -> httpx.Response:
        requests.append(url)
        return httpx.Response(200, json=next(responses), request=httpx.Request("GET", url))

    monkeypatch.setattr(aiu_web.httpx, "get", get)
    monkeypatch.setattr(aiu_web.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(aiu_web, "fetch_dictionary", lambda root: [])
    if legacy_bad_cache:
        cache_response(path, json.dumps(invalid).encode())
    with pytest.raises(ValueError, match="valid start/update"):
        aiu_web.decode(aiu_web.fetch_day(day, tmp_path), [])
    assert path.exists() == legacy_bad_cache
    events = aiu_web.decode(aiu_web.fetch_day(day, tmp_path), [])
    assert len(events) == 1 and len(requests) == 2 and path.exists()


def test_catch_up_commits_every_complete_day_across_kyiv_dst(reconciliation: Path) -> None:
    start, end = aiu_web.day_bounds(date(2026, 10, 25))
    assert start == datetime(2026, 10, 24, 21, tzinfo=UTC)
    assert end == datetime(2026, 10, 25, 22, tzinfo=UTC)
    assert end - start == timedelta(hours=25)
    ledger = ingest.catch_up(datetime(2026, 10, 27, tzinfo=UTC), root=reconciliation)
    assert ledger["watermark"] == "2026-10-26"
    assert ledger["days"] == ["2026-10-25", "2026-10-26"]
    records = pd.read_parquet(ingest.RECORDS_PATH)
    assert len(records.loc[records.source.eq("aiu_web")]) == 1
    assert records.loc[records.source.eq("aiu_web"), "finished_at"].iloc[0] == pd.Timestamp(
        "2026-10-26T01:00:00Z"
    )
    assert json.loads(ingest.SNAPSHOT_PATH.read_text())["snapshot"] == "2026-10-26T22:00:00Z"


def test_reconcile_is_idempotent_and_ongoing_ends_are_never_synthesized(
    reconciliation: Path,
) -> None:
    now = datetime(2026, 10, 27, tzinfo=UTC)
    ingest.reconcile_days([date(2026, 10, 25)], root=reconciliation, now=now)
    first = pd.read_parquet(ingest.RECORDS_PATH)
    assert first.loc[first.source.eq("aiu_web"), "finished_at"].isna().all()
    ingest.reconcile_days([date(2026, 10, 25)], root=reconciliation, now=now)
    pd.testing.assert_frame_equal(first, pd.read_parquet(ingest.RECORDS_PATH))
    ingest.catch_up(now, root=reconciliation)
    before = pd.read_parquet(ingest.RECORDS_PATH)
    episodes = pd.read_parquet(ingest.EPISODES_PATH)
    ingest.reconcile_days(
        [date(2026, 10, 25), date(2026, 10, 26)], root=reconciliation, now=now
    )
    pd.testing.assert_frame_equal(before, pd.read_parquet(ingest.RECORDS_PATH))
    pd.testing.assert_frame_equal(episodes, pd.read_parquet(ingest.EPISODES_PATH))


def test_publish_failure_keeps_watermark_and_records_gap(
    reconciliation: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(ledger: dict[str, object], root: Path) -> None:
        raise OSError("disk write failed")

    monkeypatch.setattr(ingest, "_publish", fail)
    with pytest.raises(OSError):
        ingest.catch_up(datetime(2026, 10, 27, tzinfo=UTC), root=reconciliation)
    ledger = json.loads((reconciliation / "ledger.json").read_text())
    assert ledger["watermark"] == "2026-10-24" and ledger["days"] == []
    assert ledger["gaps"][0]["day"] == "2026-10-25"
    assert ledger["gaps"][0]["start"] == "2026-10-24T21:00:00Z"
    assert ledger["gaps"][0]["end"] == "2026-10-25T22:00:00Z"


def test_archive_gate_blocks_catch_up_before_any_ledger_mutation(tmp_path: Path) -> None:
    atomic_json(tmp_path / "validation.json", {"passed": False})
    with pytest.raises(RuntimeError, match="gate has not passed"):
        ingest.catch_up(datetime(2026, 10, 12, tzinfo=UTC), root=tmp_path)
    assert not (tmp_path / "ledger.json").exists()


def test_archive_collapse_keeps_earliest_finest_and_open_end(locations: Locations) -> None:
    start = datetime(2026, 10, 11, 1, tzinfo=UTC)
    rows = [{"i": i, "luid": 31, "s": int((start + timedelta(seconds=seconds)).timestamp()),
             "u": int((start + timedelta(hours=1)).timestamp()), "al": 1}
            for i, seconds in enumerate((0, 120, 121), start=1)]
    for i, row in enumerate(rows):
        if i != 1:
            row["f"] = int((start + timedelta(minutes=10 + i)).timestamp())
    events = aiu_web.decode({"meta": {"type": "compact"}, "alerts": rows}, [])
    records = ingest.event_records(events, locations)
    records["level"] = ["oblast", "hromada", "raion"]
    collapsed = ingest.normalize_archive_records(records)
    assert len(collapsed) == 2
    assert collapsed.iloc[0].started_at == pd.Timestamp(start)
    assert collapsed.iloc[0].finished_at is pd.NaT
    assert collapsed.iloc[0].level == "hromada" and collapsed.iloc[0].source == "aiu_web"
    assert collapsed.iloc[1].started_at == pd.Timestamp(start + timedelta(seconds=121))
    records.loc[1, "finished_at"] = pd.Timestamp(start + timedelta(minutes=20))
    closed = ingest.normalize_archive_records(records)
    assert closed.iloc[0].finished_at == pd.Timestamp(start + timedelta(minutes=20))
    other = records.iloc[[0]].copy()
    other["raion_id"] = "kyivska/boryspilskyi"
    assert len(ingest.normalize_archive_records(pd.concat([records, other]))) == 3


def test_episode_gate_uses_merged_starts_and_weighted_aggregate_floor() -> None:
    start = pd.Timestamp("2026-08-20T01:00:00Z")
    csv = pd.DataFrame({"raion_id": ["kyiv-city/kyiv-city"], "oblast_id": ["kyiv-city"],
                        "level": ["oblast"], "started_at": [start],
                        "finished_at": [start + pd.Timedelta(minutes=30)]})
    archive = pd.concat([csv, csv.assign(started_at=start + pd.Timedelta(minutes=31),
                                       finished_at=start + pd.Timedelta(minutes=45))])
    assert len(ingest._matched_starts(csv, archive)[1]) == 1
    from airalert.data import merge_episodes

    left = merge_episodes(csv).rename(columns={"start": "started_at"})
    right = merge_episodes(archive).rename(columns={"start": "started_at"})
    missing, extra, pairs = ingest._matched_starts(left, right)
    assert len(pairs) == 1 and missing.empty and extra.empty
    midnight_csv = left.assign(started_at=pd.Timestamp("2026-08-19T21:01:02Z"))
    midnight_archive = right.assign(started_at=pd.Timestamp("2026-08-19T20:59:42Z"))
    missing, extra, pairs = ingest._matched_starts(midnight_csv, midnight_archive)
    assert len(pairs) == 1 and missing.empty and extra.empty
    days = [{"csv_starts": 100, "archive_starts": 100, "matched": 100} for _ in range(12)]
    days[0]["matched"] = 88
    assert ingest._gate_summary(days)["passed"]
    days[0]["archive_starts"] = 101
    assert not ingest._gate_summary(days)["passed"]
    days[0]["archive_starts"] = 100
    days[0]["matched"] = 100
    days[0]["archive_matched"] = 87
    assert not ingest._gate_summary(days)["passed"]
    del days[0]["archive_matched"]
    days[0]["matched"] = 87
    assert not ingest._gate_summary(days)["passed"]
    days = [{"csv_starts": 1, "archive_starts": 1, "matched": 1} for _ in range(12)]
    days[0] = {"csv_starts": 1_000, "archive_starts": 1_000, "matched": 940}
    summary = ingest._gate_summary(days)
    assert summary["totals"]["recall"] == 951 / 1_011
    assert not summary["passed"]
    days[0]["csv_starts"] = 940
    summary = ingest._gate_summary(days)
    assert summary["totals"]["recall"] == 1
    assert not summary["passed"]
