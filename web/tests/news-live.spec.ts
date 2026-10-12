import { test, expect } from '@playwright/test'
import type { NewsFeed, Settings } from '../src/lib/types'

/** The only live write workflow. Always restore the complete saved settings, even on failure. */
test('real news starts, runs, and stops after restoring the original settings', async ({ page }, testInfo) => {
  test.setTimeout(300_000)
  const response = await page.request.get('/api/settings')
  expect(response.ok()).toBe(true)
  const original = await response.json() as Settings
  expect(original.news_enabled, 'Start this round trip with news off').toBe(false)
  const authWrites: string[] = []
  page.on('request', request => {
    if (request.method() !== 'GET' && /\/api\/auth\//.test(request.url())) authWrites.push(request.url())
  })
  const states: string[] = []
  page.on('response', response => {
    if (response.url().endsWith('/api/news') && response.ok()) void response.json().then((feed: NewsFeed) => states.push(feed.status.state))
  })
  try {
    await page.goto('/')
    await page.getByRole('button', { name: 'Open settings' }).click()
    const panel = page.getByRole('dialog', { name: 'Settings' })
    const toggle = panel.getByRole('checkbox', { name: /^News agent/ })
    await expect(toggle).toBeVisible()
    const save = page.waitForResponse(response => response.url().endsWith('/api/settings') && response.request().method() === 'PUT')
    await toggle.click()
    const savedResponse = await save
    expect(savedResponse.ok(), `Settings PUT: HTTP ${savedResponse.status()} ${await savedResponse.text()}`).toBe(true)
    await expect(panel.getByRole('status')).toHaveText('Changes save automatically.')
    await panel.press('Escape')
    const card = page.getByRole('complementary', { name: 'News agent' })
    await expect.poll(() => states.includes('starting'), { timeout: 10_000 }).toBe(true)
    await expect(card.locator('.news-state')).toHaveText('Running', { timeout: 180_000 })
    const runningResponse = await page.request.get('/api/news')
    const running = await runningResponse.json() as NewsFeed
    expect(running.status.enabled).toBe(true)
    expect(running.status.state).toBe('running')
    for (const event of running.events.slice(0, 3)) await expect(card.getByText(event.summary, { exact: true })).toBeVisible()
    console.info(`Real news states=${[...new Set(states)].join(' -> ')}, events=${running.events.length}, adjustments=${running.adjustments.length}, last_extraction_at=${running.status.last_extraction_at}`)
    await page.screenshot({ path: testInfo.outputPath('news-running.png') })
    await page.getByRole('button', { name: 'Forecast', exact: true }).click()
    const forecast = await page.request.get('/api/forecast').then(response => response.json()) as { at: string }
    const adjustmentAvailable = running.status.enabled && running.base_at !== null && Date.parse(running.base_at) === Date.parse(forecast.at) && running.adjustments.length > 0
    await expect(page.getByRole('button', { name: 'News-adjusted (experimental)' })).toHaveCount(adjustmentAvailable ? 1 : 0)
    await page.getByRole('button', { name: 'Open settings' }).click()
    const off = page.waitForResponse(response => response.url().endsWith('/api/settings') && response.request().method() === 'PUT')
    await page.getByRole('dialog').getByRole('checkbox', { name: /^News agent/ }).uncheck()
    expect((await off).ok()).toBe(true)
    await page.getByRole('dialog').press('Escape')
    await expect(card).toContainText('News agent is off')
    const stopped = await page.request.get('/api/news').then(response => response.json()) as NewsFeed
    // Observe longer than the live display poll to detect a worker that kept running.
    await page.waitForTimeout(65_000)
    const later = await page.request.get('/api/news').then(response => response.json()) as NewsFeed
    expect(later.status.enabled).toBe(false)
    expect(later.status.state).toBe('off')
    expect(later.status.last_poll_at).toBe(stopped.status.last_poll_at)
    expect(later.status.last_extraction_at).toBe(stopped.status.last_extraction_at)
    console.info('Real news off remained stopped for 65 seconds.')
  } finally {
    const saved = await page.request.get('/api/settings').then(response => response.json()) as Settings
    if (JSON.stringify(saved) !== JSON.stringify(original)) {
      const restore = await page.request.put('/api/settings', { data: original })
      expect(restore.ok(), 'Restore the original complete settings').toBe(true)
    }
    expect(await page.request.get('/api/settings').then(response => response.json())).toEqual(original)
    expect(authWrites, 'No login, cancel, or logout against the real backend').toEqual([])
  }
})
