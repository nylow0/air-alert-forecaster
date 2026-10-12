<script lang="ts">
  import { onMount, untrack } from 'svelte'
  import MapView from './lib/MapView.svelte'
  import RaionList from './lib/RaionList.svelte'
  import RaionPanel from './lib/RaionPanel.svelte'
  import ModelReport from './lib/ModelReport.svelte'
  import { api } from './lib/api'
  import { Resource } from './lib/resource.svelte'
  import { horizons, hourMs, localTime, type Horizon } from './lib/format'
  import { gapBands, gapState } from './lib/gaps'
  import type { ForecastResponse, HistoryResponse, Meta, RaionFeature, RaionFeatureCollection, Report } from './lib/types'

  const initial = new URLSearchParams(window.location.search)
  const meta = new Resource<Meta>()
  const geo = new Resource<RaionFeatureCollection>()
  const forecast = new Resource<ForecastResponse>()
  const history = new Resource<HistoryResponse>()
  const report = new Resource<Report>()
  let mode = $state<'replay' | 'latest'>(initial.get('mode') === 'latest' ? 'latest' : 'replay')
  let horizon = $state<Horizon>(parseHorizon(initial.get('horizon')))
  let timeMs = $state(Date.parse(initial.get('at') ?? '') || 0)
  let tab = $state(initial.get('tab') === 'report' ? 'report' : 'map')
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
  const requestedAt = $derived(!meta.data ? '' : mode === 'latest' ? meta.data.latest_at : new Date(timeMs || end - 14 * hourMs).toISOString().replace('.000Z', 'Z'))
  const sourceState = $derived(gapState(Date.parse(requestedAt), gaps))
  const selectedFeature = $derived(noDataFeature ?? geo.data?.features.find(f => f.properties.raion_id === selectedId && selectedId !== null) ?? null)
  const selectedRow = $derived(forecast.data?.rows.find(row => row.raion_id === selectedId))
  const ready = $derived(meta.data && geo.data && forecast.data)
  const observationsUnknown = $derived(forecast.data ? gapState(Date.parse(forecast.data.at), gaps) === 'outage' : false)

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
  function step(direction: number): void {
    if (mode === 'replay' && meta.data) timeMs = clampTime(timeMs + direction * hourMs)
  }
  function keyboard(event: KeyboardEvent): void {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
    if (event.target instanceof Element && event.target.closest('input, select, textarea, [contenteditable], .history-chart, .raion-path, .table-scroll')) return
    if (tab === 'map' && mode === 'replay' && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
      event.preventDefault(); step(event.key === 'ArrowRight' ? 1 : -1)
    }
  }

  onMount(() => {
    void meta.load(signal => api.meta(signal))
    void geo.load(signal => api.raions(signal))
    return () => { meta.cancel(); geo.cancel(); forecast.cancel(); history.cancel(); report.cancel() }
  })
  $effect(() => {
    if (meta.data) {
      const current = untrack(() => timeMs)
      timeMs = clampTime(current || end - 14 * hourMs)
    }
  })
  $effect(() => {
    const at = requestedAt, retry = forecastRetry, available = meta.data
    if (!available || !at) return
    void retry
    const queryAt = mode === 'replay' ? at : undefined
    untrack(() => void forecast.load(signal => api.forecast(queryAt, signal)))
  })
  $effect(() => {
    const id = selectedId, at = forecast.data?.at, retry = historyRetry
    if (!id || !at) { history.cancel(); return }
    void retry
    const from = new Date(Date.parse(at) - 7 * 24 * hourMs).toISOString()
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
    if (tab === 'report') url.searchParams.set('tab', tab); else url.searchParams.delete('tab')
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
<div class="app-shell">
  <header class="topbar"><div class="brand"><h1>Air Alert Forecaster</h1><p>Hourly forecasts across Ukraine</p></div><nav class="tabs" aria-label="Dashboard pages"><button class:current={tab === 'map'} aria-current={tab === 'map' ? 'page' : undefined} onclick={() => tab = 'map'}>Map</button><button class:current={tab === 'report'} aria-current={tab === 'report' ? 'page' : undefined} onclick={() => tab = 'report'}>Model report</button></nav></header>
  {#if mock}<div class="fixture-notice" role="note">Development preview. Probabilities, history, and scores are synthetic fixtures.</div>{/if}
  <main id="main">
    {#if meta.error}<div class="error-state" role="alert"><p>{meta.error}</p><button onclick={() => void meta.load(signal => api.meta(signal))}>Retry metadata</button></div>{/if}
    {#if geo.error}<div class="error-state" role="alert"><p>{geo.error}</p><button onclick={() => void geo.load(signal => api.raions(signal))}>Retry map</button></div>{/if}
    {#if tab === 'map'}
      <div class="controls"><div class="control-group"><span>Mode</span><div class="segmented" aria-label="Forecast mode">{#each ['replay', 'latest'] as m}<button class:current={mode === m} aria-pressed={mode === m} onclick={() => { mode = m === 'latest' ? 'latest' : 'replay'; playing = false }}>{m === 'replay' ? 'Replay' : 'Latest'}</button>{/each}</div></div><div class="control-group"><span>Alert starts within</span><div class="segmented" aria-label="Forecast horizon">{#each horizons as h}<button class:current={horizon === h} aria-pressed={horizon === h} onclick={() => horizon = h}>{h} h</button>{/each}</div></div><div class="view-control segmented" aria-label="Map or list view"><button class:current={view === 'map'} aria-pressed={view === 'map'} onclick={() => view = 'map'}>Map</button><button class:current={view === 'list'} aria-pressed={view === 'list'} onclick={() => view = 'list'}>List</button></div></div>
      {#if meta.data}
        {#if mode === 'replay'}
          <section class="replay-controls" aria-label="Replay timeline">
            <div class="transport">
              <button aria-label="Step back one hour" title="Step back one hour" disabled={timeMs <= start} onclick={() => { playing = false; step(-1) }}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 4v12M15 4l-8 6 8 6z" /></svg></button>
              <button class="play-button" aria-label={playing ? 'Pause replay' : 'Play replay'} aria-pressed={playing} onclick={() => { if (timeMs >= end) timeMs = start; playing = !playing }}><svg viewBox="0 0 20 20" aria-hidden="true">{#if playing}<path d="M6 4v12M14 4v12" />{:else}<path d="M6 3l11 7-11 7z" />{/if}</svg></button>
              <button aria-label="Step forward one hour" title="Step forward one hour" disabled={timeMs >= end} onclick={() => { playing = false; step(1) }}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M15 4v12M5 4l8 6-8 6z" /></svg></button>
            </div>
            <div class="time-readout"><time datetime={requestedAt}>{localTime(requestedAt)}</time><small>Kyiv time / test period</small></div>
            <div class="scrubber">
              <label class="sr-only" for="replay-time">Replay hour</label>
              <div class="scrubber-track">
                <input id="replay-time" type="range" min={start} max={end} step={hourMs} value={timeMs} aria-valuetext={localTime(timeMs)} aria-describedby={bands.length ? 'gap-key' : undefined} oninput={event => { playing = false; timeMs = Number(event.currentTarget.value) }} />
                <div class="scrubber-gaps" aria-hidden="true">{#each bands as band}<span class="gap-band" style:left={`${band.left}%`} style:width={`${band.width}%`}></span>{/each}</div>
              </div>
              <div class="scrubber-labels"><span>{localTime(start).split(',')[0]}</span><span>{localTime(end).split(',')[0]}</span></div>
              {#if bands.length}<span class="scrubber-gap-key" id="gap-key"><i class="gap-key"></i>Shaded: source outage</span>{/if}
            </div>
            {#if sourceState}<p class="source-notice" role="note">{sourceState === 'outage' ? 'Source outage: no alert data for these hours. Forecasts here are not scored.' : 'History recovering after outage. Forecasts here are not scored.'}</p>{/if}
          </section>
        {:else}<section class="latest-notice"><div><time datetime={meta.data.latest_at}>{localTime(meta.data.latest_at)}</time><small>Kyiv time / latest dataset snapshot</small></div><p>This is a snapshot forecast. Alerts still running when the CSV was exported may be absent.</p></section>{#if sourceState}<p class="source-notice latest-source-notice" role="note">{sourceState === 'outage' ? 'Source outage: no alert data for these hours. Forecasts here are not scored.' : 'History recovering after outage. Forecasts here are not scored.'}</p>{/if}{/if}
      {/if}
      {#if forecast.error}<div class="error-state" role="alert"><p>{forecast.error}</p><button onclick={() => forecastRetry++}>Retry forecast</button></div>{/if}
      <div class="dashboard" aria-busy={forecast.loading || geo.loading}>
        <section class="geography" aria-label="Forecasts by raion"><div class="map-heading"><h2>{view === 'map' ? 'Alert onset across Ukraine' : 'Raion forecasts'}</h2><span class="muted">{forecast.loading ? 'Updating…' : forecast.data ? `${forecast.data.rows.length} raions` : 'Loading…'}</span></div>
          <div class:dimmed={forecast.loading || geo.loading}>
            {#if ready && geo.data && forecast.data}{#if view === 'map'}<MapView geo={geo.data} rows={forecast.data.rows} {horizon} {observationsUnknown} mode={forecast.data.mode} selected={selectedId} onselect={select} />{:else}<RaionList geo={geo.data} rows={forecast.data.rows} {horizon} {observationsUnknown} selected={selectedId} onselect={select} />{/if}{#if forecast.data.rows.length === 0}<p class="empty-state">No forecasts are available for this hour. Choose another time.</p>{/if}
            {:else if meta.loading || geo.loading || forecast.loading}<div class="initial-loading">Loading the map and hourly forecasts…</div>{/if}
          </div>
          {#if forecast.data}<div class="served-models"><span>Onset {forecast.data.onset_model}</span><span>Duration {forecast.data.duration_model ?? 'unavailable'}</span><span>Probabilities describe alert starts, including when an alert is already active.</span></div>{/if}
        </section>
        <div class:dimmed={forecast.loading}><RaionPanel feature={selectedFeature} row={selectedRow} at={forecast.data?.at ?? requestedAt} mode={forecast.data?.mode ?? mode} {horizon} {gaps} history={history.data} loading={history.loading} error={history.error} retry={() => historyRetry++} /></div>
      </div>
    {:else}
      {#if report.error}<div class="error-state" role="alert"><p>{report.error}</p><button onclick={() => reportRetry++}>Retry report</button></div>{/if}
      <div class:dimmed={report.loading} aria-busy={report.loading}>{#if report.data}<ModelReport report={report.data} />{:else if report.loading}<p class="initial-loading">Loading model evaluation…</p>{/if}</div>
    {/if}
  </main>
  <footer><p>Research demo. Use official alert channels for safety decisions.</p>{#if meta.data}<p>CSV snapshot {localTime(meta.data.snapshot)} Kyiv</p>{/if}</footer>
</div>
