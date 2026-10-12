import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'
import type { ForecastResponse, HistoryResponse, Meta } from '../src/lib/types.ts'

const fixtureRoot = new URL('../src/lib/fixtures/', import.meta.url)
const realGeometry = new URL('../../data/geo/raions.geojson', import.meta.url)
const hour = 3_600_000

function fixture<T>(name: string): T {
  return JSON.parse(readFileSync(new URL(name, fixtureRoot), 'utf8')) as T
}

function shift(t: string | null, delta: number): string | null {
  return t === null ? null : new Date(Date.parse(t) + delta).toISOString().replace('.000Z', 'Z')
}

/** Dev-only route simulation. The browser uses exactly the production API paths. */
export function mockApi(): Plugin {
  return {
    name: 'airalert-dev-fixtures',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = new URL(req.url ?? '/', 'http://localhost')
        if (!url.pathname.startsWith('/api/')) return next()
        try {
          let body: unknown
          if (url.pathname === '/api/meta') body = fixture<Meta>('meta.json')
          else if (url.pathname === '/api/report') body = fixture<unknown>('report.json')
          else if (url.pathname === '/api/raions') {
            const real = existsSync(realGeometry)
            body = JSON.parse(readFileSync(real ? fileURLToPath(realGeometry) : new URL('raions.geojson', fixtureRoot), 'utf8')) as unknown
            res.setHeader('X-Airalert-Geometry', real ? 'real' : 'fixture')
          } else if (url.pathname === '/api/forecast') {
            const at = url.searchParams.get('at')
            const base = fixture<ForecastResponse>(at ? 'forecast_replay.json' : 'forecast_latest.json')
            if (!at) body = base
            else {
              const t = Date.parse(at)
              const meta = fixture<Meta>('meta.json')
              if (!Number.isFinite(t) || t % hour !== 0 || t < Date.parse(meta.replay.start) || t > Date.parse(meta.replay.end)) {
                res.statusCode = 400
                body = { detail: 'Replay time must be an hourly UTC timestamp inside the replay period.' }
              } else {
                const delta = t - Date.parse(base.at)
                body = {
                  ...base, at: shift(base.at, delta),
                  rows: base.rows.map((row, i) => {
                    // One positive factor for all horizons preserves P1 <= P3 <= P12.
                    const factor = 1 + 0.65 * Math.sin(delta / hour * 0.63 + i * 0.7)
                    const p = (value: number) => Math.min(0.99, Math.max(0, value * factor))
                    return { ...row, p_1: p(row.p_1), p_3: p(row.p_3), p_12: p(row.p_12),
                      active_since: shift(row.active_since, delta), end_p10: shift(row.end_p10, delta),
                      end_p50: shift(row.end_p50, delta), end_p90: shift(row.end_p90, delta) }
                  }),
                }
              }
            }
          } else if (url.pathname.startsWith('/api/raion/') && url.pathname.endsWith('/history')) {
            const id = decodeURIComponent(url.pathname.slice('/api/raion/'.length, -'/history'.length))
            const base = fixture<HistoryResponse>('history.json')
            const end = Date.parse(url.searchParams.get('to') ?? base.end)
            const start = Date.parse(url.searchParams.get('from') ?? base.start)
            if (!id || !Number.isFinite(start) || !Number.isFinite(end) || start > end) {
              res.statusCode = 400
              body = { detail: 'History requires a raion id and a valid from/to range.' }
            } else {
              const delta = end - Date.parse(base.end)
              body = { ...base, raion_id: id, start: new Date(start).toISOString(), end: new Date(end).toISOString(),
                series: base.series.map(point => ({ ...point, t: shift(point.t, delta)! }))
                  .filter(point => Date.parse(point.t) >= start && Date.parse(point.t) <= end),
                episodes: base.episodes.map(e => ({ start: shift(e.start, delta)!, end: shift(e.end, delta) }))
                  .filter(e => Date.parse(e.start) <= end && (e.end === null || Date.parse(e.end) >= start)),
              }
            }
          } else {
            res.statusCode = 404
            body = { detail: 'Unknown mock API route.' }
          }
          res.setHeader('Content-Type', 'application/json; charset=utf-8')
          res.setHeader('Cache-Control', 'no-store')
          res.end(JSON.stringify(body))
        } catch (error) {
          res.statusCode = 500
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: error instanceof Error ? error.message : 'Could not read mock fixtures.' }))
        }
      })
    },
  }
}
