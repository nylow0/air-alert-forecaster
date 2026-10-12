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
