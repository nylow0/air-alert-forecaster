"""API and metrics contracts, mirrored by web/src/lib/types.ts and fixture JSON."""

from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer


def _as_utc(value: datetime) -> datetime:
    """Reject ambiguous naive input and normalize every API time to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime must include a timezone")
    return value.astimezone(UTC)


def _utc_json(value: datetime) -> str:
    """Use the same Z suffix for all datetimes, including nested models."""
    return value.isoformat().replace("+00:00", "Z")


UTCDatetime = Annotated[
    datetime,
    AfterValidator(_as_utc),
    PlainSerializer(_utc_json, return_type=str, when_used="json"),
]


class ContractModel(BaseModel):
    """Reject accidental extra contract fields; defaults remain independent per instance."""

    model_config = ConfigDict(extra="forbid")


class Range(ContractModel):
    start: UTCDatetime
    end: UTCDatetime


class Meta(ContractModel):
    snapshot: UTCDatetime
    csv_sha256: str
    regime_switch: date | None
    horizons: list[int]
    raion_count: int
    splits: dict[Literal["train", "val", "test"], Range]
    replay: Range
    latest_at: UTCDatetime
    onset_model: str
    duration_model: str | None
    # Source outages (config.DATA_GAPS); the replay shows them but they are never scored.
    data_gaps: list[Range]


class ForecastRow(ContractModel):
    """End quantiles are absolute episode-start-plus-duration times, only for active rows."""

    raion_id: str
    p_1: float
    p_3: float
    p_12: float
    active: bool
    active_since: UTCDatetime | None
    end_p10: UTCDatetime | None
    end_p50: UTCDatetime | None
    end_p90: UTCDatetime | None
    actual_onset_1: bool | None
    actual_onset_3: bool | None
    actual_onset_12: bool | None


class ForecastResponse(ContractModel):
    at: UTCDatetime
    mode: Literal["replay", "latest"]
    onset_model: str
    duration_model: str | None
    rows: list[ForecastRow]


class HistoryPoint(ContractModel):
    t: UTCDatetime
    p_1: float
    p_3: float
    p_12: float


class EpisodeSpan(ContractModel):
    start: UTCDatetime
    end: UTCDatetime | None


class HistoryResponse(ContractModel):
    raion_id: str
    start: UTCDatetime
    end: UTCDatetime
    series: list[HistoryPoint]
    episodes: list[EpisodeSpan]


class ReliabilityBin(ContractModel):
    lo: float
    hi: float
    mean_pred: float | None
    frac_pos: float | None
    count: int


class ScoreSlice(ContractModel):
    n: int
    base_rate: float
    brier: float
    bss: float | None


class OnsetScore(ContractModel):
    """BSS compares B0 on identical rows; row_set names any restricted comparison window."""

    model: str
    horizon: int
    split: Literal["val", "test"]
    row_set: str = "full"
    n: int
    base_rate: float
    brier: float
    bss: float | None
    logloss: float
    pr_auc: float
    reliability: list[ReliabilityBin]
    by_oblast: dict[str, ScoreSlice]
    by_state: dict[Literal["quiet", "active"], ScoreSlice]


class DurationScore(ContractModel):
    model: str
    split: Literal["val", "test"]
    n: int
    pinball_q10: float
    pinball_q50: float
    pinball_q90: float
    mae_q50_min: float
    coverage_80: float


class Selection(ContractModel):
    onset: str | None
    duration: str | None
    note: str | None


class Report(ContractModel):
    """A fragment uses this same model, filling only the lists its producer owns."""

    generated_at: UTCDatetime | None
    onset: list[OnsetScore] = Field(default_factory=list)
    duration: list[DurationScore] = Field(default_factory=list)
    selection: Selection | None = None
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# v2 contracts (live map, system status, optional news agent, settings, auth).
# Mirrored in web/src/lib/types.ts. Change both together.
# ---------------------------------------------------------------------------


class LiveUnit(ContractModel):
    """One of the 139 map polygons right now. A non-null `*_since` means that layer is active.

    Red and yellow are independent layers (both can be active). `coverage`: observed directly,
    inherited from an oblast-level alert (Crimea, Luhansk, Sevastopol, Chornobyl zone), or unknown.
    """

    display_key: str
    raion_id: str | None
    oblast_id: str
    name: str
    red_since: UTCDatetime | None
    yellow_since: UTCDatetime | None
    other_hazards: list[str]
    coverage: Literal["observed", "inherited", "unknown"]


class LiveState(ContractModel):
    """Last successful poll; `stale` once a poll fails or the data is older than 3 min."""

    fetched_at: UTCDatetime | None
    source: str
    stale: bool
    units: list[LiveUnit]


class ProductionModel(ContractModel):
    onset: str
    duration: str | None
    trained_at: UTCDatetime | None
    data_through: UTCDatetime | None


class SystemStatus(ContractModel):
    """The scheduler and data freshness, for the UI status line and debugging."""

    now: UTCDatetime
    live_fetched_at: UTCDatetime | None
    live_stale: bool
    data_through: UTCDatetime | None
    ledger_watermark: date | None
    coverage_gaps: list[Range]
    last_nightly_at: UTCDatetime | None
    last_nightly_ok: bool | None
    next_nightly_at: UTCDatetime
    production: ProductionModel


NewsEventType = Literal[
    "bomber_takeoff",
    "mig31k_takeoff",
    "drone_group",
    "ballistic",
    "missile_launch",
    "all_clear",
    "other",
]


class NewsSourceRef(ContractModel):
    handle: str
    message_id: int
    url: str


class NewsEvent(ContractModel):
    """A validated, deduplicated threat incident from channel posts (never raw LLM text)."""

    id: str
    type: NewsEventType
    status: Literal["preliminary", "reported", "cleared"]
    oblast_ids: list[str]
    raion_ids: list[str]
    origin: str | None
    heading: str | None
    confidence: float
    summary: str
    published_at: UTCDatetime
    observed_at: UTCDatetime
    expires_at: UTCDatetime
    sources: list[NewsSourceRef]


class NewsAdjustmentRow(ContractModel):
    """Experimental news-adjusted probabilities; deltas are the applied logit shifts."""

    raion_id: str
    p_1: float
    p_3: float
    p_12: float
    delta_1: float
    delta_3: float
    delta_12: float
    event_ids: list[str]


class NewsModels(ContractModel):
    extract_model: str
    extract_effort: Literal["low", "medium", "high", "xhigh"]
    evaluate_model: str
    evaluate_effort: Literal["low", "medium", "high", "xhigh"]


class NewsStatus(ContractModel):
    enabled: bool
    state: Literal[
        "off",
        "needs_codex",
        "needs_login",
        "starting",
        "running",
        "stale",
        "error",
        "quota_limited",
    ]
    message: str | None
    last_poll_at: UTCDatetime | None
    last_extraction_at: UTCDatetime | None
    last_evaluation_at: UTCDatetime | None
    news_as_of: UTCDatetime | None
    models: NewsModels


class NewsFeed(ContractModel):
    """`adjustments` apply to the base forecast at `base_at` (the current hour), or are empty."""

    status: NewsStatus
    base_at: UTCDatetime | None
    events: list[NewsEvent]
    adjustments: list[NewsAdjustmentRow]


class NewsChannel(ContractModel):
    handle: str
    title: str
    subscribers: int
    enabled: bool


class Settings(ContractModel):
    """Persisted in git-ignored data/settings.local.json. The news agent is off by default."""

    news_enabled: bool = False
    models: NewsModels
    poll_seconds: int = 120
    channels: list[NewsChannel]


class AuthStatus(ContractModel):
    """ChatGPT login via the local Codex CLI. The app never sees passwords or tokens."""

    codex_installed: bool
    logged_in: bool
    login_in_progress: bool
    available_models: list[str]
    message: str | None
