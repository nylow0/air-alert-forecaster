"""N1-local defaults pending migration to the lead-owned config contract."""

from dataclasses import dataclass

from airalert.config import DATA_DIR

NEWS_DB = DATA_DIR / "news" / "news.sqlite"
SETTINGS_PATH = DATA_DIR / "settings.local.json"
REQUEST_SPACING = 1.05
MAX_MESSAGES = 30
MAX_BATCH_BYTES = 24 * 1024
EXTRACT_INTERVAL = 120
EVALUATE_INTERVAL = 300
EXTRACTION_LIMIT = 200
EVALUATION_LIMIT = 60
TOKEN_LIMIT = 5_000_000
TOKEN_RESERVE = 100_000
OPERATING_CAP = 0.5
HARD_CAP = 1.0


@dataclass(frozen=True)
class Decay:
    tau_minutes: float
    expiry_minutes: float
    weights: tuple[float, float, float]


DECAY = {
    "bomber_takeoff": Decay(120, 360, (0.25, 1.0, 0.5)),
    "mig31k_takeoff": Decay(10, 30, (1.0, 0.4, 0.1)),
    "drone_group": Decay(30, 90, (1.0, 0.4, 0.1)),
    "ballistic": Decay(10, 30, (1.0, 0.4, 0.1)),
    "missile_launch": Decay(10, 30, (1.0, 0.4, 0.1)),
    "all_clear": Decay(10, 30, (0.0, 0.0, 0.0)),
    "other": Decay(10, 30, (0.0, 0.0, 0.0)),
}
