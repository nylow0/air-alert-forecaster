// hand-copied from src/airalert/schemas.py; change both together
// Datetimes are ISO 8601 UTC strings ending in Z; dates are YYYY-MM-DD strings.

export type Range = {
  start: string
  end: string
}

export type Meta = {
  snapshot: string
  csv_sha256: string
  regime_switch: string | null
  horizons: number[]
  raion_count: number
  splits: Record<'train' | 'val' | 'test', Range>
  replay: Range
  latest_at: string
  onset_model: string
  duration_model: string | null
  /** Source outages: the replay shows these hours, but they are never scored. */
  data_gaps: Range[]
}

export type ForecastRow = {
  raion_id: string
  p_1: number
  p_3: number
  p_12: number
  active: boolean
  active_since: string | null
  end_p10: string | null
  end_p50: string | null
  end_p90: string | null
  actual_onset_1: boolean | null
  actual_onset_3: boolean | null
  actual_onset_12: boolean | null
}

export type ForecastResponse = {
  at: string
  mode: 'replay' | 'latest'
  onset_model: string
  duration_model: string | null
  rows: ForecastRow[]
}

export type HistoryPoint = {
  t: string
  p_1: number
  p_3: number
  p_12: number
}

export type EpisodeSpan = {
  start: string
  end: string | null
}

export type HistoryResponse = {
  raion_id: string
  start: string
  end: string
  series: HistoryPoint[]
  episodes: EpisodeSpan[]
}

export type ReliabilityBin = {
  lo: number
  hi: number
  mean_pred: number | null
  frac_pos: number | null
  count: number
}

export type ScoreSlice = {
  n: number
  base_rate: number
  brier: number
  bss: number | null
}

export type OnsetScore = {
  model: string
  horizon: number
  split: 'val' | 'test'
  row_set: string
  n: number
  base_rate: number
  brier: number
  bss: number | null
  logloss: number
  pr_auc: number
  reliability: ReliabilityBin[]
  by_oblast: Record<string, ScoreSlice>
  by_state: Partial<Record<'quiet' | 'active', ScoreSlice>>
}

export type DurationScore = {
  model: string
  split: 'val' | 'test'
  n: number
  pinball_q10: number
  pinball_q50: number
  pinball_q90: number
  mae_q50_min: number
  coverage_80: number
}

export type Selection = {
  onset: string | null
  duration: string | null
  note: string | null
}

export type Report = {
  generated_at: string | null
  onset: OnsetScore[]
  duration: DurationScore[]
  selection: Selection | null
  notes: string[]
}

export type RaionFeatureProperties = {
  raion_id: string | null
  oblast_id: string
  name: string
  /** v2: crosswalk shape_id, the key LiveUnit uses for all 139 polygons. */
  display_key: string
}

export type Position = [number, number]

export type RaionGeometry =
  | { type: 'Polygon'; coordinates: Position[][] }
  | { type: 'MultiPolygon'; coordinates: Position[][][] }

export type RaionFeature = {
  type: 'Feature'
  properties: RaionFeatureProperties
  geometry: RaionGeometry
}

export type RaionFeatureCollection = {
  type: 'FeatureCollection'
  features: RaionFeature[]
}

// ---------------------------------------------------------------------------
// v2 contracts: hand-copied from the v2 section of src/airalert/schemas.py
// ---------------------------------------------------------------------------

/** One map polygon right now; a non-null `*_since` means that layer is active. */
export type LiveUnit = {
  display_key: string
  raion_id: string | null
  oblast_id: string
  name: string
  red_since: string | null
  yellow_since: string | null
  other_hazards: string[]
  coverage: 'observed' | 'inherited' | 'unknown'
}

export type LiveState = {
  fetched_at: string | null
  source: string
  stale: boolean
  units: LiveUnit[]
}

export type ProductionModel = {
  onset: string
  duration: string | null
  trained_at: string | null
  data_through: string | null
}

export type SystemStatus = {
  now: string
  live_fetched_at: string | null
  live_stale: boolean
  data_through: string | null
  ledger_watermark: string | null
  coverage_gaps: Range[]
  last_nightly_at: string | null
  last_nightly_ok: boolean | null
  next_nightly_at: string
  production: ProductionModel
}

export type NewsEventType =
  | 'bomber_takeoff'
  | 'mig31k_takeoff'
  | 'drone_group'
  | 'ballistic'
  | 'missile_launch'
  | 'all_clear'
  | 'other'

export type NewsSourceRef = { handle: string; message_id: number; url: string }

export type NewsEvent = {
  id: string
  type: NewsEventType
  status: 'preliminary' | 'reported' | 'cleared'
  oblast_ids: string[]
  raion_ids: string[]
  origin: string | null
  heading: string | null
  confidence: number
  summary: string
  published_at: string
  observed_at: string
  expires_at: string
  sources: NewsSourceRef[]
}

export type NewsAdjustmentRow = {
  raion_id: string
  p_1: number
  p_3: number
  p_12: number
  delta_1: number
  delta_3: number
  delta_12: number
  event_ids: string[]
}

export type ReasoningEffort = 'low' | 'medium' | 'high' | 'xhigh'

export type NewsModels = {
  extract_model: string
  extract_effort: ReasoningEffort
  evaluate_model: string
  evaluate_effort: ReasoningEffort
}

export type NewsStatus = {
  enabled: boolean
  state: 'off' | 'needs_codex' | 'needs_login' | 'starting' | 'running' | 'stale' | 'error' | 'quota_limited'
  message: string | null
  last_poll_at: string | null
  last_extraction_at: string | null
  last_evaluation_at: string | null
  news_as_of: string | null
  models: NewsModels
}

export type NewsFeed = {
  status: NewsStatus
  base_at: string | null
  events: NewsEvent[]
  adjustments: NewsAdjustmentRow[]
}

export type NewsChannel = { handle: string; title: string; subscribers: number; enabled: boolean }

export type Settings = {
  news_enabled: boolean
  models: NewsModels
  poll_seconds: number
  channels: NewsChannel[]
}

export type AuthStatus = {
  codex_installed: boolean
  logged_in: boolean
  login_in_progress: boolean
  available_models: string[]
  message: string | null
}
