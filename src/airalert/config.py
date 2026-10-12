"""Shared constants and absolute paths, rooted at this source checkout."""

from datetime import UTC, datetime
from pathlib import Path

HORIZONS = (1, 3, 12)
MERGE_GAP_MIN = 5
TRAIN_START = datetime(2022, 4, 1, tzinfo=UTC)
VAL_START = datetime(2026, 6, 1, tzinfo=UTC)
TEST_START = datetime(2026, 8, 1, tzinfo=UTC)
TEST_END_OFFSET_H = 12
KYIV_TZ = "Europe/Kyiv"
# Source outages: [start, end) with no alert events nationwide. v1 had one (2026-08-29T06:00Z to
# 2026-09-02T14:00Z in the CSV); v2 filled it from the validated alerts.in.ua day archive
# (docs/ingest.md), so the list is empty. The mask code stays: a future outage is added here.
# Rows whose label window (t, t+H] touches a gap, or with t in [start, end + recovery), are
# excluded from training and scoring; episodes overlapping a gap are excluded from duration work.
DATA_GAPS: tuple[tuple[datetime, datetime], ...] = ()
V1_DATA_GAPS = (
    (datetime(2026, 8, 29, 6, tzinfo=UTC), datetime(2026, 9, 2, 14, tzinfo=UTC)),
)
DATA_GAP_FEATURE_RECOVERY_H = 168  # longest feature lookback (f_starts_168h)
# Only Luhanska occurs in the saved CSV. The other names are explicit geography exclusions.
EXCLUDED_OBLASTS = frozenset({"luhanska", "autonomous-republic-of-crimea", "sevastopol-city"})
SPECIAL_STATUS_OBLASTS = frozenset({"kyiv-city", "sevastopol-city"})

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
GEO_DIR = DATA_DIR / "geo"
TEXT_DIR = DATA_DIR / "text"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
REPORTS_DIR = REPO_ROOT / "reports"
RECORDS_PATH = PROCESSED_DIR / "records.parquet"
EPISODES_PATH = PROCESSED_DIR / "episodes.parquet"
RAIONS_PATH = PROCESSED_DIR / "raions.csv"
SNAPSHOT_PATH = PROCESSED_DIR / "snapshot.json"
RAIONS_GEOJSON_PATH = GEO_DIR / "raions.geojson"
ADJACENCY_PATH = GEO_DIR / "adjacency.json"
CENTROIDS_PATH = GEO_DIR / "centroids.csv"
CROSSWALK_PATH = GEO_DIR / "crosswalk.csv"
FEATURES_DIR = PROCESSED_DIR / "features"
TRAIN_FEATURES_PATH = FEATURES_DIR / "train.parquet"
VAL_FEATURES_PATH = FEATURES_DIR / "val.parquet"
TEST_FEATURES_PATH = FEATURES_DIR / "test.parquet"
LATEST_FEATURES_PATH = FEATURES_DIR / "latest.parquet"
PREDS_DIR = PROCESSED_DIR / "preds"
ONSET_PREDS_PATH_TEMPLATE = PREDS_DIR / "onset_{model_id}.parquet"
DURATION_PREDS_PATH_TEMPLATE = PREDS_DIR / "duration_{model_id}.parquet"
FORECASTS_PATH = PROCESSED_DIR / "forecasts.parquet"
METRICS_DIR = REPORTS_DIR / "metrics"
METRICS_PATH = REPORTS_DIR / "metrics.json"
SCORES_VAL_PATH = METRICS_DIR / "scores_val.json"
SCORES_TEST_PATH = METRICS_DIR / "scores_test.json"
SELECTION_PATH = METRICS_DIR / "selection.json"
FIGURES_DIR = REPORTS_DIR / "figures"
FIXTURES_DIR = REPO_ROOT / "web" / "src" / "lib" / "fixtures"

OFFICIAL_CSV_URL = (
    "https://raw.githubusercontent.com/Vadimkin/"
    "ukrainian-air-raid-sirens-dataset/main/datasets/official_data_en.csv"
)
RAW_CSV_TEMPLATE = "official_data_en_{date}.csv"

# v2 keeps the previously scored period in validation and reserves a new test.
V2_TRAIN_START = TRAIN_START
V2_VAL_START = datetime(2026, 8, 1, tzinfo=UTC)
V2_TEST_START = datetime(2026, 9, 8, tzinfo=UTC)
V2_TEST_END_OFFSET_H = 12
V2_CALIBRATION_WEEKS = 6
V2_BOOTSTRAP_SAMPLES = 2000
V2_BOOTSTRAP_SEED = 20261012
V2_FEATURES_DIR = FEATURES_DIR / "v2"
V2_PREDS_DIR = PREDS_DIR / "v2"
V2_REPORTS_DIR = REPORTS_DIR / "v2"
V2_METRICS_DIR = V2_REPORTS_DIR / "metrics"
V2_METRICS_PATH = V2_REPORTS_DIR / "metrics.json"
V2_SELECTION_PATH = V2_REPORTS_DIR / "selection.json"
V2_ARTIFACTS_DIR = ARTIFACTS_DIR / "v2"
V2_PRODUCTION_DIR = ARTIFACTS_DIR / "production"
V2_FORECASTS_DIR = V2_PREDS_DIR / "issued"
V2_STATIC_FEATURES_PATH = GEO_DIR / "static_features.csv"
V2_LIVE_OBSERVATIONS_DIR = DATA_DIR / "live" / "observations"
