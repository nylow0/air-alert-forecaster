"""Build fixed polygon geography for the optional M2a ``f_geo_*`` join.

Run ``uv run python -m airalert.geo_features`` once to cache the pinned public
sources. Subsequent builds, including ``--offline``, never request fresh data.
"""

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx
import matplotlib
import numpy as np
import pandas as pd
import shapely
from pyproj import Geod, Transformer
from shapely.geometry import LineString, MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from airalert import config

type Json = str | int | float | bool | None | list[Json] | dict[str, Json]


@dataclass(frozen=True)
class Source:
    """An immutable Natural Earth download; the digest also guards offline caches."""

    name: str
    sha256: str

    @property
    def url(self) -> str:
        return (
            "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
            f"v5.1.2/geojson/{self.name}.geojson"
        )


SOURCES = (
    Source(
        "ne_10m_admin_0_boundary_lines_land",
        "74d9c16229c095fde65943a9919e337682f044bcebccb120764f38edf3b70f4a",
    ),
    Source(
        "ne_10m_geography_marine_polys",
        "53f865e8ffa966cdd402145c82c5cd14ee7ce974cd0eb9a3f59f03a4cfd2d66c",
    ),
    Source(
        "ne_50m_coastline",
        "271f1c4c1908312bac6b29d158ea1356544beafc129f260005300913aa5ea283",
    ),
)


def _features(path: Path) -> list[dict[str, Json]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["type"] != "FeatureCollection":
        raise ValueError(f"Expected a FeatureCollection: {path.name}")
    return cast(list[dict[str, Json]], data["features"])


def _source(source: Source, *, offline: bool) -> list[dict[str, Json]]:
    cache = config.RAW_DIR / "geo" / f"natural-earth-v5.1.2-{source.name}.geojson"
    if cache.exists():
        content = cache.read_bytes()
    else:
        if offline:
            raise FileNotFoundError(f"Missing {cache.name}; build once without --offline")
        with httpx.Client(
            follow_redirects=True,
            timeout=120,
            headers={"User-Agent": "AirAlertForecaster/static-geography"},
        ) as client:
            response = client.get(source.url)
            response.raise_for_status()
            content = response.content
    if hashlib.sha256(content).hexdigest() != source.sha256:
        raise ValueError(f"SHA256 mismatch: {cache.name}")
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(content)
    return _features(cache)


def _geometry(feature: dict[str, Json]) -> BaseGeometry:
    geometry = shape(cast(dict[str, object], feature["geometry"]))
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError("Empty or invalid source geometry")
    return geometry


def _polygons() -> tuple[dict[str, BaseGeometry], BaseGeometry]:
    """Join raw shape IDs through the published crosswalk, with a map-only fallback."""
    crosswalk = pd.read_csv(config.CROSSWALK_PATH, keep_default_na=False)
    if crosswalk["shape_id"].duplicated().any():
        raise ValueError("Duplicate shape_id in crosswalk")
    raw = config.RAW_DIR / "geo" / "gbHumanitarian-UKR-ADM2.geojson"
    if raw.exists():
        metadata = json.loads(raw.with_suffix(".metadata.json").read_text(encoding="utf-8"))
        if hashlib.sha256(raw.read_bytes()).hexdigest() != metadata["cached_sha256"]:
            raise ValueError("SHA256 mismatch: gbHumanitarian-UKR-ADM2.geojson")
        geometries = {
            cast(dict[str, str], feature["properties"])["shapeID"]: _geometry(feature)
            for feature in _features(raw)
        }
        if set(geometries) != set(crosswalk["shape_id"]):
            raise ValueError("Raw ADM2 and crosswalk shape IDs differ")
    else:
        # The map has canonical IDs for matched units, source names for unmatched ones.
        map_geometries: dict[tuple[str, str], BaseGeometry] = {}
        for feature in _features(config.RAIONS_GEOJSON_PATH):
            properties = cast(dict[str, str | None], feature["properties"])
            key = (
                str(properties["oblast_id"]),
                str(properties["raion_id"] or properties["name"]),
            )
            if key in map_geometries:
                raise ValueError(f"Duplicate map polygon: {key}")
            map_geometries[key] = _geometry(feature)
        geometries = {
            row.shape_id: map_geometries[(row.oblast_id, row.raion_id or row.shape_name)]
            for row in crosswalk.itertuples(index=False)
        }
    matched: dict[str, BaseGeometry] = {}
    crimea = []
    for row in crosswalk.itertuples(index=False):
        geometry = geometries[row.shape_id]
        if row.raion_id:
            if row.raion_id in matched:
                raise ValueError(f"Duplicate raion polygon: {row.raion_id}")
            matched[row.raion_id] = geometry
        if row.oblast_id in {"autonomous-republic-of-crimea", "sevastopol-city"}:
            crimea.append(geometry)
    if not crimea:
        raise ValueError("No Crimea polygons in crosswalk")
    return matched, shapely.union_all(crimea)


def _projection() -> Transformer:
    """Ukraine-centred Lambert conformal conic, metres, parallels 46 and 52 N."""
    return Transformer.from_crs(
        "EPSG:4326",
        "+proj=lcc +lat_1=46 +lat_2=52 +lat_0=49 +lon_0=31 +datum=WGS84 +units=m",
        always_xy=True,
    )


def _segments(geometry: BaseGeometry) -> np.ndarray:
    """Short projected segments let the STRtree prune distant border/coast sections."""
    arrays = [
        np.stack((coordinates[:-1], coordinates[1:]), axis=1)
        for part in shapely.get_parts(geometry)
        if isinstance(part, LineString)
        and len(coordinates := np.asarray(part.coords)) > 1
    ]
    if not arrays:
        raise ValueError("Source has no line segments")
    return shapely.linestrings(np.concatenate(arrays))


def _borders(features: list[dict[str, Json]], country: str) -> BaseGeometry:
    """All the country's international land borders, including river frontiers.

    Exclude disputed lines and water indicators. In particular, Natural Earth's
    disputed Crimea/Kherson line is never treated as Russia's national border.
    """
    lines = []
    for feature in features:
        properties = cast(dict[str, str], feature["properties"])
        if (
            country in {properties["ADM0_LEFT"], properties["ADM0_RIGHT"]}
            and properties["TYPE"] == "Land"
            and properties["FEATURECLA"] == "International boundary (verify)"
        ):
            lines.append(_geometry(feature))
    if not lines:
        raise ValueError(f"No international land border for {country}")
    return shapely.union_all(lines)


def _coasts(
    marine: list[dict[str, Json]],
    coastline: list[dict[str, Json]],
    project: Transformer,
) -> dict[str, np.ndarray]:
    """Classify actual coastline segments by the nearest named marine polygon.

    Marine polygons select seas only. Their open-water delimiters, including the
    Kerch Strait divider, are never used as coast. A 10 km matching tolerance
    accommodates the generalized marine outlines without moving the coastline.
    """
    seas = {
        str(properties["name"]): _geometry(feature)
        for feature in marine
        if (properties := cast(dict[str, str], feature["properties"]))["name"]
        in {"Black Sea", "Sea of Azov"}
    }
    if set(seas) != {"Black Sea", "Sea of Azov"}:
        raise ValueError("Both named seas are required")
    envelope = shapely.union_all(list(seas.values())).envelope.buffer(0.2)
    coast = shapely.union_all([
        geometry.intersection(envelope)
        for feature in coastline
        if (geometry := _geometry(feature)).intersects(envelope)
    ])
    segments = _segments(shapely.transform(
        coast.segmentize(0.01), project.transform, interleaved=False
    ))
    midpoints = shapely.line_interpolate_point(segments, 0.5, normalized=True)
    names = ["Black Sea", "Sea of Azov"]
    distances = np.stack([
        shapely.distance(
            midpoints, shapely.transform(seas[name], project.transform, interleaved=False)
        ) for name in names
    ])
    labels = distances.argmin(axis=0)
    nearby = distances.min(axis=0) <= 10_000
    result = {name: segments[nearby & (labels == index)] for index, name in enumerate(names)}
    if any(len(lines) == 0 for lines in result.values()):
        raise ValueError("Missing sea coastline after classification")
    return result


def _distance_km(polygons: list[BaseGeometry], targets: np.ndarray) -> np.ndarray:
    if len(targets) == 0:
        raise ValueError("Empty distance target")
    indices, distances = STRtree(targets).query_nearest(
        polygons, return_distance=True, all_matches=False
    )
    if not np.array_equal(indices[0], np.arange(len(polygons))):
        raise ValueError("A raion has no nearest geometry")
    return distances / 1000


def compute(*, offline: bool = False) -> pd.DataFrame:
    """Return seven float features per universe ID, without any alert/time input."""
    universe = pd.read_csv(config.RAIONS_PATH)["raion_id"]
    unit_ids = sorted(universe)
    if len(unit_ids) != 119 or universe.duplicated().any():
        raise ValueError("Expected 119 unique universe raions")
    polygons, crimea = _polygons()
    adjacency = json.loads(config.ADJACENCY_PATH.read_text(encoding="utf-8"))
    centroids = pd.read_csv(config.CENTROIDS_PATH)["raion_id"]
    if any(set(values) != set(unit_ids) for values in (polygons, adjacency, centroids)):
        raise ValueError("Universe, polygon, adjacency and centroid IDs must match")
    land, marine, coastline = [_source(source, offline=offline) for source in SOURCES]
    project = _projection()
    projected = [
        shapely.transform(polygons[unit_id], project.transform, interleaved=False)
        for unit_id in unit_ids
    ]
    coasts = _coasts(marine, coastline, project)
    geod = Geod(ellps="WGS84")
    frame = pd.DataFrame({"raion_id": unit_ids})
    for country in ["Russia", "Belarus"]:
        border = shapely.transform(
            _borders(land, country).segmentize(0.01), project.transform, interleaved=False
        )
        frame[f"dist_{country.lower()}_border_km"] = _distance_km(projected, _segments(border))
    frame["dist_black_sea_km"] = _distance_km(projected, coasts["Black Sea"])
    frame["dist_azov_sea_km"] = _distance_km(projected, coasts["Sea of Azov"])
    projected_crimea = shapely.transform(crimea, project.transform, interleaved=False)
    frame["dist_occupied_crimea_km"] = np.where(
        shapely.intersects(projected, projected_crimea), 0.0,
        _distance_km(projected, _segments(projected_crimea.boundary)),
    )
    frame["area_km2"] = [
        abs(geod.geometry_area_perimeter(shapely.orient_polygons(polygons[unit_id]))[0]) / 1e6
        for unit_id in unit_ids
    ]
    frame["n_neighbors"] = [float(len(adjacency[unit_id])) for unit_id in unit_ids]
    validate(frame)
    return frame


def validate(frame: pd.DataFrame) -> None:
    """Reject missing units, invalid values and geographic interpretation mistakes."""
    assert len(frame) == 119 and frame["raion_id"].is_unique
    numeric = frame.drop(columns="raion_id")
    assert all(pd.api.types.is_float_dtype(dtype) for dtype in numeric.dtypes)
    assert np.isfinite(numeric.to_numpy()).all()
    distances = numeric.filter(regex=r"^dist_.*_km$")
    assert distances.ge(0).all().all() and distances.lt(2000).all().all()
    assert numeric["area_km2"].between(100, 20_000).all()
    assert numeric["n_neighbors"].between(0, 20).all()
    indexed = frame.set_index("raion_id")
    assert indexed.loc["kharkivska/kharkivskyi", "dist_russia_border_km"] < 10
    assert indexed.loc[
        indexed.index.str.startswith("zakarpatska/"), "dist_russia_border_km"
    ].gt(500).all()


def render(frame: pd.DataFrame) -> None:
    """Render the shipped values on the complete Ukraine map, including exclusions."""
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from matplotlib.patches import Patch, PathPatch
    from matplotlib.path import Path as PlotPath

    values = frame.set_index("raion_id")["dist_russia_border_km"]
    project = _projection()
    colors = LinearSegmentedColormap.from_list(
        "distance", ["#c5f0ff", "#369ac2", "#154765", "#061624"]
    )
    norm = Normalize(0, np.ceil(values.max() / 100) * 100)
    figure, axis = plt.subplots(figsize=(14.4, 9), dpi=120, facecolor="#000000")
    axis.set_facecolor("#000000")
    for feature in _features(config.RAIONS_GEOJSON_PATH):
        properties = cast(dict[str, str | None], feature["properties"])
        unit_id = properties["raion_id"]
        geometry = shapely.transform(_geometry(feature), project.transform, interleaved=False)
        polygons = [geometry] if isinstance(geometry, Polygon) else list(
            cast(MultiPolygon, geometry).geoms
        )
        color = colors(norm(values[unit_id])) if unit_id in values.index else "#292c32"
        for polygon in polygons:
            vertices: list[tuple[float, ...]] = []
            codes: list[int] = []
            for ring in [polygon.exterior, *polygon.interiors]:
                coordinates = list(ring.coords)
                vertices.extend(coordinates)
                codes.extend(
                    [PlotPath.MOVETO] + [PlotPath.LINETO] * (len(coordinates) - 2)
                    + [PlotPath.CLOSEPOLY]
                )
            axis.add_patch(PathPatch(
                PlotPath(vertices, codes), facecolor=color, edgecolor="#65737e", linewidth=0.4
            ))
    axis.autoscale_view()
    axis.set_aspect("equal")
    axis.margins(0.03)
    axis.axis("off")
    figure.text(
        0.07, 0.95, "Distance to Russia's land border", color="white", fontsize=24
    )
    figure.text(
        0.07, 0.905,
        "Minimum raion polygon distance | km | Fixed geography; front-line feature omitted",
        color="#a2b5c4", fontsize=12,
    )
    colorbar = figure.colorbar(
        matplotlib.cm.ScalarMappable(norm=norm, cmap=colors), ax=axis,
        orientation="horizontal", fraction=0.04, pad=0.035, shrink=0.7, aspect=45,
    )
    colorbar.ax.tick_params(colors="#d0dae3", labelsize=10)
    colorbar.set_label("Distance in km. Lighter raions are closer.", color="#d0dae3")
    axis.legend(
        handles=[Patch(facecolor="#292c32", label="Outside the forecast universe")],
        loc="lower left", frameon=False, labelcolor="#d0dae3", fontsize=10,
    )
    figure.text(
        0.07, 0.025,
        "119 forecast units | Natural Earth v5.1.2, public domain | "
        "OCHA COD-AB / geoBoundaries 2022, CC BY 3.0 IGO",
        color="#90a2b1", fontsize=10,
    )
    figure.subplots_adjust(left=0.05, right=0.98, top=0.875, bottom=0.1)
    figure.savefig(config.REPO_ROOT / "docs" / "geo-features.png", facecolor="#000000")
    plt.close(figure)


def build(*, offline: bool = False) -> pd.DataFrame:
    """Publish the deterministic CSV and its map; leave all existing geo inputs intact."""
    frame = compute(offline=offline)
    path = config.GEO_DIR / "static_features.csv"
    frame.to_csv(path, index=False, float_format="%.6f", lineterminator="\n")
    render(frame)
    print(f"Wrote {len(frame)} raions and {len(frame.columns) - 1} float features to "
          "data/geo/static_features.csv")
    print("Rendered docs/geo-features.png; dist_front_km omitted pending licensed dated geometry")
    print(frame.drop(columns="raion_id").agg(["min", "max"]).round(3).to_string())
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Require all pinned sources cached")
    build(offline=parser.parse_args().offline)


if __name__ == "__main__":
    main()
