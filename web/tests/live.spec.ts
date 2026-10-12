import { test as base, expect, type Page } from '@playwright/test'
import type { AuthStatus, ForecastResponse, LiveState, NewsFeed, RaionFeatureCollection, Settings, SystemStatus } from '../src/lib/types'

const test = base.extend<{ browserErrors: void }>({
  browserErrors: [async ({ page }, use) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(() => { window.addEventListener('unhandledrejection', event => { throw event.reason }) })
    await use()
    expect(errors, 'Uncaught browser errors').toEqual([])
  }, { auto: true }],
})

// Auth actions and scenario controls are intentionally exercised only against dev middleware.
test.skip((process.env.E2E_TARGET ?? 'mock') !== 'mock', 'v2 state and auth workflows require the isolated mock API.')

async function read<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`/api/${path}`)
  expect(response.ok(), path).toBeTruthy()
  return await response.json() as T
}
async function scenario(page: Page, options: Record<string, string | boolean>): Promise<void> {
  const response = await page.request.post('/api/mock', { data: options })
  expect(response.ok()).toBeTruthy()
}
async function openSettings(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Open settings' }).click()
  await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible()
  await expect(page.getByRole('dialog').getByRole('checkbox', { name: /^News agent/ })).toBeVisible()
}
async function noOverflow(page: Page): Promise<void> {
  const dimensions = await page.evaluate(() => [document.documentElement.clientWidth, document.documentElement.scrollWidth, document.body.scrollWidth])
  expect(dimensions[1]).toBeLessThanOrEqual(dimensions[0])
  expect(dimensions[2]).toBeLessThanOrEqual(dimensions[0])
  const scroll = await page.locator('.page-scroll').evaluate(element => ({ width: element.clientWidth, content: element.scrollWidth }))
  expect(scroll.content, 'Page scroll area horizontal overflow').toBeLessThanOrEqual(scroll.width)
}

test('Live is the landing view; both independent maps cover the API units in yellow-over-red order', async ({ page }) => {
  await page.goto('/')
  const [live, geo] = await Promise.all([read<LiveState>(page, 'live'), read<RaionFeatureCollection>(page, 'raions')])
  expect(geo.features).toHaveLength(139)
  expect(live.units).toHaveLength(geo.features.length)
  await expect(page.getByRole('button', { name: 'Live', exact: true })).toHaveAttribute('aria-current', 'page')
  for (const layer of ['yellow', 'red'] as const) {
    const map = page.getByLabel(`Ukraine live ${layer} alerts`, { exact: true })
    await expect(map.getByRole('button')).toHaveCount(geo.features.length)
    const paths = await map.getByRole('button').evaluateAll(elements => elements.map(element => ({ key: element.getAttribute('data-display-key'), active: element.getAttribute('data-active'), coverage: element.getAttribute('data-coverage'), path: element.getAttribute('d') })))
    expect(paths.map(path => path.key).sort()).toEqual(live.units.map(unit => unit.display_key).sort())
    for (const unit of live.units) {
      const path = paths.find(path => path.key === unit.display_key)!
      expect(path.active).toBe(String(unit[`${layer}_since`] !== null))
      expect(path.coverage).toBe(unit.coverage)
      expect(path.path).toBeTruthy()
    }
    const count = live.units.filter(unit => unit[`${layer}_since`] !== null).length
    await expect(page.getByTestId(`${layer}-count`)).toHaveText(`${count} raions ${layer}`)
  }
  const yellow = await page.getByLabel('Ukraine live yellow alerts').boundingBox()
  const red = await page.getByLabel('Ukraine live red alerts').boundingBox()
  expect(red!.y).toBeGreaterThanOrEqual(yellow!.y + yellow!.height)
  const inherited = live.units.find(unit => unit.coverage === 'inherited')!
  await page.locator(`[data-layer="red"] [data-display-key="${inherited.display_key}"]`).hover()
  await expect(page.locator('.live-tooltip')).toContainText('Oblast-level alert')
  const tooltip = (await page.locator('.live-tooltip').boundingBox())!
  const viewport = (await page.locator('.page-scroll').boundingBox())!
  expect(tooltip.x).toBeGreaterThanOrEqual(0)
  expect(tooltip.x + tooltip.width).toBeLessThanOrEqual(viewport.width)
  expect(tooltip.y + tooltip.height).toBeLessThanOrEqual(viewport.y + viewport.height)
  await page.mouse.move(0, 0)
  await page.locator(`[data-layer="red"] [data-display-key="${inherited.display_key}"]`).focus()
  await expect(page.getByRole('status')).toContainText('Oblast-level alert')
  const unknown = live.units.find(unit => unit.coverage === 'unknown')!
  await page.locator(`[data-layer="yellow"] [data-display-key="${unknown.display_key}"]`).focus()
  await expect(page.getByRole('status').filter({ hasText: 'Unknown:' })).toContainText('does not mean all clear')
  await noOverflow(page)
  await page.reload()
  await expect(page.getByRole('button', { name: 'Live', exact: true })).toHaveAttribute('aria-current', 'page')
})

test('stale live data retains both maps and a calm delay banner', async ({ page }) => {
  await scenario(page, { stale: true })
  const live = await read<LiveState>(page, 'live')
  await page.goto('/')
  await expect(page.getByRole('status')).toContainText('Live updates are delayed')
  expect(live.stale).toBe(true)
  for (const layer of ['yellow', 'red'] as const) await expect(page.getByLabel(`Ukraine live ${layer} alerts`).getByRole('button')).toHaveCount(live.units.length)
})

test('live status polls after 60 seconds and failed polling keeps the last maps', async ({ page }) => {
  await page.clock.install()
  await page.goto('/')
  const live = await read<LiveState>(page, 'live')
  await expect(page.getByLabel('Ukraine live red alerts').getByRole('button')).toHaveCount(live.units.length)
  await page.route('**/api/live', route => route.abort())
  await page.clock.runFor(60_100)
  await expect(page.getByRole('status')).toContainText('Live updates are delayed')
  await expect(page.getByTestId('red-count')).toHaveText(`${live.units.filter(unit => unit.red_since !== null).length} raions red`)
})

test('news dismissal survives reload and settings restores the card; Esc restores keyboard focus', async ({ page }) => {
  await scenario(page, { news_state: 'running', auth: 'in' })
  await page.goto('/')
  const card = page.getByRole('complementary', { name: 'News agent' })
  await expect(card).toBeVisible()
  await card.getByRole('button', { name: 'Close news card' }).click()
  await expect(card).toHaveCount(0)
  await page.reload()
  await expect(card).toHaveCount(0)
  await openSettings(page)
  await page.getByRole('button', { name: 'Show news card again' }).click()
  await page.getByRole('dialog').press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Open settings' })).toBeFocused()
  await expect(card).toBeVisible()
  const feed = await read<NewsFeed>(page, 'news')
  for (const event of feed.events.slice(0, 3)) {
    await expect(card.getByText(event.summary, { exact: true })).toBeVisible()
    for (const source of event.sources) await expect(card.getByRole('link', { name: `@${source.handle}`, exact: true })).toHaveAttribute('href', source.url)
  }
})

test('maps, news, footer and Settings have separate space at mobile and desktop widths', async ({ page }) => {
  await scenario(page, { news_state: 'running', auth: 'in' })
  await page.goto('/')
  await expect(page.locator('.live-path')).toHaveCount(278)
  await page.evaluate(() => document.fonts.ready)
  const widths = page.viewportSize()!.width < 700 ? [390] : [1024, 1280, 1440]
  for (const width of widths) {
    await page.setViewportSize({ width, height: width < 700 ? 844 : 900 })
    await page.locator('.page-scroll').evaluate(element => element.scrollTop = 0)
    const scroll = (await page.locator('.page-scroll').boundingBox())!
    const dock = (await page.locator('.settings-dock').boundingBox())!
    const settings = (await page.getByRole('button', { name: 'Open settings', exact: true }).boundingBox())!
    expect(scroll.y + scroll.height).toBeLessThanOrEqual(dock.y)
    expect(settings.x).toBeLessThanOrEqual(24)
    expect(settings.y).toBeGreaterThanOrEqual(dock.y)
    const live = (await page.locator('.live-view').boundingBox())!
    const map = (await page.locator('.live-map-canvas').first().boundingBox())!
    expect(map.width).toBeCloseTo(live.width, 0)
    const card = page.getByRole('complementary', { name: 'News agent' })
    const news = (await card.boundingBox())!
    if (width < 1191) expect(news.y).toBeGreaterThanOrEqual(live.y + live.height)
    else {
      expect(news.x).toBeGreaterThanOrEqual(live.x + live.width)
      await page.locator('.live-key').evaluate(element => element.scrollIntoView({ block: 'nearest' }))
      const amount = await page.locator('.page-scroll').evaluate(element => element.scrollTop)
      expect(amount, 'Both maps and their key need at most a small scroll').toBeLessThanOrEqual(100)
      const yellow = (await page.getByLabel('Ukraine live yellow alerts').boundingBox())!
      const red = (await page.getByLabel('Ukraine live red alerts').boundingBox())!
      expect(yellow.y).toBeGreaterThanOrEqual(scroll.y)
      expect(red.y + red.height).toBeLessThanOrEqual(dock.y)
      const legend = (await page.locator('.live-key').boundingBox())!
      expect(legend.y + legend.height).toBeLessThanOrEqual(dock.y)
    }
    await card.getByRole('button', { name: 'Close news card' }).click()
    const expanded = (await page.locator('.live-map-canvas').first().boundingBox())!
    if (width >= 1191) expect(expanded.width).toBeGreaterThan(map.width + 200)
    else expect(expanded.width).toBeCloseTo(map.width, 0)
    await openSettings(page)
    const panel = page.getByRole('dialog')
    const bounds = (await panel.boundingBox())!
    expect(bounds.x).toBeGreaterThanOrEqual(0)
    expect(bounds.y).toBeGreaterThanOrEqual(0)
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(dock.y)
    const reopen = panel.getByRole('button', { name: 'Show news card again' })
    await reopen.scrollIntoViewIfNeeded()
    const action = (await reopen.boundingBox())!
    const heading = (await panel.locator('.settings-heading').boundingBox())!
    expect(action.y).toBeGreaterThanOrEqual(heading.y + heading.height)
    expect(action.y + action.height).toBeLessThanOrEqual(bounds.y + bounds.height)
    await reopen.click()
    await panel.press('Escape')
    await expect(card).toBeVisible()
    await page.getByRole('button', { name: 'Forecast', exact: true }).click()
    await expect(page.locator('.ukraine-map')).toBeVisible()
    const dashboard = (await page.locator('.dashboard').boundingBox())!
    const forecastNews = (await card.boundingBox())!
    if (width < 1400) expect(forecastNews.y).toBeGreaterThanOrEqual(dashboard.y + dashboard.height)
    else expect(forecastNews.x).toBeGreaterThanOrEqual(dashboard.x + dashboard.width)
    await page.locator('footer').scrollIntoViewIfNeeded()
    const safety = (await page.locator('footer > p').boundingBox())!
    expect(safety.y).toBeGreaterThanOrEqual(scroll.y)
    expect(safety.y + safety.height).toBeLessThanOrEqual(dock.y)
    await noOverflow(page)
    await page.getByRole('button', { name: 'Live', exact: true }).click()
  }
})

test('settings toggle and channel changes PUT their values; a failed save rolls back', async ({ page }) => {
  await page.goto('/')
  const original = await read<Settings>(page, 'settings')
  await openSettings(page)
  const panel = page.getByRole('dialog')
  const toggle = panel.getByRole('checkbox', { name: 'News agent', exact: false })
  const write = page.waitForRequest(request => request.url().endsWith('/api/settings') && request.method() === 'PUT')
  await toggle.setChecked(!original.news_enabled)
  expect((await write).postDataJSON() as Settings).toEqual({ ...original, news_enabled: !original.news_enabled })
  await expect(panel.getByRole('status')).toHaveText('Changes save automatically.')
  const saved = await read<Settings>(page, 'settings')
  expect(saved.news_enabled).toBe(!original.news_enabled)
  const channel = saved.channels[0]
  const channelWrite = page.waitForRequest(request => request.url().endsWith('/api/settings') && request.method() === 'PUT')
  await panel.getByRole('checkbox', { name: `Channel @${channel.handle}`, exact: true }).setChecked(!channel.enabled)
  expect(((await channelWrite).postDataJSON() as Settings).channels[0].enabled).toBe(!channel.enabled)
  await expect(panel.getByRole('status')).toHaveText('Changes save automatically.')
  const auth = await read<AuthStatus>(page, 'auth/status')
  await panel.getByLabel('Extraction model', { exact: true }).selectOption(auth.available_models[auth.available_models.length - 1])
  await expect(panel.getByRole('status')).toHaveText('Changes save automatically.')
  expect((await read<Settings>(page, 'settings')).models.extract_model).toBe(auth.available_models[auth.available_models.length - 1])
  await panel.getByLabel('Evaluation effort', { exact: true }).selectOption('medium')
  await expect(panel.getByRole('status')).toHaveText('Changes save automatically.')
  expect((await read<Settings>(page, 'settings')).models.evaluate_effort).toBe('medium')
  await panel.getByLabel('Poll interval, seconds', { exact: true }).fill(String(saved.poll_seconds + 60))
  await panel.getByLabel('Poll interval, seconds', { exact: true }).press('Tab')
  await expect(panel.getByRole('status')).toHaveText('Changes save automatically.')
  expect((await read<Settings>(page, 'settings')).poll_seconds).toBe(saved.poll_seconds + 60)
  await scenario(page, { fail_settings: true })
  await toggle.setChecked(original.news_enabled)
  await expect(panel.getByRole('alert')).toContainText('rolled back')
  await expect(toggle).toBeChecked({ checked: !original.news_enabled })
  expect((await read<Settings>(page, 'settings')).news_enabled).toBe(saved.news_enabled)
})

test('ChatGPT login polls to completion, supports cancellation, and confirms machine logout', async ({ page }) => {
  await page.goto('/')
  await openSettings(page)
  await page.getByRole('button', { name: 'Login with ChatGPT' }).click()
  await expect(page.getByText('Login in progress', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Cancel login' }).click()
  await expect(page.getByText('Not logged in', { exact: true })).toBeVisible()
  expect((await read<AuthStatus>(page, 'auth/status')).login_in_progress).toBe(false)
  await page.getByRole('button', { name: 'Login with ChatGPT' }).click()
  await expect(page.getByText('Logged in with ChatGPT', { exact: true })).toBeVisible()
  const auth = await read<AuthStatus>(page, 'auth/status')
  expect(auth.logged_in).toBe(true)
  const panel = page.getByRole('dialog')
  const options = await panel.getByLabel('Extraction model', { exact: true }).locator('option').evaluateAll(items => items.map(item => item.getAttribute('value')))
  expect(options).toEqual(auth.available_models)
  await panel.getByRole('button', { name: 'Logout', exact: true }).click()
  await expect(panel.getByRole('group', { name: 'Confirm logout' })).toContainText('signs out Codex on this machine')
  expect((await read<AuthStatus>(page, 'auth/status')).logged_in).toBe(true)
  await panel.getByRole('button', { name: 'Confirm logout', exact: true }).click()
  await expect(page.getByText('Not logged in', { exact: true })).toBeVisible()
})

test('missing Codex gives installation guidance', async ({ page }) => {
  await scenario(page, { auth: 'missing' })
  await page.goto('/')
  await openSettings(page)
  await expect(page.getByText('Codex is not installed', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Installation guide' })).toHaveAttribute('href', 'https://developers.openai.com/codex/cli/')
  await expect(page.getByRole('button', { name: 'Login with ChatGPT' })).toHaveCount(0)
})

test('news adjusts the matching Now forecast and panel, with linked events; replay stays base', async ({ page }) => {
  await scenario(page, { news_state: 'running', auth: 'in' })
  await page.goto('/?tab=map&mode=latest')
  const [feed, base, geo] = await Promise.all([read<NewsFeed>(page, 'news'), read<ForecastResponse>(page, 'forecast'), read<RaionFeatureCollection>(page, 'raions')])
  const adjustment = feed.adjustments[0]
  const feature = geo.features.find(feature => feature.properties.raion_id === adjustment.raion_id)!
  const path = page.locator(`.ukraine-map [data-raion-id="${adjustment.raion_id}"]`)
  await expect(path).toBeVisible()
  const baseFill = await path.getAttribute('fill')
  await page.getByRole('button', { name: 'News-adjusted (experimental)', exact: true }).click()
  await expect(path).not.toHaveAttribute('fill', baseFill!)
  await expect(path).toHaveAttribute('aria-label', new RegExp(`${adjustment.p_3.toFixed(2)} probability within 3 hours`))
  await path.click()
  const panel = page.getByRole('complementary', { name: 'Selected raion' })
  await expect(panel.getByRole('heading', { name: feature.properties.name, exact: true })).toBeVisible()
  const table = panel.getByRole('region', { name: 'Base and news-adjusted probabilities' }).getByRole('table')
  const row = base.rows.find(row => row.raion_id === adjustment.raion_id)!
  await expect(table.getByRole('row').filter({ has: page.getByRole('rowheader', { name: '3 h', exact: true }) }).getByRole('cell')).toHaveText([row.p_3.toFixed(2), adjustment.p_3.toFixed(2), `+${((adjustment.p_3 - row.p_3) * 100).toFixed(1)} pp`])
  for (const event of feed.events.filter(event => adjustment.event_ids.includes(event.id))) for (const source of event.sources) await expect(panel.getByRole('link', { name: `@${source.handle}`, exact: true })).toHaveAttribute('href', source.url)
  await noOverflow(page)
  await page.getByRole('button', { name: 'Replay', exact: true }).click()
  await expect(page.getByRole('button', { name: 'News-adjusted (experimental)' })).toHaveCount(0)
  await expect(panel.getByRole('region', { name: 'Base and news-adjusted probabilities' })).toHaveCount(0)
})

test('all news states are available through the mock and fit the viewport', async ({ page }) => {
  for (const state of ['off', 'needs_login', 'needs_codex', 'starting', 'running', 'stale', 'error', 'quota_limited']) {
    await scenario(page, { news_state: state })
    const feed = await read<NewsFeed>(page, 'news')
    await page.goto('/')
    const card = page.getByRole('complementary', { name: 'News agent' })
    if (state === 'off') await expect(card).toContainText('News agent is off')
    else await expect(card).toContainText(state.replaceAll('_', ' '), { ignoreCase: true })
    expect(feed.status.state).toBe(state)
    await noOverflow(page)
  }
})

test('system footer reports API data coverage and nightly time; capture desktop/mobile views', async ({ page }, testInfo) => {
  await scenario(page, { news_state: 'running', auth: 'in' })
  await page.goto('/')
  const system = await read<SystemStatus>(page, 'system')
  await expect(page.getByLabel('System status')).toContainText('Nightly update at 00:00 Kyiv')
  if (system.coverage_gaps.length) await expect(page.getByLabel('System status')).toContainText('Coverage gaps:')
  await expect(page.getByLabel('Ukraine live red alerts').getByRole('button')).toHaveCount(139)
  await page.evaluate(() => document.fonts.ready)
  await page.screenshot({ path: testInfo.outputPath('live-landing.png') })
  if (page.viewportSize()!.width < 700) {
    await page.locator('.page-scroll').evaluate(element => {
      const live = element.querySelector<HTMLElement>('.live-view')!
      element.scrollTop += live.getBoundingClientRect().top
    })
    await page.screenshot({ path: testInfo.outputPath('live-maps.png') })
  } else await page.locator('.live-key').evaluate(element => element.scrollIntoView({ block: 'nearest' }))
  await page.screenshot({ path: testInfo.outputPath('live.png') })
  await noOverflow(page)
  await page.getByRole('button', { name: 'Forecast', exact: true }).click()
  await expect(page.locator('.ukraine-map')).toBeVisible()
  const forecast = await read<ForecastResponse>(page, 'forecast')
  const sample = (await read<NewsFeed>(page, 'news')).adjustments[0]
  expect(forecast.rows.some(row => row.raion_id === sample.raion_id)).toBe(true)
  await page.locator(`.ukraine-map [data-raion-id="${sample.raion_id}"]`).click()
  await expect(page.getByRole('complementary', { name: 'Selected raion' }).getByRole('slider')).toBeVisible()
  await page.getByRole('button', { name: 'News-adjusted (experimental)', exact: true }).click()
  await page.mouse.move(4, 4)
  await page.screenshot({ path: testInfo.outputPath('forecast-news.png') })
  await page.getByRole('button', { name: 'Live', exact: true }).click()
  await page.locator('.page-scroll').evaluate(element => element.scrollTop = 0)
  await openSettings(page)
  await noOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('settings.png') })
})
