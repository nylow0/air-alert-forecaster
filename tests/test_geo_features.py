"""Check the shipped geography, border interpretation and offline cache integrity."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pyproj import Geod, Transformer
from shapely.geometry import LineString, box
from shapely.ops import nearest_points

from airalert import config, geo_features


def test_published_features_cover_universe_and_geographic_orderings() -> None:
    frame = pd.read_csv(config.GEO_DIR / "static_features.csv")
    geo_features.validate(frame)
    universe = pd.read_csv(config.RAIONS_PATH)["raion_id"]
    assert len(frame) == 119
    assert set(frame["raion_id"]) == set(universe)
    assert list(frame.columns) == [
        "raion_id", "dist_russia_border_km", "dist_belarus_border_km",
        "dist_black_sea_km", "dist_azov_sea_km", "dist_occupied_crimea_km",
        "area_km2", "n_neighbors",
    ]
    indexed = frame.set_index("raion_id")
    # Three geographic orderings guard swapped targets and centroid-distance regressions.
    for column, ordered_ids in [
        ("dist_russia_border_km", [
            "kharkivska/kharkivskyi", "poltavska/poltavskyi", "zakarpatska/uzhhorodskyi",
        ]),
        ("dist_belarus_border_km", [
            "volynska/kovelskyi", "kyiv-city/kyiv-city", "odeska/odeskyi",
        ]),
        ("dist_azov_sea_km", [
            "donetska/mariupolskyi", "dnipropetrovska/dniprovskyi", "lvivska/lvivskyi",
        ]),
    ]:
        assert np.diff(indexed.loc[ordered_ids, column].to_numpy()).min() > 0
    assert indexed.loc["odeska/odeskyi", "dist_black_sea_km"] < 2
    assert indexed.loc["donetska/mariupolskyi", "dist_azov_sea_km"] < 2
    assert indexed.loc["khersonska/henicheskyi", "dist_occupied_crimea_km"] < 2
    assert 700 < indexed.loc["kyiv-city/kyiv-city", "area_km2"] < 1000
    adjacency = json.loads(config.ADJACENCY_PATH.read_text(encoding="utf-8"))
    assert indexed["n_neighbors"].to_dict() == {
        unit_id: float(len(neighbors)) for unit_id, neighbors in adjacency.items()
    }


def test_distance_uses_polygon_edges_and_agrees_with_geodesic() -> None:
    import shapely

    project = geo_features._projection()
    polygon = shapely.transform(box(35.0, 49.0, 35.1, 49.1), project.transform, interleaved=False)
    border = shapely.transform(
        LineString([(36.0, 48.8), (36.0, 49.5)]).segmentize(0.01),
        project.transform, interleaved=False,
    )
    distance = geo_features._distance_km([polygon], geo_features._segments(border))[0]
    first, second = nearest_points(polygon, border)
    unproject = Transformer.from_crs(project.target_crs, "EPSG:4326", always_xy=True)
    lon1, lat1 = unproject.transform(first.x, first.y)
    lon2, lat2 = unproject.transform(second.x, second.y)
    geodesic = Geod(ellps="WGS84").inv(lon1, lat1, lon2, lat2)[2] / 1000
    assert distance == pytest.approx(geodesic, rel=0.005)
    centroid_distance = geo_features._distance_km(
        [polygon.centroid], geo_features._segments(border)
    )[0]
    assert centroid_distance > distance + 2
    assert geo_features._distance_km([polygon], geo_features._segments(polygon.boundary))[0] == 0


def test_country_borders_exclude_crimea_dispute_and_water_indicators() -> None:
    lines = [LineString([(33, 46), (34, 46)]), LineString([(36, 50), (37, 50)])]
    features = [
        {
            "type": "Feature",
            "properties": {
                "ADM0_LEFT": "Russia", "ADM0_RIGHT": "Ukraine", "TYPE": "Land",
                "FEATURECLA": "Disputed (please verify)",
            },
            "geometry": lines[0].__geo_interface__,
        },
        {
            "type": "Feature",
            "properties": {
                "ADM0_LEFT": "Russia", "ADM0_RIGHT": "Ukraine", "TYPE": "Land",
                "FEATURECLA": "International boundary (verify)",
            },
            "geometry": lines[1].__geo_interface__,
        },
        {
            "type": "Feature",
            "properties": {
                "ADM0_LEFT": "Russia", "ADM0_RIGHT": "Ukraine", "TYPE": "Water Indicator",
                "FEATURECLA": "International boundary (verify)",
            },
            "geometry": lines[0].__geo_interface__,
        },
    ]
    assert geo_features._borders(features, "Russia").equals(lines[1])


def test_cached_source_never_uses_http_and_rejects_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    content = b'{"type":"FeatureCollection","features":[]}'
    source = geo_features.Source("cache-check", hashlib.sha256(content).hexdigest())
    cache = tmp_path / "geo" / f"natural-earth-v5.1.2-{source.name}.geojson"

    def unexpected_http(*args: object, **kwargs: object) -> None:
        raise AssertionError("Cached geometry must never make an HTTP request")

    monkeypatch.setattr(geo_features.httpx, "Client", unexpected_http)
    with pytest.raises(FileNotFoundError, match="build once"):
        geo_features._source(source, offline=True)
    cache.parent.mkdir()
    cache.write_bytes(content)
    assert geo_features._source(source, offline=False) == []
    cache.write_bytes(content + b"corrupted")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        geo_features._source(source, offline=True)
