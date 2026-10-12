<script lang="ts">
  import HistoryChart from './HistoryChart.svelte'
  import { actual, clock, horizons, localTime, oblastName, probability, probabilityText, type Horizon } from './format'
  import { gapState } from './gaps'
  import { liveDescription, sourceUrl } from './live-format'
  import type { ForecastRow, HistoryResponse, LiveUnit, NewsAdjustmentRow, NewsFeed, RaionFeature, Range } from './types'
  let { feature, row, baseRow, adjustment, news, liveUnit, now, at, mode, horizon, gaps, history, loading, error, retry }: {
    feature: RaionFeature | null; row: ForecastRow | undefined; at: string
    baseRow: ForecastRow | undefined; adjustment: NewsAdjustmentRow | undefined; news: NewsFeed | null; liveUnit: LiveUnit | undefined; now: number
    mode: 'replay' | 'latest'; horizon: Horizon; history: HistoryResponse | null
    gaps: Range[]
    loading: boolean; error: string | null; retry: () => void
  } = $props()
  const sourceState = $derived(gapState(Date.parse(at), gaps))
  const remaining = $derived.by(() => {
    if (!row?.end_p10 || !row.end_p50 || !row.end_p90) return null
    return [row.end_p10, row.end_p50, row.end_p90].map(t => Math.round((Date.parse(t) - Date.parse(at)) / 60_000))
  })
</script>

<aside class="raion-panel" aria-label="Selected raion">
  {#if feature}
    <div class="panel-heading"><p class="muted">Selected raion</p><h2>{feature.properties.name}</h2><p>{oblastName(feature.properties.oblast_id)}</p></div>
    {#if row}
      <p class="probability-label">Probability an alert starts within</p>
      <div class="probability-figures">{#each horizons as h}<div class:current={h === horizon}><span>{h} h</span><strong>{probabilityText(probability(row, h))}</strong></div>{/each}</div>
      {#if baseRow && adjustment}
        <section class="news-comparison" aria-label="Base and news-adjusted probabilities"><h3>News adjustment · experimental</h3>
          <table><thead><tr><th>Within</th><th>Base</th><th>Adjusted</th><th>Change</th></tr></thead><tbody>{#each horizons as h}
            {@const base = probability(baseRow, h)}{@const value = probability(adjustment, h)}
            <tr><th>{h} h</th><td>{probabilityText(base)}</td><td>{probabilityText(value)}</td><td>{value >= base ? '+' : ''}{((value - base) * 100).toFixed(1)} pp</td></tr>
          {/each}</tbody></table><small>Change is in percentage points. Applied logit shifts: {adjustment.delta_1.toFixed(2)} / {adjustment.delta_3.toFixed(2)} / {adjustment.delta_12.toFixed(2)}.</small>
          {#each news?.events.filter(event => adjustment?.event_ids.includes(event.id)) ?? [] as event}<p>{event.summary}</p><div class="source-links">{#each event.sources as source}<a href={sourceUrl(source.url)} target="_blank" rel="noreferrer">@{source.handle}</a>{/each}</div>{/each}
        </section>
      {/if}
      <div class="alert-status">
        {#if sourceState === 'outage'}<p>Unknown alert status during source outage.</p>
        {:else if row.active}
          <p><i class="dot-key"></i>Alert active {row.active_since ? `since ${clock(row.active_since)}` : 'at this hour'}</p>
          {#if row.active_since}<small>{localTime(row.active_since)} Kyiv</small>{/if}
          {#if remaining}
            {#if remaining[1] > 0}<p>Expected to end in <strong>{remaining[1]} min</strong></p>
            {:else}<p>The predicted median end {remaining[1] === 0 ? 'is now' : `passed ${Math.abs(remaining[1])} min ago`}. The alert is still active.</p>{/if}
            <small>P10–P90: {remaining[0]}–{remaining[2]} min relative to this forecast. {remaining[0] < 0 ? 'Negative values are estimates that have already passed.' : ''}</small>
          {:else}<p class="muted">No end-time estimate is available.</p>{/if}
        {:else}<p>No alert active at this hour.</p>{/if}
      </div>
      {#if mode === 'replay'}
        <div class="actual-outcomes"><h3>What actually happened</h3><p class="muted">An alert started within</p><div>{#each horizons as h}<span>{h} h <strong>{actual(row, h) === null ? 'Unknown' : actual(row, h) ? 'Yes' : 'No'}</strong></span>{/each}</div></div>
      {/if}
      <section class="history-section" aria-busy={loading}>
        <div class="section-heading"><h3>Seven-day history</h3>{#if loading}<span class="muted">Updating…</span>{/if}</div>
        {#if error}<div class="error-state" role="alert"><p>{error}</p><button onclick={retry}>Retry history</button></div>{/if}
        <div class:dimmed={loading}>{#if history && history.raion_id === row.raion_id}<HistoryChart data={history} {horizon} {gaps} />{:else if loading}<p class="empty-state">Loading seven-day history…</p>{:else if !error}<p class="empty-state">No history is available.</p>{/if}</div>
      </section>
    {:else}<p class="empty-state">No forecast. This display unit is outside the forecast universe.</p><div class="alert-status"><h3>Current live status</h3><p>{liveDescription(liveUnit, now)}</p><small>Live status is current, even while replaying a historical forecast.</small></div>{/if}
  {:else}
    <div class="panel-empty"><h2>Look closer at a raion</h2><p>Select a region on the map or in the list to see its probabilities, alert status, and seven-day history.</p><span class="muted">You can also focus a polygon and press Enter.</span></div>
  {/if}
</aside>
