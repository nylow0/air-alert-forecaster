import { readFileSync } from 'node:fs'
import type { Plugin } from 'vite'
import type { AuthStatus, ForecastResponse, HistoryResponse, LiveState, Meta, NewsFeed, NewsStatus, Settings, SystemStatus } from '../src/lib/types.ts'

const fixtureRoot = new URL('../src/lib/fixtures/', import.meta.url)
const hour = 3_600_000

type MockSession = { settings: Settings; auth: AuthStatus; loginAt: number | null; stale: boolean; newsState: NewsStatus['state'] | null; failSettings: boolean }
const sessions = new Map<string, MockSession>()
let sessionId = 0

/** Shift fixed, schema-validated samples to the current hour without changing their intervals. */
function currentFixture<T>(name: string): T {
  const delta = Math.floor(Date.now() / hour) * hour - Date.parse('2026-10-12T17:00:00Z')
  const fixedDates = new Set(['data_through', 'trained_at', 'last_nightly_at', 'next_nightly_at'])
  return JSON.parse(JSON.stringify(fixture<unknown>(name)), (key: string, value: unknown) =>
    typeof value === 'string' && /^2026-10-1[12]T/.test(value) && !fixedDates.has(key)
      ? shift(value, delta) : value) as T
}

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
      server.middlewares.use(async (req, res, next) => {
        const url = new URL(req.url ?? '/', 'http://localhost')
        if (!url.pathname.startsWith('/api/')) return next()
        try {
          const cookie = req.headers.cookie?.match(/(?:^|; )airalert_mock=(\d+)/)?.[1]
          let session = cookie ? sessions.get(cookie) : undefined
          if (!session) {
            session = { settings: fixture<Settings>('settings.json'), auth: fixture<AuthStatus>('auth_logged_out.json'), loginAt: null, stale: false, newsState: null, failSettings: false }
            const scenario = new URL(req.headers.referer ?? 'http://localhost').searchParams.get('mock_news')
            if (scenario === 'running') { session.settings.news_enabled = true; session.auth = fixture<AuthStatus>('auth_logged_in.json') }
            sessions.set(String(++sessionId), session)
            res.setHeader('Set-Cookie', `airalert_mock=${sessionId}; Path=/; SameSite=Lax`)
          }
          const readBody = async (): Promise<unknown> => {
            let raw = ''
            for await (const chunk of req) raw += String(chunk)
            return JSON.parse(raw || '{}') as unknown
          }
          let body: unknown
          if (url.pathname === '/api/mock' && req.method === 'POST') {
            // Per-browser scenarios keep parallel e2e workflows isolated. Dev middleware only.
            const options = await readBody() as { stale?: boolean; news_state?: NewsStatus['state']; auth?: 'in' | 'out' | 'missing'; fail_settings?: boolean }
            if (options.stale !== undefined) session.stale = options.stale
            if (options.fail_settings !== undefined) session.failSettings = options.fail_settings
            if (options.news_state) { session.newsState = options.news_state; session.settings.news_enabled = options.news_state !== 'off' }
            if (options.auth) { session.auth = fixture<AuthStatus>(options.auth === 'in' ? 'auth_logged_in.json' : options.auth === 'missing' ? 'auth_missing.json' : 'auth_logged_out.json'); session.loginAt = null }
            body = { ok: true }
          } else if (url.pathname === '/api/live') {
            const state = currentFixture<LiveState>(session.stale ? 'live_stale.json' : 'live.json')
            body = { ...state, fetched_at: new Date(Date.now() - (session.stale ? 12 * 60_000 : 23_000)).toISOString() }
          }
          else if (url.pathname === '/api/system') {
            const system = currentFixture<SystemStatus>('system.json')
            const now = new Date()
            // Kyiv calendar midnight uses the IANA offset, including DST boundaries.
            const formatter = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Kyiv', year: 'numeric', month: '2-digit', day: '2-digit' })
            const today = formatter.format(now)
            const next = new Date(Math.floor(Date.now() / hour) * hour)
            do { next.setTime(next.getTime() + hour) } while (formatter.format(next) === today)
            body = { ...system, now: now.toISOString(), next_nightly_at: next.toISOString(), live_stale: session.stale }
          } else if (url.pathname === '/api/settings') {
            if (req.method === 'PUT') {
              const updated = await readBody() as Settings
              if (session.failSettings) { session.failSettings = false; res.statusCode = 503; body = { detail: 'Mock settings write failed.' } }
              else { session.settings = updated; session.newsState = null; body = updated }
            } else body = session.settings
          } else if (url.pathname === '/api/auth/status') {
            if (session.loginAt !== null && Date.now() - session.loginAt >= 2000) { session.auth = fixture<AuthStatus>('auth_logged_in.json'); session.loginAt = null }
            body = session.auth
          } else if (url.pathname === '/api/auth/login' && req.method === 'POST') {
            if (!session.auth.codex_installed) { res.statusCode = 409; body = { detail: 'Codex is not installed.' } }
            else { session.auth = fixture<AuthStatus>('auth_pending.json'); session.loginAt = Date.now(); body = { ok: true } }
          } else if (url.pathname === '/api/auth/cancel' && req.method === 'POST') { session.auth = fixture<AuthStatus>('auth_logged_out.json'); session.loginAt = null; body = { ok: true } }
          else if (url.pathname === '/api/auth/logout' && req.method === 'POST') { session.auth = fixture<AuthStatus>('auth_logged_out.json'); session.loginAt = null; body = { ok: true } }
          else if (url.pathname === '/api/news') {
            const state = session.newsState ?? (!session.settings.news_enabled ? 'off' : !session.auth.codex_installed ? 'needs_codex' : !session.auth.logged_in ? 'needs_login' : 'running')
            const feed = currentFixture<NewsFeed>(state === 'running' ? 'news.json' : `news_${state}.json`)
            body = { ...feed, status: { ...feed.status, models: session.settings.models } }
          } else if (url.pathname === '/api/meta') body = fixture<Meta>('meta.json')
          else if (url.pathname === '/api/report') body = fixture<unknown>('report.json')
          else if (url.pathname === '/api/raions') {
            body = fixture<unknown>('raions.geojson')
            res.setHeader('X-Airalert-Geometry', 'fixture')
          } else if (url.pathname === '/api/forecast') {
            const at = url.searchParams.get('at')
            const base = fixture<ForecastResponse>(at ? 'forecast_replay.json' : 'forecast_latest.json')
            if (!at) {
              const delta = Math.floor(Date.now() / hour) * hour - Date.parse(base.at)
              body = { ...base, at: shift(base.at, delta), rows: base.rows.map(row => ({ ...row, active_since: shift(row.active_since, delta), end_p10: shift(row.end_p10, delta), end_p50: shift(row.end_p50, delta), end_p90: shift(row.end_p90, delta) })) }
            }
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
