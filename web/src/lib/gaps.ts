import { hourMs, type Horizon } from './format'
import type { Range } from './types'

/** With a horizon, also reject (t, t+H] touching [start, end), including t+H = start. */
export function gapState(t: number, gaps: Range[], horizon?: Horizon): 'outage' | 'recovery' | 'label-window' | null {
  if (gaps.some(g => t >= Date.parse(g.start) && t < Date.parse(g.end))) return 'outage'
  if (gaps.some(g => t >= Date.parse(g.end) && t < Date.parse(g.end) + __DATA_GAP_FEATURE_RECOVERY_H__ * hourMs)) return 'recovery'
  if (horizon !== undefined && gaps.some(g => t < Date.parse(g.end) && t + horizon * hourMs >= Date.parse(g.start))) return 'label-window'
  return null
}

/** Explain scoring eligibility without treating a future outage as unknown current status. */
export function gapNotice(state: ReturnType<typeof gapState>, horizon: Horizon): string | null {
  if (state === 'label-window') return `The ${horizon} h forecast window reaches into the source outage. This forecast is not scored.`
  if (state === 'outage') return 'This hour is inside the source outage. Alert observations are unknown, and this forecast is not scored.'
  if (state === 'recovery') return 'This hour is inside the recovery window after the source outage. This forecast is not scored.'
  return null
}

/** Clip outage bands to the displayed timeline; recovery has a notice, not a band. */
export function gapBands(gaps: Range[], start: number, end: number) {
  if (end <= start) return []
  return gaps.map(gap => ({ gap, start: Math.max(start, Date.parse(gap.start)), end: Math.min(end, Date.parse(gap.end)) }))
    .filter(band => band.end > band.start)
    .map(band => ({ ...band, left: (band.start - start) / (end - start) * 100, width: (band.end - band.start) / (end - start) * 100 }))
}
