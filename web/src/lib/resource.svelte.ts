/** Keeps the last successful render during refetch; superseded requests cannot overwrite it. */
export class Resource<T> {
  data = $state<T | null>(null)
  loading = $state(false)
  error = $state<string | null>(null)
  private controller: AbortController | undefined

  async load(fetcher: (signal: AbortSignal) => Promise<T>): Promise<void> {
    this.controller?.abort()
    const controller = new AbortController()
    this.controller = controller
    this.loading = true
    this.error = null
    try {
      const data = await fetcher(controller.signal)
      if (!controller.signal.aborted) this.data = data
    } catch (error) {
      if (!controller.signal.aborted) this.error = error instanceof Error ? error.message : 'Could not load data. Try again.'
    } finally {
      if (!controller.signal.aborted) this.loading = false
    }
  }

  cancel(): void {
    this.controller?.abort()
  }
}
