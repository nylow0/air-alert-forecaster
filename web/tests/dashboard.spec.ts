import { test as base, expect, type APIRequestContext, type Locator, type Page } from '@playwright/test'
import type { ForecastResponse, ForecastRow, HistoryResponse, Meta, RaionFeature, RaionFeatureCollection, Report } from '../src/lib/types'

type Horizon = 1 | 3 | 12
const probabilityKeys = { 1: 'p_1', 3: 'p_3', 12: 'p_12' } as const
const outcomeKeys = { 1: 'actual_onset_1', 3: 'actual_onset_3', 12: 'actual_onset_12' } as const
const hour = 3_600_000
const horizons: Horizon[] = [1, 3, 12]

// Every workflow collects errors from navigation through its last assertion.
const test = base.extend<{ browserErrors: void }>({
  browserErrors: [async ({ page }, use) => {
    const errors: string[] = []
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
    page.on('pageerror', error => errors.push(error.message))
    // ErrorEvents without exception objects, such as ResizeObserver errors, evade pageerror.
    await page.exposeFunction('recordVerifierError', (message: string) => { errors.push(message) })
    await page.addInitScript(() => {
      const record = (message: string) => {
        void (window as unknown as { recordVerifierError: (message: string) => Promise<void> }).recordVerifierError(message)
      }
      window.addEventListener('error', event => { if (event.message) record(event.message) })
      window.addEventListener('unhandledrejection', event => record(String(event.reason)))
    })
    await use()
    expect(errors, 'Console errors and uncaught browser exceptions').toEqual([])
  }, { auto: true }],
})

let meta: Meta
let geo: RaionFeatureCollection
let forecast: ForecastResponse
let nextForecast: ForecastResponse
let report: Report

async function api<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(path)
  expect(response.ok(), `${path}: HTTP ${response.status()} ${await response.text()}`).toBeTruthy()
  return await response.json() as T
}

function forecastPath(at: string): string {
  return `/api/forecast?${new URLSearchParams({ at })}`
}

function iso(time: number): string {
  return new Date(time).toISOString().replace('.000Z', 'Z')
}

function kyivTime(time: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Kyiv', day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'longOffset',
  }).format(new Date(time)).replace('GMT', 'UTC')
}

function oblastName(id: string): string {
  return id.split('-').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ')
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function featureFor(id: string): RaionFeature {
  const feature = geo.features.find(item => item.properties.raion_id === id)
  expect(feature, `A polygon for ${id}`).toBeDefined()
  return feature!
}

function map(page: Page): Locator {
  return page.getByLabel(/^Ukraine raions\. Probability/)
}

function polygon(page: Page, feature: RaionFeature): Locator {
  const prefix = `${feature.properties.name}, ${oblastName(feature.properties.oblast_id)}.`
  return map(page).getByRole('button', { name: new RegExp(`^${escapeRegex(prefix)} `) })
}

async function fills(page: Page): Promise<Record<string, string | null>> {
  return map(page).getByRole('button').evaluateAll(elements => Object.fromEntries(
    elements.filter(element => element.hasAttribute('data-raion-id'))
      .map(element => [element.getAttribute('data-raion-id')!, element.getAttribute('fill')]),
  ))
}

async function ready(page: Page, at: string): Promise<void> {
  await expect.poll(() => new URL(page.url()).searchParams.get('at')).toBe(iso(Date.parse(at)))
  const timeline = page.getByRole('region', { name: 'Replay timeline' })
  await expect(timeline.locator('time')).toHaveAttribute('datetime', iso(Date.parse(at)))
  await expect(timeline.locator('time')).toHaveText(kyivTime(at))
  await expect(page.getByRole('region', { name: 'Forecasts by raion' }).locator('..')).toHaveAttribute('aria-busy', 'false')
  await expect(map(page)).toBeVisible()
}

async function assertLabels(page: Page, response: ForecastResponse, horizon: Horizon): Promise<void> {
  const labels = await map(page).getByRole('button').evaluateAll(elements => Object.fromEntries(
    elements.filter(element => element.hasAttribute('data-raion-id'))
      .map(element => [element.getAttribute('data-raion-id')!, element.getAttribute('aria-label')]),
  ))
  for (const row of response.rows) {
    expect(labels[row.raion_id], `${row.raion_id} at ${response.at}`).toContain(
      `${row[probabilityKeys[horizon]].toFixed(2)} probability within ${horizon} hours`,
    )
  }
}

async function assertChangedPaint(page: Page, before: Record<string, string | null>, previous: ForecastResponse, current: ForecastResponse): Promise<void> {
  const after = await fills(page)
  const oldRows = new Map(previous.rows.map(row => [row.raion_id, row]))
  const changed = current.rows.filter(row => oldRows.get(row.raion_id)?.p_3 !== row.p_3)
  expect(changed.length, 'The sampled API hours must have different probabilities').toBeGreaterThan(0)
  for (const row of changed) expect(after[row.raion_id], `Changed paint for ${row.raion_id}`).not.toBe(before[row.raion_id])
  await assertLabels(page, current, 3)
}

async function selectRaion(page: Page, request: APIRequestContext, row: ForecastRow = forecast.rows[0]): Promise<HistoryResponse> {
  await polygon(page, featureFor(row.raion_id)).click()
  const panel = page.getByRole('complementary', { name: 'Selected raion' })
  await expect(panel.getByRole('heading', { name: featureFor(row.raion_id).properties.name, exact: true })).toBeVisible()
  await expect.poll(() => new URL(page.url()).searchParams.get('raion')).toBe(row.raion_id)
  const history = await api<HistoryResponse>(request,
    `/api/raion/${row.raion_id}/history?${new URLSearchParams({ from: iso(Date.parse(forecast.at) - 7 * 24 * hour), to: forecast.at })}`,
  )
  await expect(panel.getByRole('slider', { name: /^Seven-day probability history/ })).toBeVisible()
  return history
}

async function openReport(page: Page): Promise<void> {
  await page.getByRole('navigation', { name: 'Dashboard pages' }).getByRole('button', { name: 'Model report' }).click()
  await expect(page.getByRole('heading', { name: 'Model report', exact: true })).toBeVisible()
  await expect.poll(() => new URL(page.url()).searchParams.get('tab')).toBe('report')
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
}

async function noOverflow(page: Page): Promise<void> {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }))
  expect(dimensions.document, 'Document horizontal overflow').toBeLessThanOrEqual(dimensions.viewport)
  expect(dimensions.body, 'Body horizontal overflow').toBeLessThanOrEqual(dimensions.viewport)
}

test.beforeAll(async ({ request }) => {
  ;[meta, geo, report] = await Promise.all([
    api<Meta>(request, '/api/meta'), api<RaionFeatureCollection>(request, '/api/raions'), api<Report>(request, '/api/report'),
  ])
  expect(meta.horizons).toEqual(horizons)
  // Look for adjacent varying hours, bounded to one day. Avoid outages and leave room for transport controls.
  const start = Date.parse(meta.replay.start), end = Date.parse(meta.replay.end)
  let previous = await api<ForecastResponse>(request, forecastPath(iso(start + hour)))
  let pairFound = false
  for (let offset = 2; offset <= 25 && start + (offset + 4) * hour <= end; offset++) {
    const at = start + offset * hour
    if (meta.data_gaps.some(gap => at >= Date.parse(gap.start) && at <= Date.parse(gap.end))) continue
    const current = await api<ForecastResponse>(request, forecastPath(iso(at)))
    const oldRows = new Map(previous.rows.map(row => [row.raion_id, row.p_3]))
    if (Date.parse(current.at) - Date.parse(previous.at) === hour && current.rows.some(row => oldRows.get(row.raion_id) !== row.p_3)) {
      forecast = previous; nextForecast = current; pairFound = true; break
    }
    previous = current
  }
  expect(pairFound, 'No adjacent varying forecast hours in the first 25 replay hours. Time-colour verification requires varying API data.').toBeTruthy()
  expect(forecast.rows.length).toBe(meta.raion_count)
})

test.beforeEach(async ({ page }) => {
  await page.goto(`/?${new URLSearchParams({ mode: 'replay', horizon: '3', at: forecast.at })}`)
  await ready(page, forecast.at)
  const mockNotice = page.getByText('Development preview. Probabilities, history, and scores are synthetic fixtures.', { exact: true })
  if ((process.env.E2E_TARGET ?? 'mock') === 'mock') await expect(mockNotice).toBeVisible()
  else await expect(mockNotice).toHaveCount(0)
})

test('map covers every forecast raion exactly once and null polygons have no data', async ({ page }) => {
  const rendered = await map(page).getByRole('button').evaluateAll(elements => elements.map(element => ({
    id: element.getAttribute('data-raion-id'), label: element.getAttribute('aria-label'),
    tag: element.tagName.toLowerCase(), path: element.getAttribute('d'), fill: element.getAttribute('fill'),
    width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height,
  })))
  expect(rendered).toHaveLength(geo.features.length)
  expect(rendered.filter(item => item.id !== null).map(item => item.id).sort()).toEqual(forecast.rows.map(row => row.raion_id).sort())
  for (const item of rendered) {
    expect(item.tag).toBe('path')
    expect(item.path).toBeTruthy()
    expect(item.width).toBeGreaterThan(0)
    expect(item.height).toBeGreaterThan(0)
    expect(item.label).toBeTruthy()
    if (item.id === null) { expect(item.fill).toBe('#111'); expect(item.label).toContain('No data') }
    else { expect(item.label).toContain(featureFor(item.id).properties.name); expect(item.fill).not.toBe('#111') }
  }
  await assertLabels(page, forecast, 3)
})

test('all horizon controls update probability colours and the shareable URL', async ({ page }) => {
  let before = await fills(page)
  let previousHorizon: Horizon = 3
  for (const horizon of [1, 3, 12] as const) {
    await page.getByRole('button', { name: `${horizon} h`, exact: true }).click()
    await expect(page.getByRole('button', { name: `${horizon} h`, exact: true })).toHaveAttribute('aria-pressed', 'true')
    await expect.poll(() => new URL(page.url()).searchParams.get('horizon')).toBe(String(horizon))
    await assertLabels(page, forecast, horizon)
    const after = await fills(page)
    const changed = forecast.rows.filter(row => row[probabilityKeys[previousHorizon]] !== row[probabilityKeys[horizon]])
    expect(changed.length, 'The API horizons must differ').toBeGreaterThan(0)
    for (const row of changed) expect(after[row.raion_id]).not.toBe(before[row.raion_id])
    before = after; previousHorizon = horizon
  }
})

test('scrubbing to another hour changes time, URL, and forecast colours', async ({ page }) => {
  const before = await fills(page)
  const scrubber = page.getByRole('slider', { name: 'Replay hour', exact: true })
  await scrubber.focus()
  await scrubber.press('ArrowRight')
  await ready(page, nextForecast.at)
  await expect(scrubber).toHaveValue(String(Date.parse(nextForecast.at)))
  await assertChangedPaint(page, before, forecast, nextForecast)
})

test('step buttons move one hour in either direction and stop at replay bounds', async ({ page }) => {
  const before = await fills(page)
  await page.getByRole('button', { name: 'Step forward one hour' }).click()
  await ready(page, nextForecast.at)
  await assertChangedPaint(page, before, forecast, nextForecast)
  await page.getByRole('button', { name: 'Step back one hour' }).click()
  await ready(page, forecast.at)
  expect(await fills(page)).toEqual(before)
  const scrubber = page.getByRole('slider', { name: 'Replay hour', exact: true })
  await scrubber.press('Home')
  await ready(page, meta.replay.start)
  await expect(page.getByRole('button', { name: 'Step back one hour' })).toBeDisabled()
  await scrubber.press('End')
  await ready(page, meta.replay.end)
  await expect(page.getByRole('button', { name: 'Step forward one hour' })).toBeDisabled()
})

test('play advances hourly forecasts and pause keeps the hour stable', async ({ page, request }) => {
  await page.getByRole('button', { name: 'Play replay' }).click()
  await expect(page.getByRole('button', { name: 'Pause replay' })).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => Date.parse(new URL(page.url()).searchParams.get('at') ?? '')).toBeGreaterThan(Date.parse(forecast.at))
  await page.getByRole('button', { name: 'Pause replay' }).click()
  const at = new URL(page.url()).searchParams.get('at')!
  await ready(page, at)
  const displayed = await api<ForecastResponse>(request, forecastPath(at))
  await assertLabels(page, displayed, 3)
  const pausedFills = await fills(page)
  // Three playback intervals is the observation window, with no clock or timer replacements.
  await page.waitForTimeout(1_250)
  await expect(page.getByRole('button', { name: 'Play replay' })).toHaveAttribute('aria-pressed', 'false')
  await ready(page, at)
  expect(await fills(page)).toEqual(pausedFills)
})

test('clicking a polygon shows API probabilities, replay outcomes, and history', async ({ page, request }, testInfo) => {
  const row = forecast.rows.find(item => item.active) ?? forecast.rows[0]
  const history = await selectRaion(page, request, row)
  const panel = page.getByRole('complementary', { name: 'Selected raion' })
  for (const horizon of horizons) {
    await expect(panel.getByText(`${horizon} h`, { exact: true }).locator('..').locator('strong')).toHaveText(row[probabilityKeys[horizon]].toFixed(2))
    const outcome = row[outcomeKeys[horizon]]
    await expect(panel.getByText(`${horizon} h ${outcome === null ? 'Unknown' : outcome ? 'Yes' : 'No'}`, { exact: true })).toBeVisible()
  }
  await panel.getByText('History data and episodes', { exact: true }).click()
  const historyRows = panel.getByRole('region', { name: 'Hourly history data' }).getByRole('row').filter({ has: page.getByRole('cell') })
  await expect(historyRows).toHaveCount(history.series.length)
  const values = await historyRows.evaluateAll(rows => rows.map(row => [...row.querySelectorAll('th, td')].map(cell => cell.textContent?.trim())))
  expect(values).toEqual(history.series.map(point => [kyivTime(point.t), point.p_1.toFixed(2), point.p_3.toFixed(2), point.p_12.toFixed(2)]))
  await panel.getByText('History data and episodes', { exact: true }).click()
  await page.mouse.move(0, 0)
  await page.evaluate(() => document.fonts.ready)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: testInfo.outputPath('map.png'), fullPage: true })
})

test('keyboard polygon selection and history exploration preserve the forecast hour', async ({ page, request }) => {
  const row = forecast.rows[0], feature = featureFor(row.raion_id)
  const path = polygon(page, feature)
  await path.focus()
  await expect(page.getByRole('status')).toContainText(feature.properties.name)
  await expect(page.getByRole('status')).toContainText(row.p_3.toFixed(2))
  await path.press('Enter')
  await expect(path).toHaveAttribute('aria-pressed', 'true')
  const history = await api<HistoryResponse>(request, `/api/raion/${row.raion_id}/history?${new URLSearchParams({ from: iso(Date.parse(forecast.at) - 7 * 24 * hour), to: forecast.at })}`)
  expect(history.series.length, 'Hourly history for keyboard exploration').toBeGreaterThan(1)
  const chart = page.getByRole('slider', { name: /^Seven-day probability history/ })
  await chart.focus()
  await chart.press('ArrowLeft')
  const point = history.series[history.series.length - 2]
  await expect(chart).toHaveAttribute('aria-valuenow', String(history.series.length - 2))
  await expect(chart).toHaveAttribute('aria-valuetext', new RegExp(escapeRegex(kyivTime(point.t))))
  await expect(page.getByRole('complementary', { name: 'Selected raion' }).getByText(`P1 ${point.p_1.toFixed(2)} / P3 ${point.p_3.toFixed(2)} / P12 ${point.p_12.toFixed(2)}`, { exact: true })).toBeVisible()
  await ready(page, forecast.at)
})

test('list contains every raion sorted by the chosen horizon and opens its panel', async ({ page }) => {
  await page.getByRole('button', { name: 'List', exact: true }).click()
  await expect.poll(() => new URL(page.url()).searchParams.get('view')).toBe('list')
  const list = page.getByRole('region', { name: 'Raions sorted by probability' })
  const rows = list.getByRole('row').filter({ has: page.getByRole('cell') })
  for (const horizon of horizons) {
    await page.getByRole('button', { name: `${horizon} h`, exact: true }).click()
    await expect(rows).toHaveCount(forecast.rows.length)
    const displayed = await rows.evaluateAll(items => items.map(item => ({
      name: item.querySelector('button')?.childNodes[0].textContent?.trim(),
      cells: [...item.querySelectorAll('td')].map(cell => cell.textContent?.trim()),
    })))
    const sorted = [...forecast.rows].sort((a, b) => b[probabilityKeys[horizon]] - a[probabilityKeys[horizon]])
    expect(displayed).toEqual(sorted.map(row => ({
      name: featureFor(row.raion_id).properties.name,
      cells: [row.p_1.toFixed(2), row.p_3.toFixed(2), row.p_12.toFixed(2), row.active ? 'Active' : 'Quiet'],
    })))
  }
  const selected = [...forecast.rows].sort((a, b) => b.p_12 - a.p_12)[0]
  await rows.first().getByRole('button').click()
  await expect(page.getByRole('complementary', { name: 'Selected raion' }).getByRole('heading', { name: featureFor(selected.raion_id).properties.name, exact: true })).toBeVisible()
  await expect.poll(() => new URL(page.url()).searchParams.get('raion')).toBe(selected.raion_id)
})

test('every displayed onset metric matches the API at three decimal places', async ({ page }) => {
  await openReport(page)
  expect(report.onset.length, 'The prepared report needs onset evaluation scores').toBeGreaterThan(0)
  for (const split of ['val', 'test'] as const) {
    const rowSets = [...new Set(report.onset.filter(score => score.split === split).map(score => score.row_set))]
    for (const rowSet of rowSets) {
      const scores = report.onset.filter(score => score.split === split && score.row_set === rowSet)
      const caption = `${split === 'val' ? 'Validation' : 'Test'}, ${rowSet === 'full' ? 'full row set' : `row set ${rowSet}`}`
      const table = page.getByRole('region', { name: caption, exact: true }).getByRole('table')
      const tableHorizons = [...new Set(scores.map(score => score.horizon))].sort((a, b) => a - b)
      const models = [...new Set(scores.map(score => score.model))].sort()
      const rows = table.getByRole('row').filter({ has: page.getByRole('rowheader') })
      await expect(rows).toHaveCount(models.length)
      for (const score of scores) {
        const row = rows.filter({ has: page.getByRole('rowheader', { name: score.model, exact: true }) })
        const cells = row.getByRole('cell')
        const offset = tableHorizons.indexOf(score.horizon) * 4
        const values = [score.bss, score.brier, score.logloss, score.pr_auc]
        for (let index = 0; index < values.length; index++) {
          await expect(cells.nth(offset + index), `${split}/${rowSet}/${score.model}/${score.horizon} metric ${index}`).toHaveText(values[index] === null ? '—' : values[index]!.toFixed(3))
        }
      }
    }
  }
})

test('duration tables, model selection, and report notes match the API', async ({ page }) => {
  await openReport(page)
  for (const split of ['val', 'test'] as const) {
    const scores = report.duration.filter(score => score.split === split)
    const region = page.getByRole('region', { name: `${split} duration metrics`, exact: true })
    if (!scores.length) { await expect(region).toHaveCount(0); continue }
    const rows = region.getByRole('row').filter({ has: page.getByRole('rowheader') })
    await expect(rows).toHaveCount(scores.length)
    for (const score of scores) {
      const row = rows.filter({ has: page.getByRole('rowheader', { name: score.model, exact: true }) })
      await expect(row.getByRole('cell')).toHaveText([
        String(score.n), score.pinball_q10.toFixed(2), score.pinball_q50.toFixed(2),
        score.pinball_q90.toFixed(2), score.mae_q50_min.toFixed(2), `${(score.coverage_80 * 100).toFixed(1)}%`,
      ])
    }
  }
  if (report.selection) {
    await expect(page.getByRole('heading', { name: 'Selected models' })).toBeVisible()
    await expect(page.getByText(`Alert onset ${report.selection.onset ?? 'Not selected'}`, { exact: true })).toBeVisible()
    await expect(page.getByText(`Duration ${report.selection.duration ?? 'Not selected'}`, { exact: true })).toBeVisible()
    if (report.selection.note) await expect(page.getByText(report.selection.note, { exact: true })).toBeVisible()
  } else await expect(page.getByRole('heading', { name: 'Selected models' })).toHaveCount(0)
  for (const note of report.notes) await expect(page.getByText(note, { exact: true })).toBeVisible()
})

test('latest uses the snapshot and hides the replay timeline and outcomes', async ({ page, request }) => {
  await selectRaion(page, request)
  await page.getByRole('button', { name: 'Latest', exact: true }).click()
  const latest = await api<ForecastResponse>(request, '/api/forecast')
  expect(latest.mode).toBe('latest')
  expect(Date.parse(latest.at)).toBe(Date.parse(meta.latest_at))
  await expect.poll(() => new URL(page.url()).searchParams.get('mode')).toBe('latest')
  await expect.poll(() => new URL(page.url()).searchParams.get('at')).toBe(iso(Date.parse(meta.latest_at)))
  await expect(page.getByRole('slider', { name: 'Replay hour', exact: true })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Replay timeline' })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Forecasts by raion' }).locator('..')).toHaveAttribute('aria-busy', 'false')
  await expect(page.getByRole('heading', { name: 'What actually happened' })).toHaveCount(0)
  await expect(page.getByText(/^Alert actually started within/)).toHaveCount(0)
  const snapshotTime = page.locator('main time').first()
  await expect(snapshotTime).toHaveAttribute('datetime', meta.latest_at)
  await expect(snapshotTime).toHaveText(kyivTime(meta.snapshot))
  await assertLabels(page, latest, 3)
  expect(latest.rows.every(row => horizons.every(horizon => row[outcomeKeys[horizon]] === null))).toBeTruthy()
  await page.getByRole('button', { name: 'Replay', exact: true }).click()
  await ready(page, forecast.at)
})

test('map, selected panel, list, and report fit the viewport and deep links reload', async ({ page, request }, testInfo) => {
  await selectRaion(page, request)
  await noOverflow(page)
  await page.getByRole('button', { name: '12 h', exact: true }).click()
  await page.getByRole('button', { name: 'List', exact: true }).click()
  await noOverflow(page)
  const url = page.url()
  await page.reload()
  await expect(page.getByRole('region', { name: 'Raions sorted by probability' })).toBeVisible()
  await expect(page.getByRole('button', { name: '12 h', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('complementary', { name: 'Selected raion' }).getByRole('slider')).toBeVisible()
  expect(page.url()).toBe(url)
  await noOverflow(page)
  await openReport(page)
  await noOverflow(page)
  await page.evaluate(() => document.fonts.ready)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: testInfo.outputPath('report.png'), fullPage: true })
})
