<script lang="ts">
  import { onMount, untrack } from 'svelte'
  import MapView from './lib/MapView.svelte'
  import RaionList from './lib/RaionList.svelte'
  import RaionPanel from './lib/RaionPanel.svelte'
  import ModelReport from './lib/ModelReport.svelte'
  import LiveView from './lib/LiveView.svelte'
  import NewsCard from './lib/NewsCard.svelte'
  import SettingsPanel from './lib/SettingsPanel.svelte'
  import SystemFooter from './lib/SystemFooter.svelte'
  import { api } from './lib/api'
  import { Resource } from './lib/resource.svelte'
  import { horizons, hourMs, localTime, type Horizon } from './lib/format'
  import { gapBands, gapNotice, gapState } from './lib/gaps'
  import type { AuthStatus, ForecastResponse, HistoryResponse, LiveState, Meta, NewsFeed, RaionFeature, RaionFeatureCollection, Report, Settings, SystemStatus } from './lib/types'

  const initial = new URLSearchParams(window.location.search)
  const meta = new Resource<Meta>()
  const geo = new Resource<RaionFeatureCollection>()
  const forecast = new Resource<ForecastResponse>()
  const history = new Resource<HistoryResponse>()
  const replayHours = new Resource<number[]>()
  const report = new Resource<Report>()
  const live = new Resource<LiveState>()
  const news = new Resource<NewsFeed>()
  const system = new Resource<SystemStatus>()
  const settings = new Resource<Settings>()
  const auth = new Resource<AuthStatus>()
  let now = $state(Date.now())
  let settingsOpen = $state(false)
  let saving = $state(false)
  let saveError = $state<string | null>(null)
  let newsHidden = $state(readNewsDismissal())
  let adjusted = $state(false)
  let mode = $state<'replay' | 'latest'>(initial.get('mode') === 'replay' || (initial.has('at') && !initial.has('mode')) ? 'replay' : 'latest')
  let horizon = $state<Horizon>(parseHorizon(initial.get('horizon')))
  let timeMs = $state(Date.parse(initial.get('at') ?? '') || 0)
  let tab = $state(initial.get('tab') === 'report' ? 'report' : initial.get('tab') === 'live' ? 'live' : initial.get('tab') === 'map' || initial.has('mode') ? 'map' : 'live')
  let view = $state(initial.get('view') === 'list' ? 'list' : 'map')
  let selectedId = $state<string | null>(initial.get('raion'))
  let noDataFeature = $state<RaionFeature | null>(null)
  let playing = $state(false)
  let forecastRetry = $state(0)
  let historyRetry = $state(0)
  let reportRetry = $state(0)
  const mock = __MOCK_API__
  const start = $derived(meta.data ? Date.parse(meta.data.replay.start) : 0)
  const end = $derived(meta.data ? Date.parse(meta.data.replay.end) : 0)
  const gaps = $derived(meta.data?.data_gaps ?? [])
  const bands = $derived(gapBands(gaps, start, end))
  const requestedAt = $derived(!meta.data ? '' : mode === 'latest' ? new Date(Math.floor(now / hourMs) * hourMs).toISOString().replace('.000Z', 'Z') : new Date(timeMs || end).toISOString().replace('.000Z', 'Z'))
  const sourceNotice = $derived(gapNotice(gapState(Date.parse(requestedAt), gaps, horizon), horizon))
  const selectedFeature = $derived(noDataFeature ?? geo.data?.features.find(f => f.properties.raion_id === selectedId && selectedId !== null) ?? null)
  const selectedRow = $derived(forecast.data?.rows.find(row => row.raion_id === selectedId))
  const forecastMissing = $derived(forecast.status === 404 && !forecast.loading)
  const forecastPending = $derived(forecast.status === 503 && !forecast.loading)
  const validForecast = $derived(forecast.data && (forecast.loading || Date.parse(forecast.data.at) === Date.parse(requestedAt)) ? forecast.data : null)
  const ready = $derived(meta.data && geo.data && validForecast)
  const observationsUnknown = $derived(forecast.data ? gapState(Date.parse(forecast.data.at), gaps) === 'outage' : false)
  // Adjustments belong to exactly one current-hour base. Never apply them to replay.
  const canAdjust = $derived(mode === 'latest' && forecast.data?.mode === 'latest' && news.data?.status.enabled && news.data.status.state === 'running' && news.data.base_at !== null && Date.parse(news.data.base_at) === Date.parse(forecast.data.at) && news.data.adjustments.length > 0)
  const adjustments = $derived(new Map((canAdjust ? news.data?.adjustments ?? [] : []).map(row => [row.raion_id, row])))
  const displayRows = $derived((forecast.data?.rows ?? []).map(row => {
    const adjustment = adjusted && canAdjust ? adjustments.get(row.raion_id) : undefined
    return adjustment ? { ...row, p_1: adjustment.p_1, p_3: adjustment.p_3, p_12: adjustment.p_12 } : row
  }))
  const displayRow = $derived(displayRows.find(row => row.raion_id === selectedId))
  const selectedLive = $derived(live.data?.units.find(unit => unit.display_key === selectedFeature?.properties.display_key))

  function readNewsDismissal(): boolean {
    try { return localStorage.getItem('airalert-news-hidden') === '1' } catch { return false }
  }
  function dismissNews(hidden: boolean): void {
    newsHidden = hidden
    try { localStorage.setItem('airalert-news-hidden', hidden ? '1' : '0') } catch { /* This session still supports dismissal when storage is blocked. */ }
  }
  function openSettings(): void {
    settingsOpen = true
    void settings.load(signal => api.settings(signal))
    void auth.load(signal => api.auth(signal))
  }
  async function saveSettings(next: Settings): Promise<void> {
    if (saving || !settings.data) return
    const previous = $state.snapshot(settings.data)
    settings.data = next; saving = true; saveError = null
    try { settings.data = await api.saveSettings(next); await news.load(signal => api.news(signal)) }
    catch (cause) { settings.data = previous; saveError = cause instanceof Error ? cause.message : 'Could not save settings.' }
    finally { saving = false }
  }

  function parseHorizon(value: string | null): Horizon {
    const h = Number(value)
    return h === 1 || h === 12 ? h : 3
  }
  function clampTime(value: number): number {
    return Math.min(end, Math.max(start, Math.floor(value / hourMs) * hourMs))
  }
  function select(feature: RaionFeature): void {
    selectedId = feature.properties.raion_id
    noDataFeature = selectedId === null ? feature : null
  }
  async function storedHours(signal: AbortSignal, id: string, first: number, last: number): Promise<number[]> {
    // History lists actual issued hours. Its endpoint accepts at most 31 days per request.
    const times = new Set<number>()
    for (let from = first; from <= last; from += 31 * 24 * hourMs) {
      const to = Math.min(last, from + 31 * 24 * hourMs - hourMs)
      const data = await api.history(id, new Date(from).toISOString(), new Date(to).toISOString(), signal)
      for (const point of data.series) times.add(Date.parse(point.t))
    }
    return [...times].sort((a, b) => a - b)
  }
  function step(direction: number): void {
    if (mode !== 'replay' || !meta.data || forecast.loading) return
    const hours = replayHours.data
    if (!hours) { if (replayHours.error) timeMs = clampTime(timeMs + direction * hourMs); return }
    const next = direction > 0 ? hours.find(t => t > timeMs) : hours.findLast(t => t < timeMs)
    if (next !== undefined) timeMs = next
    else playing = false
  }
  function keyboard(event: KeyboardEvent): void {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
    if (settingsOpen || event.target instanceof Element && event.target.closest('input, select, textarea, [contenteditable], .history-chart, .raion-path, .table-scroll')) return
    if (tab === 'map' && mode === 'replay' && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
      event.preventDefault(); step(event.key === 'ArrowRight' ? 1 : -1)
    }
  }

  onMount(() => {
    void geo.load(signal => api.raions(signal))
    const refresh = () => {
      void meta.load(signal => api.meta(signal))
      void live.load(signal => api.live(signal))
      void news.load(signal => api.news(signal))
      void system.load(signal => api.system(signal))
      void auth.load(signal => api.auth(signal))
    }
    refresh()
    const poll = window.setInterval(refresh, 60_000)
    const clock = window.setInterval(() => now = Date.now(), 10_000)
    return () => { window.clearInterval(poll); window.clearInterval(clock); for (const resource of [meta, geo, forecast, history, replayHours, report, live, news, system, settings, auth]) resource.cancel() }
  })
  $effect(() => {
    if (meta.data) {
      const current = untrack(() => timeMs)
      timeMs = clampTime(current || end)
    }
  })
  $effect(() => {
    const at = requestedAt, retry = forecastRetry, available = Boolean(meta.data)
    if (!available || !at) return
    void retry
    const queryAt = mode === 'replay' ? at : undefined
    untrack(() => void forecast.load(signal => api.forecast(queryAt, signal)))
  })
  $effect(() => {
    const id = geo.data?.features.find(f => f.properties.raion_id)?.properties.raion_id
    const first = start, last = end, activeMode = mode
    if (activeMode === 'replay' && id && first && last) untrack(() => void replayHours.load(signal => storedHours(signal, id, first, last)))
  })
  $effect(() => {
    if (!forecastPending) return
    const timer = window.setTimeout(() => forecastRetry++, 2000)
    return () => window.clearTimeout(timer)
  })
  $effect(() => {
    if (!settings.data?.news_enabled || news.data?.status.state !== 'starting') return
    const timer = window.setInterval(() => void news.load(signal => api.news(signal)), 2000)
    return () => window.clearInterval(timer)
  })
  $effect(() => {
    const id = selectedId, at = forecast.data?.at, retry = historyRetry, first = start
    if (!id || !at) { history.cancel(); return }
    void retry
    const from = new Date(Math.max(first, Date.parse(at) - 7 * 24 * hourMs)).toISOString()
    untrack(() => void history.load(signal => api.history(id, from, at, signal)))
  })
  $effect(() => {
    const activeTab = tab, retry = reportRetry
    void retry
    if (activeTab === 'report') untrack(() => void report.load(signal => api.report(signal)))
  })
  $effect(() => {
    if (!meta.data || !requestedAt) return
    const url = new URL(window.location.href)
    url.searchParams.set('mode', mode); url.searchParams.set('horizon', String(horizon)); url.searchParams.set('at', requestedAt)
    if (selectedId) url.searchParams.set('raion', selectedId); else url.searchParams.delete('raion')
    url.searchParams.set('tab', tab)
    if (view === 'list') url.searchParams.set('view', view); else url.searchParams.delete('view')
    window.history.replaceState(null, '', url)
  })
  $effect(() => {
    if (!playing) return
    if (mode !== 'replay' || tab !== 'map') { playing = false; return }
    const timer = window.setInterval(() => {
      if (timeMs >= end) playing = false
      else step(1)
    }, 400)
    return () => window.clearInterval(timer)
  })
</script>

<svelte:window onkeydown={keyboard} />
<svelte:head><meta name="description" content="Replay hourly alert onset forecasts across Ukraine’s raions and inspect model evaluation." /></svelte:head>
<a class="skip-link" href="#main">Skip to dashboard</a>
<div class="page-scroll" class:live-page={tab === 'live'}><div class="app-shell">
  <header class="topbar"><div class="brand"><h1>Air Alert Forecaster</h1><p>Live alerts and hourly forecasts across Ukraine</p></div><nav class="tabs" aria-label="Dashboard pages"><button class:current={tab === 'live'} aria-current={tab === 'live' ? 'page' : undefined} onclick={() => tab = 'live'}>Live</button><button class:current={tab === 'map'} aria-current={tab === 'map' ? 'page' : undefined} onclick={() => tab = 'map'}>Forecast</button><button class:current={tab === 'report'} aria-current={tab === 'report' ? 'page' : undefined} onclick={() => tab = 'report'}>Model report</button></nav></header>
  {#if mock}<div class="fixture-notice" role="note">Development preview. Probabilities, history, and scores are synthetic fixtures.</div>{/if}
  <div class="workspace" class:has-news={!newsHidden && tab !== 'report'}><main id="main">
    {#if meta.error}<div class="error-state" role="alert"><p>{meta.error}</p><button onclick={() => void meta.load(signal => api.meta(signal))}>Retry metadata</button></div>{/if}
    {#if geo.error}<div class="error-state" role="alert"><p>{geo.error}</p><button onclick={() => void geo.load(signal => api.raions(signal))}>Retry map</button></div>{/if}
    {#if tab === 'live'}
      <LiveView geo={geo.data} live={live.data} {now} error={live.error} retry={() => void live.load(signal => api.live(signal))} />
    {:else if tab === 'map'}
      <div class="controls"><div class="control-group"><span>Mode</span><div class="segmented" aria-label="Forecast mode">{#each ['replay', 'latest'] as m}<button class:current={mode === m} aria-pressed={mode === m} onclick={() => { mode = m === 'latest' ? 'latest' : 'replay'; playing = false }}>{m === 'replay' ? 'Replay' : 'Now'}</button>{/each}</div></div><div class="control-group"><span>Alert starts within</span><div class="segmented" aria-label="Forecast horizon">{#each horizons as h}<button class:current={horizon === h} aria-pressed={horizon === h} onclick={() => horizon = h}>{h} h</button>{/each}</div></div><div class="view-control segmented" aria-label="Map or list view"><button class:current={view === 'map'} aria-pressed={view === 'map'} onclick={() => view = 'map'}>Map</button><button class:current={view === 'list'} aria-pressed={view === 'list'} onclick={() => view = 'list'}>List</button></div></div>
      {#if canAdjust}<div class="adjustment-control"><div class="segmented" aria-label="Forecast adjustment"><button class:current={!adjusted} aria-pressed={!adjusted} onclick={() => adjusted = false}>Base</button><button class:current={adjusted} aria-pressed={adjusted} onclick={() => adjusted = true}>News-adjusted (experimental)</button></div></div>{/if}
      {#if meta.data}
        {#if mode === 'replay'}
          <section class="replay-controls" aria-label="Replay timeline">
            <div class="transport">
              <button aria-label="Step back one hour" title="Step back one hour" disabled={timeMs <= start} onclick={() => { playing = false; step(-1) }}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 4v12M15 4l-8 6 8 6z" /></svg></button>
              <button class="play-button" aria-label={playing ? 'Pause replay' : 'Play replay'} aria-pressed={playing} onclick={() => { if (timeMs >= end) timeMs = start; playing = !playing }}><svg viewBox="0 0 20 20" aria-hidden="true">{#if playing}<path d="M6 4v12M14 4v12" />{:else}<path d="M6 3l11 7-11 7z" />{/if}</svg></button>
              <button aria-label="Step forward one hour" title="Step forward one hour" disabled={timeMs >= end} onclick={() => { playing = false; step(1) }}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M15 4v12M5 4l8 6-8 6z" /></svg></button>
            </div>
            <div class="time-readout"><time datetime={requestedAt}>{localTime(requestedAt)}</time><small>Kyiv time / stored forecast replay</small></div>
            <div class="scrubber">
              <label class="sr-only" for="replay-time">Replay hour</label>
              <div class="scrubber-track">
                <input id="replay-time" type="range" min={start} max={end} step={hourMs} value={timeMs} aria-valuetext={localTime(timeMs)} aria-describedby={bands.length ? 'gap-key' : undefined} oninput={event => { playing = false; timeMs = Number(event.currentTarget.value) }} />
                <div class="scrubber-gaps" aria-hidden="true">{#each bands as band}<span class="gap-band" style:left={`${band.left}%`} style:width={`${band.width}%`}></span>{/each}</div>
              </div>
              <div class="scrubber-labels"><span>{localTime(start).split(',')[0]}</span><span>{localTime(end).split(',')[0]}</span></div>
              {#if bands.length}<span class="scrubber-gap-key" id="gap-key"><i class="gap-key"></i>Shaded: source outage</span>{/if}
            </div>
            {#if sourceNotice}<p class="source-notice" role="note">{sourceNotice}</p>{/if}
          </section>
        {:else}<section class="latest-notice"><div><time datetime={requestedAt}>{localTime(requestedAt)}</time><small>Kyiv time / current hourly forecast</small></div><p>Live alerts update every minute. Forecast probabilities update on the hour.</p></section>{#if sourceNotice}<p class="source-notice latest-source-notice" role="note">{sourceNotice}</p>{/if}{/if}
      {/if}
      {#if forecastMissing}<div class="empty-state" role="status"><p>No forecast stored for this hour.</p><p>Choose another hour. Stepping and playback use stored forecasts.</p></div>
      {:else if forecastPending}<p class="empty-state" role="status">Preparing this hour's forecast. Checking again shortly.</p>
      {:else if forecast.error}<div class="error-state" role="alert"><p>{forecast.error}</p><button onclick={() => forecastRetry++}>Retry forecast</button></div>{/if}
      <div class="dashboard" aria-busy={forecast.loading || geo.loading}>
        <section class="geography" aria-label="Forecasts by raion"><div class="map-heading"><h2>{view === 'map' ? 'Alert onset across Ukraine' : 'Raion forecasts'}</h2><span class="muted">{forecast.loading ? 'Updating…' : forecast.data ? `${forecast.data.rows.length} raions` : 'Loading…'}</span></div>
          <div class:dimmed={forecast.loading || geo.loading}>
            {#if ready && geo.data && validForecast}{#if view === 'map'}<MapView geo={geo.data} rows={displayRows} live={live.data} {now} {horizon} {observationsUnknown} mode={validForecast.mode} selected={selectedId} onselect={select} />{:else}<RaionList geo={geo.data} rows={displayRows} {horizon} {observationsUnknown} selected={selectedId} onselect={select} />{/if}{#if validForecast.rows.length === 0}<p class="empty-state">No forecasts are available for this hour. Choose another time.</p>{/if}
            {:else if meta.loading || geo.loading || forecast.loading}<div class="initial-loading">Loading the map and hourly forecasts…</div>{/if}
          </div>
          {#if validForecast}<div class="served-models"><span>Onset {validForecast.onset_model}</span><span>Duration {validForecast.duration_model ?? 'unavailable'}</span><span>Probabilities describe alert starts, including when an alert is already active.</span></div>{/if}
        </section>
        <div class:dimmed={forecast.loading}><RaionPanel feature={selectedFeature} row={validForecast ? displayRow : undefined} unavailable={!validForecast} baseRow={selectedRow} adjustment={canAdjust ? adjustments.get(selectedId ?? '') : undefined} news={news.data} liveUnit={selectedLive} {now} at={validForecast?.at ?? requestedAt} mode={mode} {horizon} {gaps} history={history.data} loading={history.loading} error={history.error} retry={() => historyRetry++} /></div>
      </div>
    {:else}
      {#if report.error}<div class="error-state" role="alert"><p>{report.error}</p><button onclick={() => reportRetry++}>Retry report</button></div>{/if}
      <div class:dimmed={report.loading} aria-busy={report.loading}>{#if report.data}<ModelReport report={report.data} meta={meta.data} />{:else if report.loading}<p class="initial-loading">Loading model evaluation…</p>{/if}</div>
    {/if}
  </main>{#if !newsHidden && tab !== 'report'}<NewsCard feed={news.data} {now} error={news.error} onclose={() => dismissNews(true)} opensettings={openSettings} />{/if}</div>
  <footer><p>Research demo. Use official alert channels for safety decisions.</p><SystemFooter system={system.data} {now} error={system.error} /></footer>
</div></div>
<div class="settings-dock"><button class="settings-button" aria-label="Open settings" aria-haspopup="dialog" onclick={openSettings}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z"/><circle cx="12" cy="12" r="3"/></svg>Settings</button></div>
{#if settingsOpen}<SettingsPanel settings={settings.data} auth={auth.data} {saving} error={saveError} loadError={settings.error} authLoadError={auth.error} onclose={() => settingsOpen = false} save={saveSettings} refreshauth={async () => { await auth.load(signal => api.auth(signal)); await news.load(signal => api.news(signal)) }} reopennews={() => dismissNews(false)} />{/if}
