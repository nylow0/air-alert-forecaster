"""Match humanitarian boundaries to dataset IDs and build offline map inputs."""

import csv
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import cast

import httpx
import matplotlib
import pandas as pd
from pyproj import Transformer
from shapely import coverage_is_valid
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from shapely.strtree import STRtree

from airalert import config, ids

type Json = str | int | float | bool | None | list[Json] | dict[str, Json]


@dataclass(frozen=True)
class Unit:
    raion_id: str
    oblast_id: str
    oblast_name: str
    raion_name: str


@dataclass(frozen=True)
class Boundary:
    shape_id: str
    shape_name: str
    oblast_id: str
    geometry: BaseGeometry
    raion_id: str | None = None
    method: str = "unmatched"


# These are boundary-name corrections, never changes to canonical dataset IDs.
# Novomoskovskyi became Samarivskyi in resolution 3984-IX of 19 September 2024.
# Novohrad-Volynskyi became Zviahelskyi in resolution 2780-IX of 16 November 2022.
# Cnernivetskyi is the humanitarian source's spelling error for Chernivetskyi.
# The Kyiv city polygon is called Kyivska, the same source name as Kyiv oblast.
NAME_OVERRIDES = {
    ("dnipropetrovska", "novomoskovskyi"): "Samarivskyi",
    ("zhytomyrska", "novohrad-volynskyi"): "Zviahelskyi",
    ("chernivetska", "cnernivetskyi"): "Chernivetskyi",
    ("kyiv-city", "kyivska"): "Kyiv City",
}

# ADM1 has no English attribute for these regions. Its second Kyivska shape is
# Kyiv city, identified below by its smaller area rather than source row order.
OBLAST_NAME_OVERRIDES = {
    "avtonomna-respublika-krym": "Autonomous Republic of Crimea",
    "sevastopilska": "Sevastopol City",
}


def _source(level: str) -> tuple[dict[str, Json], dict[str, Json]]:
    """Cache API metadata and the full geometry, verifying cached bytes on every run."""
    cache = config.RAW_DIR / "geo"
    cache.mkdir(parents=True, exist_ok=True)
    metadata_path = cache / f"gbHumanitarian-UKR-{level}.metadata.json"
    geometry_path = cache / f"gbHumanitarian-UKR-{level}.geojson"
    api_url = f"https://www.geoboundaries.org/api/current/gbHumanitarian/UKR/{level}/"
    if metadata_path.exists() and geometry_path.exists():
        metadata = cast(dict[str, Json], json.loads(metadata_path.read_text(encoding="utf-8")))
        content = geometry_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != metadata["cached_sha256"]:
            raise ValueError(f"Corrupt boundary cache: {geometry_path}")
    else:
        with httpx.Client(follow_redirects=True, timeout=180) as client:
            response = client.get(api_url)
            response.raise_for_status()
            metadata = cast(dict[str, Json], response.json())
            response = client.get(str(metadata["gjDownloadURL"]))
            response.raise_for_status()
            content = response.content
        metadata["api_url"] = api_url
        metadata["retrieved_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        metadata["cached_sha256"] = hashlib.sha256(content).hexdigest()
        geometry_path.write_bytes(content)
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    geometry = cast(dict[str, Json], json.loads(content))
    features = cast(list[dict[str, Json]], geometry["features"])
    expected = int(str(metadata["admUnitCount"]))
    if len(features) != expected or (level == "ADM2" and not 130 <= expected <= 150):
        raise ValueError(f"Wrong boundary vintage: {level}, {len(features)} units")
    print(f"Cached gbHumanitarian {level}: {len(features)} polygons", flush=True)
    return metadata, geometry


def _universe() -> tuple[dict[str, Unit], Path, str]:
    """Use all dates and only dataset names, matching A1's fixed-universe contract."""
    copies = sorted(config.RAW_DIR.glob(config.RAW_CSV_TEMPLATE.format(date="*")))
    if not copies:
        raise FileNotFoundError("No dated official CSV; run `uv run airalert fetch` first")
    path = copies[-1]
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    checksum = path.with_suffix(".csv.sha256")
    if checksum.exists() and checksum.read_text(encoding="ascii").split()[0] != digest:
        raise ValueError(f"Dataset checksum mismatch: {path}")
    frame = pd.read_csv(path, usecols=["oblast", "raion", "level"], keep_default_na=False)
    names = frame[["oblast", "raion", "level"]].drop_duplicates()
    units: dict[str, Unit] = {}
    for oblast_name, raion_name, level in names.itertuples(index=False, name=None):
        region = ids.oblast_id(oblast_name)
        if region in config.EXCLUDED_OBLASTS or level not in {"raion", "hromada"}:
            continue
        if not raion_name:
            raise ValueError(f"Missing parent raion for {oblast_name}, {level}")
        unit_id = ids.raion_id(oblast_name, raion_name)
        units[unit_id] = Unit(unit_id, region, oblast_name, raion_name)
    for oblast_name in sorted(set(names["oblast"])):
        region = ids.oblast_id(oblast_name)
        if region in config.EXCLUDED_OBLASTS or region not in config.SPECIAL_STATUS_OBLASTS:
            continue
        if not any(unit.oblast_id == region for unit in units.values()):
            unit_id = ids.raion_id(oblast_name, None)
            units[unit_id] = Unit(unit_id, region, oblast_name, oblast_name)
    if not units:
        raise ValueError("Dataset raion universe is empty")
    if config.RAIONS_PATH.exists():
        published = set(pd.read_csv(config.RAIONS_PATH)["raion_id"])
        if published != set(units):
            raise ValueError(
                "A1 universe differs from raw CSV: "
                f"only raw={sorted(set(units) - published)}, "
                f"only published={sorted(published - set(units))}"
            )
    return dict(sorted(units.items())), path, digest


def _normalized(name: str) -> str:
    """Fold separators and common transliteration suffixes, scoped to one oblast."""
    value = ids.slug(re.sub(r"\s+(?:raion|rayon|district)$", "", name, flags=re.IGNORECASE))
    value = re.sub(r"(?:skyy|skiy|skij|skyi)$", "sky", value)
    return value.replace("-", "")


def _boundaries(adm1: dict[str, Json], adm2: dict[str, Json]) -> list[Boundary]:
    """Assign ADM2 to its greatest-overlap ADM1 polygon, including Kyiv city."""
    regions = cast(list[dict[str, Json]], adm1["features"])
    geometries = [shape(cast(dict[str, object], feature["geometry"])) for feature in regions]
    region_ids = []
    for feature in regions:
        properties = cast(dict[str, str], feature["properties"])
        name = properties["shapeName"]
        canonical = OBLAST_NAME_OVERRIDES.get(ids.slug(name), name)
        region_ids.append(ids.oblast_id(canonical))
    kyiv_shapes = [index for index, region in enumerate(region_ids) if region == "kyivska"]
    if len(kyiv_shapes) != 2:
        raise ValueError("Expected separate Kyiv oblast and city ADM1 polygons")
    city_index = min(kyiv_shapes, key=lambda index: geometries[index].area)
    region_ids[city_index] = ids.oblast_id("Kyiv City")
    tree = STRtree(geometries)
    result = []
    for feature in cast(list[dict[str, Json]], adm2["features"]):
        properties = cast(dict[str, str], feature["properties"])
        geometry = shape(cast(dict[str, object], feature["geometry"]))
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError(f"Invalid raw polygon: {properties['shapeName']}")
        overlaps = {
            int(index): geometry.intersection(geometries[index]).area
            for index in tree.query(geometry, predicate="intersects")
        }
        region_index = max(overlaps, key=lambda index: overlaps[index])
        if overlaps[region_index] / geometry.area < 0.99:
            raise ValueError(f"Ambiguous oblast for {properties['shapeName']}")
        result.append(
            Boundary(
                properties["shapeID"], properties["shapeName"], region_ids[region_index], geometry
            )
        )
    return result


def _crosswalk(boundaries: list[Boundary], units: dict[str, Unit]) -> list[Boundary]:
    """Resolve unique names inside each oblast; refuse ambiguous or duplicate matches."""
    result = []
    seen: set[str] = set()
    for boundary in boundaries:
        unit_id: str | None = None
        method = "unmatched"
        candidates = [unit for unit in units.values() if unit.oblast_id == boundary.oblast_id]
        if boundary.oblast_id not in config.EXCLUDED_OBLASTS:
            exact = [
                unit
                for unit in candidates
                if ids.raion_id(unit.oblast_name, boundary.shape_name) == unit.raion_id
            ]
            normalized = [
                unit
                for unit in candidates
                if _normalized(unit.raion_name) == _normalized(boundary.shape_name)
            ]
            override = NAME_OVERRIDES.get((boundary.oblast_id, ids.slug(boundary.shape_name)))
            if exact:
                matches, method = exact, "exact"
            elif normalized:
                matches, method = normalized, "normalized"
            elif override:
                matches = [
                    unit
                    for unit in candidates
                    if _normalized(unit.raion_name) == _normalized(override)
                ]
                method = "override" if matches else "unmatched"
            else:
                matches = []
            if len(matches) > 1:
                raise ValueError(f"Ambiguous name match: {boundary.shape_name}")
            if matches:
                unit_id = matches[0].raion_id
                if unit_id in seen:
                    raise ValueError(f"Two polygons match dataset unit {unit_id}")
                seen.add(unit_id)
        result.append(
            Boundary(
                boundary.shape_id,
                boundary.shape_name,
                boundary.oblast_id,
                boundary.geometry,
                unit_id,
                method,
            )
        )
    assert len(seen) == sum(boundary.raion_id is not None for boundary in result)
    return result


def _edges(geometries: list[BaseGeometry]) -> set[tuple[int, int]]:
    """Positive-length shared borders only; point contacts never become neighbours."""
    tree = STRtree(geometries)
    borders = [geometry.boundary for geometry in geometries]
    return {
        (left, int(right))
        for left, geometry in enumerate(geometries)
        for right in tree.query(geometry, predicate="intersects")
        if left < right and borders[left].intersection(borders[right]).length > 0
    }


def _adjacency(
    boundaries: list[Boundary], units: dict[str, Unit], edges: set[tuple[int, int]]
) -> dict[str, list[str]]:
    neighbours: dict[str, set[str]] = {unit_id: set() for unit_id in units}
    for left, right in edges:
        first, second = boundaries[left].raion_id, boundaries[right].raion_id
        if first is not None and second is not None:
            neighbours[first].add(second)
            neighbours[second].add(first)
    adjacency = {unit_id: sorted(values) for unit_id, values in sorted(neighbours.items())}
    assert all(left in adjacency[right] for left, values in adjacency.items() for right in values)
    assert all(unit_id not in values for unit_id, values in adjacency.items())
    return adjacency


def _simplified(
    boundaries: list[Boundary], edges: set[tuple[int, int]], units: dict[str, Unit]
) -> tuple[dict[str, Json], float, int]:
    """Use mapshaper's shared arcs and intersection repair, then verify all borders."""
    features = [
        {
            "type": "Feature",
            "properties": {
                "raion_id": boundary.raion_id,
                "oblast_id": boundary.oblast_id,
                "name": units[boundary.raion_id].raion_name
                if boundary.raion_id is not None
                else boundary.shape_name,
            },
            "geometry": boundary.geometry.__geo_interface__,
        }
        for boundary in boundaries
    ]
    bun = shutil.which("bun")
    if bun is None:
        raise RuntimeError("Install bun to run mapshaper; see docs/geo.md")
    cache = config.RAW_DIR / "geo"
    input_path = cache / "mapshaper-input.geojson"
    output_path = cache / "mapshaper-output.geojson"
    input_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    for tolerance in (250, 350, 500, 750):
        completed = subprocess.run(
            # Explicit `bun x` avoids Windows argv[0] casing changing bunx into bun.
            [
                bun,
                "x",
                "--no-install",
                "mapshaper@0.7.83",
                str(input_path),
                "-simplify",
                "weighted",
                f"interval={tolerance}",
                "keep-shapes",
                "-o",
                "format=geojson",
                "precision=0.0001",
                "reverse-winding",
                "force",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(
                "Mapshaper is not cached or failed. First run `bunx mapshaper@0.7.83 -v`.\n"
                + completed.stderr
            )
        collection = cast(dict[str, Json], json.loads(output_path.read_text(encoding="utf-8")))
        output_features = cast(list[dict[str, Json]], collection["features"])
        # Keep only contract data, without generator-specific feature IDs.
        for feature in output_features:
            feature.pop("id", None)
        shapes = [
            shape(cast(dict[str, object], feature["geometry"])) for feature in output_features
        ]
        if len(shapes) != len(boundaries) or any(
            geometry.is_empty or not geometry.is_valid for geometry in shapes
        ):
            print(f"Rejected tolerance {tolerance}: invalid rounded geometry", flush=True)
            continue
        if not coverage_is_valid(shapes):
            print(f"Rejected tolerance {tolerance}: invalid polygon coverage", flush=True)
            continue
        if _edges(shapes) != edges:
            print(f"Rejected tolerance {tolerance}: border adjacency changed", flush=True)
            continue
        encoded = json.dumps(collection, ensure_ascii=False, separators=(",", ":")) + "\n"
        size = len(encoded.encode("utf-8"))
        if size < 600_000:
            config.RAIONS_GEOJSON_PATH.write_text(encoded, encoding="utf-8", newline="\n")
            return collection, tolerance, size
    raise ValueError("No topology-preserving four-decimal GeoJSON fits under 600 KB")


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _centroids(boundaries: list[Boundary]) -> int:
    """Use area centroids in Europe LAEA, with an interior point for coastal shapes."""
    project = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True).transform
    unproject = Transformer.from_crs("EPSG:3035", "EPSG:4326", always_xy=True).transform
    rows: list[dict[str, object]] = []
    fallbacks = 0
    for boundary in sorted(boundaries, key=lambda item: item.raion_id or ""):
        if boundary.raion_id is None:
            continue
        projected = transform(project, boundary.geometry)
        point = projected.centroid
        if not projected.covers(point):
            point = projected.representative_point()
            fallbacks += 1
        point = transform(unproject, point)
        assert boundary.geometry.covers(point)
        rows.append(
            {"raion_id": boundary.raion_id, "lat": round(point.y, 8), "lon": round(point.x, 8)}
        )
    _write_csv(config.CENTROIDS_PATH, ["raion_id", "lat", "lon"], rows)
    return fallbacks


def _map(collection: dict[str, Json], units: dict[str, Unit], size: int) -> None:
    """Render the actual simplified artifact at the dashboard's desktop width."""
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch, PathPatch
    from matplotlib.path import Path as PlotPath

    project = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True).transform
    figure, axis = plt.subplots(figsize=(14.4, 9), dpi=100, facecolor="#000000")
    axis.set_facecolor("#000000")
    for feature in cast(list[dict[str, Json]], collection["features"]):
        properties = cast(dict[str, Json], feature["properties"])
        geometry = transform(project, shape(cast(dict[str, object], feature["geometry"])))
        polygons = [geometry] if isinstance(geometry, Polygon) else list(geometry.geoms)
        excluded = properties["oblast_id"] in config.EXCLUDED_OBLASTS
        color = "#353b43" if excluded else "#36b3a8" if properties["raion_id"] else "#e9aa54"
        for polygon in polygons:
            vertices = []
            codes = []
            for ring in [polygon.exterior, *polygon.interiors]:
                points = list(ring.coords)
                vertices.extend(points)
                codes.extend(
                    [PlotPath.MOVETO] + [PlotPath.LINETO] * (len(points) - 2) + [PlotPath.CLOSEPOLY]
                )
            axis.add_patch(
                PathPatch(
                    PlotPath(vertices, codes), facecolor=color, edgecolor="#101d22", linewidth=0.45
                )
            )
    axis.autoscale_view()
    axis.set_aspect("equal")
    axis.margins(0.025)
    axis.axis("off")
    axis.set_title(
        "Ukraine | 2022 humanitarian raion boundaries",
        color="white",
        fontsize=20,
        loc="left",
        pad=18,
    )
    handles = [
        Patch(facecolor="#36b3a8", label="Matched dataset raions"),
        Patch(facecolor="#e9aa54", label="No dataset raion"),
        Patch(facecolor="#353b43", label="Excluded from forecasting"),
    ]
    axis.legend(handles=handles, loc="lower left", frameon=False, labelcolor="#dfebed", fontsize=11)
    figure.text(
        0.09,
        0.035,
        f"{len(units)} dataset units | {size / 1000:.1f} KB GeoJSON | "
        "Source: geoBoundaries / OCHA COD-AB, CC BY 3.0 IGO",
        color="#8b9da3",
        fontsize=10,
    )
    figure.subplots_adjust(left=0.06, right=0.97, top=0.9, bottom=0.08)
    figure.savefig(config.REPO_ROOT / "docs" / "geo_map.png", facecolor="#000000")
    plt.close(figure)


def _report(
    metadata: list[dict[str, Json]],
    boundaries: list[Boundary],
    units: dict[str, Unit],
    csv_path: Path,
    digest: str,
    adjacency: dict[str, list[str]],
    tolerance: float,
    size: int,
    fallbacks: int,
) -> str:
    matched = {boundary.raion_id for boundary in boundaries if boundary.raion_id}
    missing = sorted(set(units) - matched)
    unmatched = [boundary for boundary in boundaries if boundary.raion_id is None]
    degrees = [len(values) for values in adjacency.values()]
    zero = [unit_id for unit_id, values in adjacency.items() if not values]
    share = len(missing) / len(units)
    lines = ["# Geography and crosswalk", "", "## Sources and reproducibility", ""]
    for source in metadata:
        lines.extend(
            [
                f"- [{source['boundaryType']} API]({source['api_url']}), "
                f"[{source['boundaryType']} full GeoJSON]({source['gjDownloadURL']}). "
                f"Boundary ID `{source['boundaryID']}`, "
                f"represented year {source['boundaryYearRepresented']}, "
                f"{source['admUnitCount']} units. Retrieved {source['retrieved_at']}.",
                f"  License: {source['boundaryLicense']}. "
                "Origin: [OCHA COD-AB / Kartographia]"
                "(https://data.humdata.org/dataset/cod-ab-ukr). "
                f"SHA256 `{source['cached_sha256']}`.",
            ]
        )
    lines.extend(
        [
            "",
            "Raw API metadata and full unsimplified boundaries are cached in `data/raw/geo/`. "
            "A cached rerun makes no HTTP requests and verifies both boundary SHA256 hashes. "
            "Mapshaper 0.7.83 is pinned and run through `bunx --no-install`; "
            "on a fresh machine first cache it with `bunx mapshaper@0.7.83 -v`.",
            "",
            f"Universe: `{csv_path.relative_to(config.REPO_ROOT).as_posix()}`, "
            f"SHA256 `{digest}`. All dates contribute raion- and hromada-level parents; "
            "special-status cities without dataset raions contribute one unit. "
            "IDs and exclusions come from `airalert.ids` and `airalert.config`. "
            "If A1's `raions.csv` exists, the build requires identical ID sets.",
            "",
            "## Matching and geometry",
            "",
            "ADM2 has no parent-oblast attribute. Each polygon is assigned to the ADM1 polygon "
            "with greatest overlap, requiring at least 99% overlap. The smaller of the two ADM1 "
            "`Kyivska` polygons is Kyiv city. Its dataset unit is `kyiv-city/kyiv-city`.",
            "",
            "Names match only within an oblast. Exact canonical IDs are tried first, then "
            "separator and transliteration-suffix normalization. Remaining aliases are explicit "
            "in `NAME_OVERRIDES`; no fuzzy or cross-oblast match is used. Each matched dataset "
            "ID has exactly one polygon.",
            "",
            "Map labels use the dataset's English names for matched units, so Kyiv city "
            "and renamed raions display their dataset names. Crosswalk `shape_name` retains "
            "the original boundary name for auditability.",
            "",
            "The 2022 source retains Novomoskovskyi and Novohrad-Volynskyi. "
            "[Resolution 3984-IX](https://zakon.rada.gov.ua/laws/show/3984-20) "
            "renamed the former to Samarivskyi; "
            "[resolution 2780-IX](https://zakon.rada.gov.ua/laws/show/2780-20) "
            "renamed the latter to Zviahelskyi. `Cnernivetskyi` is corrected to Chernivetskyi. "
            "These affect only the crosswalk, never the dataset IDs.",
            "",
            f"Mapshaper 0.7.83 constructs shared topology across all {len(boundaries)} polygons. "
            f"Shared arcs use weighted simplification at a {tolerance}-metre interval, "
            "with intersection repair and `keep-shapes`, and are exported at four "
            f"decimal places. Output is {size:,} bytes, {size / 1000:.3f} KB "
            f"or {size / 1024:.3f} KiB, below the strict 600,000-byte target. "
            "Every output polygon must be valid and nonempty, and every positive-length "
            "border pair across all polygons must equal the unsimplified source pair set. "
            "Shapely's coverage check also rejects overlapping or mismatched polygon edges. "
            "[Mapshaper reference](https://mapshaper.org/docs/reference.html#simplify) "
            "documents shared topology and simplification.",
            "",
            "Outer rings are clockwise and holes counterclockwise, using mapshaper's "
            "`reverse-winding` export for direct d3-geo rendering. "
            "[The export reference](https://mapshaper.org/docs/reference.html#o) documents "
            "this d3-compatible winding. The frontend can consume this artifact directly.",
            "",
            "Adjacency uses unsimplified border intersections with strictly positive length. "
            "Point-only contacts do not count. Its keys include every universe ID, with "
            "missing geometries represented by empty lists. Excluded and unmatched polygons "
            "never appear as neighbours. Symmetry, uniqueness and no self-edges are asserted.",
            "",
            "Centroids use EPSG:3035 area centroids and are transformed back to "
            "longitude/latitude. "
            f"{fallbacks} matched polygons use an interior representative point because their "
            "centroid falls outside. Every point is checked against its unsimplified polygon.",
            "",
            "## Coverage and G1 recommendation",
            "",
            f"- Source polygons: {len(boundaries)}.",
            f"- Dataset units: {len(units)}; matched: {len(matched)}.",
            f"- Dataset units without a polygon: {len(missing)} / {len(units)} = {share:.2%}.",
            f"- Polygons without a dataset unit: {len(unmatched)}, including "
            f"{sum(boundary.oblast_id in config.EXCLUDED_OBLASTS for boundary in unmatched)} "
            "excluded polygons retained for Ukraine's complete outline.",
            "- Match methods: "
            f"{dict(sorted(Counter(boundary.method for boundary in boundaries).items()))}.",
            "- G1 recommendation: "
            f"{'switch to oblast units' if share > 0.05 else 'retain raion units'}. "
            f"The unmatched share is {'above' if share > 0.05 else 'at or below'} 5%. "
            "This is a recommendation for the architect, not an implemented unit switch.",
            "",
            "### Dataset units without polygons",
            "",
            *([f"- `{unit_id}`" for unit_id in missing] or ["None."]),
            "",
            "### Polygons without dataset units",
            "",
            "| Shape ID | English source name | Oblast ID | Reason |",
            "| --- | --- | --- | --- |",
            *[
                f"| `{boundary.shape_id}` | {boundary.shape_name} | `{boundary.oblast_id}` | "
                + (
                    "Excluded by config"
                    if boundary.oblast_id in config.EXCLUDED_OBLASTS
                    else "Absent from dataset"
                )
                + " |"
                for boundary in unmatched
            ],
            "",
            "## Adjacency",
            "",
            f"Min / median / max neighbours: {min(degrees)} / {median(degrees):g} / "
            f"{max(degrees)}. "
            f"Undirected edges: {sum(degrees) // 2}. Zero-neighbour units: {len(zero)}.",
            "",
            *(
                [
                    f"- `{unit_id}`: "
                    + (
                        "no matched polygon"
                        if unit_id in missing
                        else "the raw polygon shares no positive-length border "
                        "with another matched universe unit"
                    )
                    for unit_id in zero
                ]
                or ["No isolated dataset units."]
            ),
            "",
            "## Simplified map",
            "",
            "![Simplified Ukraine map](geo_map.png)",
            "",
            "The 1440 by 900 PNG renders the exported GeoJSON. Teal marks matched units, "
            "amber marks unmatched units, and grey marks excluded units. The full country "
            "outline includes Crimea, Sevastopol and Luhansk.",
            "",
        ]
    )
    config.REPO_ROOT.joinpath("docs", "geo.md").write_text("\n".join(lines), encoding="utf-8")
    summary = [
        f"Polygons: {len(boundaries)}; matched: {len(matched)}; dataset units: {len(units)}",
        f"Dataset raions without polygons ({len(missing)}): {missing}",
        f"Polygons without dataset raions ({len(unmatched)}): "
        f"{[(boundary.oblast_id, boundary.shape_name) for boundary in unmatched]}",
        f"GeoJSON: {size / 1000:.3f} KB ({size:,} bytes)",
        "Adjacency neighbours min/median/max: "
        f"{min(degrees)}/{median(degrees):g}/{max(degrees)}; "
        f"edges: {sum(degrees) // 2}; zero neighbours: {zero}",
        f"G1: {len(missing)}/{len(units)} = {share:.2%} unmatched; recommend "
        f"{'oblast fallback' if share > 0.05 else 'retain raions'}",
        f"Map PNG: {config.REPO_ROOT / 'docs' / 'geo_map.png'}",
    ]
    return "\n".join(summary)


def build() -> None:
    """Build the crosswalk, shared-border map, adjacency and coverage report offline."""
    units, csv_path, digest = _universe()
    metadata1, adm1 = _source("ADM1")
    metadata2, adm2 = _source("ADM2")
    boundaries = _crosswalk(_boundaries(adm1, adm2), units)
    edges = _edges([boundary.geometry for boundary in boundaries])
    adjacency = _adjacency(boundaries, units, edges)
    config.GEO_DIR.mkdir(parents=True, exist_ok=True)
    print("Simplifying shared mapshaper arcs...", flush=True)
    collection, tolerance, size = _simplified(boundaries, edges, units)
    _write_csv(
        config.CROSSWALK_PATH,
        ["shape_id", "shape_name", "oblast_id", "raion_id", "method"],
        [
            {
                "shape_id": boundary.shape_id,
                "shape_name": boundary.shape_name,
                "oblast_id": boundary.oblast_id,
                "raion_id": boundary.raion_id,
                "method": boundary.method,
            }
            for boundary in boundaries
        ],
    )
    config.ADJACENCY_PATH.write_text(json.dumps(adjacency, indent=2) + "\n", encoding="utf-8")
    fallbacks = _centroids(boundaries)
    _map(collection, units, size)
    print(
        _report(
            [metadata1, metadata2],
            boundaries,
            units,
            csv_path,
            digest,
            adjacency,
            tolerance,
            size,
            fallbacks,
        ),
        flush=True,
    )
