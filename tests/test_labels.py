"""Guard the open-left/closed-right onset target and snapshot censoring."""

import pandas as pd
import pytest

from airalert.config import HORIZONS
from airalert.labels import build_labels


@pytest.mark.parametrize("horizon", HORIZONS)
def test_label_edges_and_snapshot(horizon: int) -> None:
    t = pd.Timestamp("2026-06-10T12:00:00Z")
    delta = pd.Timedelta(hours=horizon)
    universe = pd.DataFrame({"raion_id": ["at-t", "at-edge", "after-edge", "quiet"]})
    episodes = pd.DataFrame({
        "raion_id": ["at-t", "at-edge", "after-edge"],
        "start": [t, t + delta, t + delta + pd.Timedelta(microseconds=1)],
    })
    labels = build_labels(episodes, [t], t + pd.Timedelta(days=2), raions=universe)
    assert labels.set_index("raion_id")[f"y_{horizon}"].to_dict() == {
        "at-t": 0, "at-edge": 1, "after-edge": 0, "quiet": 0,
    }
    edge = build_labels(episodes, [t], t + delta, raions=universe)
    pd.testing.assert_series_equal(edge[f"y_{horizon}"], labels[f"y_{horizon}"])
    censored = build_labels(
        episodes, [t], t + delta - pd.Timedelta(microseconds=1), raions=universe
    )
    assert censored[f"y_{horizon}"].isna().all()
    latest = build_labels(episodes, [t], t, raions=universe)
    assert latest[[f"y_{h}" for h in HORIZONS]].isna().all().all()
