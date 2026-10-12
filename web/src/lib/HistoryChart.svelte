<script lang="ts">
  import { scaleLinear, scaleTime } from 'd3-scale'
  import { day, localTime, number, probability, type Horizon } from './format'
  import type { HistoryResponse } from './types'
  let { data, horizon }: { data: HistoryResponse; horizon: Horizon } = $props()
  let hovered = $state<number | null>(null)
  const width = 328, height = 180, left = 28, right = 8, top = 12, bottom = 28
  const x = $derived(scaleTime().domain([new Date(data.start), new Date(data.end)]).range([left, width - right]))
  const y = scaleLinear().domain([0, 1]).range([height - bottom, top])
  const path = $derived(data.series.map((p, i) => `${i ? 'L' : 'M'}${x(new Date(p.t))},${y(probability(p, horizon))}`).join(' '))
  const point = $derived(hovered === null ? null : data.series[Math.min(hovered, data.series.length - 1)])
  const active = $derived(point && data.episodes.some(e => Date.parse(e.start) <= Date.parse(point.t) && (e.end === null || Date.parse(e.end) > Date.parse(point.t))))
  const ticks = $derived([Date.parse(data.start), (Date.parse(data.start) + Date.parse(data.end)) / 2, Date.parse(data.end)])

  function move(event: PointerEvent): void {
    const rect = event.currentTarget instanceof SVGElement ? event.currentTarget.getBoundingClientRect() : null
    if (!rect || data.series.length === 0) return
    const time = x.invert(Math.max(left, Math.min(width - right, (event.clientX - rect.left) / rect.width * width))).getTime()
    hovered = data.series.reduce((best, p, i) => Math.abs(Date.parse(p.t) - time) < Math.abs(Date.parse(data.series[best].t) - time) ? i : best, 0)
  }
  function key(event: KeyboardEvent): void {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault(); event.stopPropagation()
      hovered = Math.max(0, Math.min(data.series.length - 1, (hovered ?? data.series.length - 1) + (event.key === 'ArrowRight' ? 1 : -1)))
    }
  }
</script>

{#if data.series.length === 0}
  <p class="empty-state">No history is available for these seven days.</p>
{:else}
  <svg class="history-chart" viewBox={`0 0 ${width} ${height}`} role="slider" tabindex="0"
    aria-valuemin="0" aria-valuemax={data.series.length - 1} aria-valuenow={hovered ?? data.series.length - 1}
    aria-valuetext={point ? `${localTime(point.t)}, probability ${number(probability(point, horizon), 2)}${active ? ', alert active' : ', no alert active'}` : 'Focus and use arrow keys to inspect hourly history'}
    aria-label={`Seven-day probability history within ${horizon} hours. Use arrow keys to explore hours, or open the data table below.`}
    onpointermove={move} onpointerleave={() => hovered = null} onkeydown={key}
    onfocus={() => hovered = data.series.length - 1} onblur={() => hovered = null}>
    <title>Seven-day alert probability and actual episodes</title>
    <defs><clipPath id="history-plot"><rect x={left} y={top} width={width - left - right} height={height - top - bottom} /></clipPath></defs>
    <g clip-path="url(#history-plot)">
      {#each data.episodes as episode}
        {@const start = Math.max(left, x(new Date(episode.start)))}
        {@const end = Math.min(width - right, x(new Date(episode.end ?? data.end)))}
        <rect x={start} y={top} width={Math.max(0, end - start)} height={height - top - bottom} fill="rgba(208,59,59,0.22)" />
      {/each}
      {#each [0, 0.5, 1] as tick}<line class="gridline" x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} />{/each}
      <path d={path} stroke="oklch(0.8 0.15 68)" stroke-width="2" fill="none" />
      {#if point}<line x1={x(new Date(point.t))} x2={x(new Date(point.t))} y1={top} y2={height - bottom} stroke="#f0efec" stroke-width="1" /><circle cx={x(new Date(point.t))} cy={y(probability(point, horizon))} r="4" fill="#f0efec" />{/if}
    </g>
    {#each [0, 0.5, 1] as tick}<text class="axis" x={left - 7} y={y(tick) + 4} text-anchor="end">{tick}</text>{/each}
    {#each ticks as tick, i}<text class="axis" x={x(new Date(tick))} y={height - 5} text-anchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}>{day(tick)}</text>{/each}
  </svg>
  <div class="history-readout" aria-live="polite">
    {#if point}<time datetime={point.t}>{localTime(point.t)}</time><span>P1 {number(point.p_1, 2)} / P3 {number(point.p_3, 2)} / P12 {number(point.p_12, 2)}</span><span>{active ? 'Alert active at this hour' : 'No alert active at this hour'}</span>
    {:else}<span>Hover, touch, or focus the chart and use arrow keys.</span>{/if}
  </div>
  <div class="chart-key"><span><i class="line-key"></i>P within {horizon} h</span><span><i class="band-key"></i>Actual alert episodes</span></div>
  <details class="chart-data"><summary>History data and episodes</summary>
    <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users need to focus the scroll region to scroll this table.) -->
    <div class="table-scroll" tabindex="0" role="region" aria-label="Hourly history data"><table><thead><tr><th>Kyiv time</th><th>P1</th><th>P3</th><th>P12</th></tr></thead><tbody>{#each data.series as p}<tr><th scope="row">{localTime(p.t)}</th><td>{number(p.p_1, 2)}</td><td>{number(p.p_3, 2)}</td><td>{number(p.p_12, 2)}</td></tr>{/each}</tbody></table></div>
    <ul class="episode-list">{#each data.episodes as e}<li>{localTime(e.start)} to {e.end ? localTime(e.end) : 'still active at this forecast time'}</li>{/each}</ul>
  </details>
{/if}
