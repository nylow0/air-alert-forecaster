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
