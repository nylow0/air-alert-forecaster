import { test, expect } from '@playwright/test'
import type { ForecastResponse, HistoryResponse, Meta, RaionFeatureCollection } from '../src/lib/types'

const hour = 3_600_000
const mock = (process.env.E2E_TARGET ?? 'mock') === 'mock'
const iso = (t: number) => new Date(t).toISOString().replace('.000Z', 'Z')

test('an unissued hour clears old probabilities and stepping and playback skip to stored hours', async ({ page }, testInfo) => {
  const meta = await page.request.get('/api/meta').then(response => response.json()) as Meta
  const geo = await page.request.get('/api/raions').then(response => response.json()) as RaionFeatureCollection
  const id = geo.features.find(feature => feature.properties.raion_id)?.properties.raion_id
  expect(id).toBeTruthy()
  const last = Date.parse(meta.replay.end)
  const first = Math.max(Date.parse(meta.replay.start), last - 30 * 24 * hour)
  const history = await page.request.get(`/api/raion/${id}/history?${new URLSearchParams({ from: iso(first), to: iso(last) })}`).then(response => response.json()) as HistoryResponse
  const stored = history.series.map(point => Date.parse(point.t))
  expect(stored.length).toBeGreaterThan(1)
  let previous: number, missing: number, next: number
  if (mock) {
    previous = stored[0]; missing = previous + hour; next = missing + hour
    await page.route('**/api/raion/**/history?*', async route => {
      const response = await route.fetch()
      const data = await response.json() as HistoryResponse
      await route.fulfill({ json: { ...data, series: data.series.filter(point => Date.parse(point.t) !== missing) } })
    })
    await page.route('**/api/forecast?*', async route => {
      if (Date.parse(new URL(route.request().url()).searchParams.get('at') ?? '') === missing) await route.fulfill({ status: 404, json: { detail: 'No forecast was issued for this hour.' } })
      else await route.continue()
    })
  } else {
    const index = stored.findIndex((t, i) => i > 0 && t - stored[i - 1] > hour)
    if (index < 1) {
      testInfo.annotations.push({ type: 'coverage', description: 'No unissued hours in the sampled live history. The mock exercises sparse storage.' })
      return
    }
    previous = stored[index - 1]; missing = previous + hour; next = stored[index]
    expect((await page.request.get(`/api/forecast?${new URLSearchParams({ at: iso(missing) })}`)).status()).toBe(404)
  }
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const open = async (at: number) => {
    await page.goto(`/?${new URLSearchParams({ tab: 'map', mode: 'replay', at: iso(at), raion: id! })}`)
  }
  await open(previous)
  await expect(page.locator('.ukraine-map')).toBeVisible()
  const slider = page.getByRole('slider', { name: 'Replay hour', exact: true })
  await slider.focus()
  await slider.press('ArrowRight')
  await expect(page.getByText('No forecast stored for this hour.', { exact: true })).toBeVisible()
  await expect(page.locator('.ukraine-map')).toHaveCount(0)
  await expect(page.locator('.probability-figures')).toHaveCount(0)
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.getByRole('button', { name: 'Step forward one hour' }).click()
  await expect.poll(() => new URL(page.url()).searchParams.get('at')).toBe(iso(next))
  await expect(page.locator('.ukraine-map')).toBeVisible()
  const expected = await page.request.get(`/api/forecast?${new URLSearchParams({ at: iso(next) })}`).then(response => response.json()) as ForecastResponse
  const row = expected.rows.find(row => row.raion_id === id)!
  await expect(page.locator(`.ukraine-map [data-raion-id="${id}"]`)).toHaveAttribute('aria-label', new RegExp(`${row.p_3 < 0.01 ? '<0.01' : row.p_3 > 0.99 ? '>0.99' : row.p_3.toFixed(2)} probability`))
  await expect(page.getByText('Breaks in the line mark hours with no stored forecast.', { exact: true })).toBeVisible()
  const segments = await page.locator('.history-chart g > path').getAttribute('d')
  expect(segments?.match(/M/g)?.length, 'The chart breaks instead of joining missing hours').toBeGreaterThan(1)
  await page.getByRole('button', { name: 'Step back one hour' }).click()
  await expect.poll(() => new URL(page.url()).searchParams.get('at')).toBe(iso(previous))
  await expect(page.locator('.dashboard')).toHaveAttribute('aria-busy', 'false')
  await page.getByRole('button', { name: 'Play replay' }).click()
  await expect.poll(() => Date.parse(new URL(page.url()).searchParams.get('at') ?? '')).toBeGreaterThan(missing)
  // Re-selecting Replay stops playback even if reaching the final stored hour already paused it.
  await page.getByRole('button', { name: 'Replay', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Play replay' })).toHaveAttribute('aria-pressed', 'false')
  expect(errors).toEqual([])
})
