import { hourMs } from './format'
import type { Range } from './types'

/** Outages and recovery are half-open intervals, matching the scoring policy. */
export function gapState(t: number, gaps: Range[]): 'outage' | 'recovery' | null {
  if (gaps.some(g => t >= Date.parse(g.start) && t < Date.parse(g.end))) return 'outage'
  if (gaps.some(g => t >= Date.parse(g.end) && t < Date.parse(g.end) + __DATA_GAP_FEATURE_RECOVERY_H__ * hourMs)) return 'recovery'
  return null
}

/** Clip outage bands to the displayed timeline; recovery has a notice, not a band. */
export function gapBands(gaps: Range[], start: number, end: number) {
  if (end <= start) return []
  return gaps.map(gap => ({ gap, start: Math.max(start, Date.parse(gap.start)), end: Math.min(end, Date.parse(gap.end)) }))
    .filter(band => band.end > band.start)
    .map(band => ({ ...band, left: (band.start - start) / (end - start) * 100, width: (band.end - band.start) / (end - start) * 100 }))
}
