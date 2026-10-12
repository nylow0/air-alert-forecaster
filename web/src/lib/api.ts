import type { AuthStatus, ForecastResponse, HistoryResponse, LiveState, Meta, NewsFeed, RaionFeatureCollection, Report, Settings, SystemStatus } from './types'

async function request<T>(path: string, label: string, signal?: AbortSignal, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api/${path}`, { ...init, signal })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new Error(`Could not reach the server to load ${label}. Try again.`)
  }
  if (!response.ok) throw new Error(`Could not load ${label}. Try again.`)
  return response.json() as Promise<T>
}

/** The only browser API client. Mock and real servers share these routes and types. */
export const api = {
  meta(signal?: AbortSignal) {
    return request<Meta>('meta', 'dashboard metadata', signal)
  },
  raions(signal?: AbortSignal) {
    return request<RaionFeatureCollection>('raions', 'the map', signal)
  },
  forecast(at: string | undefined, signal?: AbortSignal) {
    return request<ForecastResponse>(`forecast${at ? `?${new URLSearchParams({ at })}` : ''}`, 'the forecast', signal)
  },
  history(id: string, from: string, to: string, signal?: AbortSignal) {
    const encodedId = id.split('/').map(encodeURIComponent).join('/')
    return request<HistoryResponse>(`raion/${encodedId}/history?${new URLSearchParams({ from, to })}`, 'seven-day history', signal)
  },
  report(signal?: AbortSignal) {
    return request<Report>('report', 'the model report', signal)
  },
  live(signal?: AbortSignal) { return request<LiveState>('live', 'live alerts', signal) },
  system(signal?: AbortSignal) { return request<SystemStatus>('system', 'system status', signal) },
  news(signal?: AbortSignal) { return request<NewsFeed>('news', 'news events', signal) },
  settings(signal?: AbortSignal) { return request<Settings>('settings', 'settings', signal) },
  saveSettings(settings: Settings) {
    return request<Settings>('settings', 'saved settings', undefined, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings),
    })
  },
  auth(signal?: AbortSignal) { return request<AuthStatus>('auth/status', 'ChatGPT login status', signal) },
  authAction(action: 'login' | 'cancel' | 'logout') {
    return request<unknown>(`auth/${action}`, `ChatGPT ${action}`, undefined, { method: 'POST' })
  },
}
