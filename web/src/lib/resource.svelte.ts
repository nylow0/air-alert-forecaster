import { ApiError } from './api'

/** Keeps successful data during refetch, but clears unavailable HTTP 404/503 results. */
export class Resource<T> {
  data = $state<T | null>(null)
  loading = $state(false)
  error = $state<string | null>(null)
  status = $state<number | null>(null)
  private controller: AbortController | undefined

  async load(fetcher: (signal: AbortSignal) => Promise<T>): Promise<void> {
    this.controller?.abort()
    const controller = new AbortController()
    this.controller = controller
    this.loading = true
    this.error = null
    this.status = null
    try {
      const data = await fetcher(controller.signal)
      if (!controller.signal.aborted) this.data = data
    } catch (error) {
      if (!controller.signal.aborted) {
        this.error = error instanceof Error ? error.message : 'Could not load data. Try again.'
        this.status = error instanceof ApiError ? error.status : null
        if (this.status === 404 || this.status === 503) this.data = null
      }
    } finally {
      if (!controller.signal.aborted) this.loading = false
    }
  }

  cancel(): void {
    this.controller?.abort()
  }
}
