import { request, type FullConfig } from '@playwright/test'
import type { Meta } from '../src/lib/types'

/** Fail before the browser workflows if the live API is absent or Vite serves the wrong target. */
export default async function globalSetup(config: FullConfig): Promise<void> {
  const target = process.env.E2E_TARGET ?? 'mock'
  const client = await request.newContext({ timeout: 5_000 })
  try {
    let backend: Meta | undefined
    if (target === 'live') {
      try {
        const response = await client.get('http://127.0.0.1:8000/api/meta')
        if (!response.ok()) throw new Error(`HTTP ${response.status()}: ${await response.text()}`)
        backend = await response.json() as Meta
      } catch (error) {
        throw new Error(
          'E2E_TARGET=live requires the prepared FastAPI backend at 127.0.0.1:8000. ' +
          'From the repository root, run uv run airalert serve --port 8000 before Playwright. ' +
          `GET /api/meta failed: ${error instanceof Error ? error.message : String(error)}`,
        )
      }
    }
    const baseURL = config.projects[0].use.baseURL
    if (!baseURL) throw new Error('The E2E baseURL is missing.')
    const response = await client.get(`${baseURL}/api/meta`)
    if (!response.ok()) throw new Error(`The ${target} frontend API is not ready: HTTP ${response.status()}`)
    const meta = await response.json() as Meta
    if (!meta.replay || !meta.latest_at || meta.raion_count <= 0) {
      throw new Error(`The ${target} API metadata lacks a replay range, latest hour, or raion universe.`)
    }
    if (backend && (backend.csv_sha256 !== meta.csv_sha256 || backend.latest_at !== meta.latest_at)) {
      throw new Error('The live Vite proxy metadata differs from FastAPI. Check VITE_MOCK_API and port 8000.')
    }
    const html = await client.get(baseURL)
    if (!html.ok()) throw new Error(`The ${target} frontend is not ready: HTTP ${html.status()}`)
  } finally {
    await client.dispose()
  }
}
