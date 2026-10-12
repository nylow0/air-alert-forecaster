"""Check every A1 record against its episode and print three reproducible spot checks."""

import numpy as np
import pandas as pd

from airalert.config import MERGE_GAP_MIN
from airalert.data import load_episodes, load_raions, load_records


def main() -> None:
    records, episodes, raions = load_records(), load_episodes(), load_raions()
    assert episodes.end.isna().equals(episodes.censored)
    assert not episodes.duplicated(["raion_id", "start"]).any()
    assert set(records.raion_id) == set(raions.raion_id)
    assert raions.notna().all().all()
    for frame, columns in [
        (records, ["started_at", "finished_at"]), (episodes, ["start", "end"]),
    ]:
        for column in columns:
            assert str(frame[column].dtype) == "datetime64[us, UTC]"
    episode_groups = {key: group for key, group in episodes.groupby("raion_id", sort=False)}
    matched = np.empty(len(records), dtype=np.int64)
    for raion, rows in records.groupby("raion_id", sort=False):
        targets = episode_groups[raion]
        local = np.searchsorted(targets.start.array.asi8, rows.started_at.array.asi8,
                                side="right") - 1
        assert (local >= 0).all()
        chosen = targets.iloc[local].reset_index(drop=True)
        raw = rows.reset_index(drop=True)
        assert raw.started_at.ge(chosen.start).all()
        assert (raw.finished_at.le(chosen.end) | chosen.censored).all()
        matched[rows.index] = targets.index.to_numpy()[local]
        gaps = targets.start - targets.end.shift()
        assert gaps.iloc[1:].ge(pd.Timedelta(minutes=MERGE_GAP_MIN)).all()
    extents = records.assign(
        episode=matched, _censored=records.finished_at.isna()
    ).groupby("episode").agg(
        start=("started_at", "min"), end=("finished_at", "max"),
        censored=("_censored", "any"),
    )
    extents.loc[extents.censored, "end"] = pd.NaT
    pd.testing.assert_series_equal(extents.start.reset_index(drop=True), episodes.start,
                                    check_names=False)
    pd.testing.assert_series_equal(extents.end.reset_index(drop=True), episodes.end,
                                    check_names=False)
    print(f"Full-artifact invariants: {len(records):,} records contained; "
          f"all {len(episodes):,} extents match; "
          f"all adjacent gaps >= {MERGE_GAP_MIN} min; "
          "UTC us / censor / unique episode checks passed.")
    for raion in raions.raion_id.sample(3, random_state=42):
        rows = records.loc[records.raion_id.eq(raion)]
        counts = pd.Series(matched[rows.index]).value_counts()
        candidates = sorted(counts.index[(counts >= 2) & (counts <= 6)])
        selected = next(
            int(index) for index in candidates
            if rows.loc[matched[rows.index] == index].drop_duplicates().shape[0] >= 2
        )
        raw = rows.loc[matched[rows.index] == selected, ["level", "started_at", "finished_at"]]
        episode = episodes.loc[[selected], ["start", "end", "source_level", "censored"]]
        print(f"RAION {raion}\nRAW RECORDS\n{raw.to_string(index=False)}")
        print(f"EPISODE\n{episode.to_string(index=False)}")
        unique = raw.drop_duplicates().sort_values("started_at").reset_index(drop=True)
        gaps = unique.started_at - unique.finished_at.cummax().shift()
        print("Gaps to running end, minutes:",
              (gaps.dt.total_seconds() / 60).round(4).tolist())


if __name__ == "__main__":
    main()
