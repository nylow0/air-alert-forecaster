import type { ForecastRow } from './types'

export type Horizon = 1 | 3 | 12
export const horizons: Horizon[] = [1, 3, 12]
export const hourMs = 3_600_000

const dateFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Kyiv', day: '2-digit', month: 'short', year: 'numeric',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'longOffset',
})
const clockFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Kyiv', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})
const dayFormatter = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Kyiv', day: 'numeric', month: 'short' })

export function localTime(t: string | number): string {
  return dateFormatter.format(new Date(t)).replace('GMT', 'UTC')
}
export function clock(t: string): string {
  return clockFormatter.format(new Date(t))
}
export function day(t: number): string {
  return dayFormatter.format(new Date(t))
}
export function probability(row: Pick<ForecastRow, 'p_1' | 'p_3' | 'p_12'>, h: Horizon): number {
  return h === 1 ? row.p_1 : h === 3 ? row.p_3 : row.p_12
}
export function actual(row: ForecastRow, h: Horizon): boolean | null {
  return h === 1 ? row.actual_onset_1 : h === 3 ? row.actual_onset_3 : row.actual_onset_12
}
export function number(value: number | null, digits = 3): string {
  return value === null ? '—' : value.toFixed(digits)
}
/** Keep extreme calibrated probabilities from reading as certainty; paint uses raw values. */
export function probabilityText(value: number | null, digits = 2): string {
  if (value === null) return '—'
  if (value < 0.01) return '<0.01'
  if (value > 0.99) return '>0.99'
  return value.toFixed(digits)
}
export function oblastName(id: string): string {
  return id.split('-').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ')
}
/** Linear interpolation in OKLCH, shared by the paths and gradient legend. */
export function probabilityColor(p: number): string {
  const v = Math.max(0, Math.min(1, p))
  return `oklch(${0.26 + 0.6 * v} ${0.05 + 0.12 * v} ${55 + 17 * v})`
}
