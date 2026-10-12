import type { ForecastResponse, HistoryResponse, Meta, RaionFeatureCollection, Report } from './types'

async function request<T>(path: string, label: string, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api/${path}`, { signal })
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
}
