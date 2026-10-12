import { clock, oblastName } from './format'
import type { LiveUnit, NewsEventType, NewsStatus } from './types'

export const liveColors = { yellow: '#eee34b', red: '#e54862' }

export function elapsed(since: string, now: number): string {
  const seconds = Math.max(0, Math.floor((now - Date.parse(since)) / 1000))
  if (seconds < 60) return `${seconds} s`
  const minutes = Math.floor(seconds / 60)
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`
}

export function coverageNote(unit: LiveUnit | undefined): string {
  if (!unit || unit.coverage === 'unknown') return 'Unknown: no reliable live status is available. This does not mean all clear.'
  if (unit.coverage === 'inherited') return unit.oblast_id === 'kyivska'
    ? 'Oblast-level alert. The Chornobyl zone inherits only Kyivska oblast-wide alerts.'
    : 'Oblast-level alert, e.g. Crimea permanent alert. Local status is inherited.'
  return 'Live status observed for this unit.'
}

export function liveDescription(unit: LiveUnit | undefined, now: number): string {
  if (!unit) return coverageNote(unit)
  const layers = (['red', 'yellow'] as const).flatMap(layer => {
    const since = unit[`${layer}_since`]
    return since ? [`${layer} since ${clock(since)} (${elapsed(since, now)})`] : []
  })
  return [layers.length ? layers.join(' · ') : unit.coverage === 'unknown' ? 'Status unknown' : 'No red or yellow alert',
    ...unit.other_hazards.map(hazard => hazard.replaceAll('_', ' ')), coverageNote(unit)].join('. ')
}

export function liveLabel(unit: LiveUnit | undefined, now: number): string {
  return unit ? `${unit.name}, ${oblastName(unit.oblast_id)}. ${liveDescription(unit, now)}` : coverageNote(unit)
}

export const newsStateLabels: Record<NewsStatus['state'], string> = {
  off: 'Off', needs_codex: 'Needs Codex', needs_login: 'Needs login', starting: 'Starting',
  running: 'Running', stale: 'Stale', error: 'Error', quota_limited: 'Quota limited',
}
export const eventLabels: Record<NewsEventType, { icon: string; label: string }> = {
  bomber_takeoff: { icon: '✈', label: 'Bomber takeoff' }, mig31k_takeoff: { icon: '✈', label: 'MiG-31K takeoff' },
  drone_group: { icon: '◇', label: 'Drone group' }, ballistic: { icon: '↗', label: 'Ballistic threat' },
  missile_launch: { icon: '↗', label: 'Missile launch' }, all_clear: { icon: '✓', label: 'All clear' },
  other: { icon: '·', label: 'Other report' },
}

/** Channel links arrive from validated events; never turn a script URL into a link. */
export function sourceUrl(url: string): string | undefined {
  try { return ['https:', 'http:'].includes(new URL(url).protocol) ? url : undefined } catch { return undefined }
}
