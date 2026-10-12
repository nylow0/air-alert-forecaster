<script lang="ts">
  import { geoArea, geoMercator, geoPath } from 'd3-geo'
  import { actual, oblastName, probability, probabilityColor, probabilityText, type Horizon } from './format'
  import { liveColors, liveDescription } from './live-format'
  import type { ForecastRow, LiveState, RaionFeature, RaionFeatureCollection } from './types'

  let { geo, rows, live, now, horizon, observationsUnknown, mode, selected, onselect }: {
    geo: RaionFeatureCollection; rows: ForecastRow[]; horizon: Horizon
    live: LiveState | null; now: number
    observationsUnknown: boolean
    mode: 'replay' | 'latest'; selected: string | null; onselect: (feature: RaionFeature) => void
  } = $props()
  // A fixed coordinate space lets CSS resize the map without measuring its own content.
  const width = 800, height = 528
  let hovered = $state<number | null>(null)
  let focused = $state<number | null>(null)
  const byId = $derived(new Map(rows.map(row => [row.raion_id, row])))
  const liveByKey = $derived(new Map((live?.units ?? []).map(unit => [unit.display_key, unit])))

  // D3 uses clockwise exterior rings. Correct RFC 7946 winding locally if needed.
  const geometry = $derived({ ...geo, features: geo.features.map(feature => {
    if (geoArea(feature) <= 2 * Math.PI) return feature
    const g = feature.geometry
    return { ...feature, geometry: g.type === 'Polygon'
      ? { ...g, coordinates: g.coordinates.map(ring => [...ring].reverse()) }
      : { ...g, coordinates: g.coordinates.map(polygon => polygon.map(ring => [...ring].reverse())) } }
  }) })
  // Geometry and dimensions alone invalidate these paths. Forecasts only change paint.
  const shapes = $derived.by(() => {
    const projection = geoMercator().fitSize([Math.max(1, width - 28), height - 28], geometry)
    const [tx, ty] = projection.translate()
    projection.translate([tx + 14, ty + 14])
    const path = geoPath(projection)
    return geometry.features.map((feature, index) => ({ feature, index, d: path(feature) ?? '', centroid: path.centroid(feature) }))
  })
  const ordered = $derived([...shapes].sort((a, b) => Number(a.feature.properties.raion_id === selected && selected !== null) - Number(b.feature.properties.raion_id === selected && selected !== null)))
  // Scrolling a keyboard-focused polygon under the pointer can trigger mouseenter.
  // Keep its tooltip tied to focus until the user moves focus elsewhere.
  const tooltipIndex = $derived(focused ?? hovered)
  const hoveredShape = $derived(tooltipIndex === null ? null : shapes[tooltipIndex])
  const hoveredRow = $derived(hoveredShape?.feature.properties.raion_id ? byId.get(hoveredShape.feature.properties.raion_id) : undefined)
  const gradient = Array.from({ length: 11 }, (_, i) => `${probabilityColor(i / 10)} ${i * 10}%`).join(', ')

  function select(event: KeyboardEvent, feature: RaionFeature): void {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onselect(feature) }
  }
</script>

<div class="map-canvas">
  <svg class="ukraine-map" viewBox={`0 0 ${width} ${height}`} aria-label={`Ukraine raions. Probability of an alert starting within ${horizon} hours.`}>
    <title>Alert onset probability by raion</title>
    <defs>
      <pattern id="excluded-both" patternUnits="userSpaceOnUse" width="8" height="8"><rect width="8" height="8" fill={liveColors.yellow} /><path d="M0 0L8 8M-4 4L4 12M4-4L12 4" stroke={liveColors.red} stroke-width="4" /></pattern>
      <pattern id="excluded-inherited" patternUnits="userSpaceOnUse" width="7" height="7"><path d="M0 7L7 0" stroke="#000" stroke-opacity="0.3" stroke-width="1.3" /></pattern>
    </defs>
    {#each ordered as shape (shape.index)}
      {@const id = shape.feature.properties.raion_id}
      {@const row = id ? byId.get(id) : undefined}
      {@const isSelected = id !== null && id === selected}
      {@const onset = row && mode === 'replay' && actual(row, horizon) === true}
      {@const unit = liveByKey.get(shape.feature.properties.display_key)}
      {@const statusFill = unit?.coverage === 'unknown' ? '#111' : unit?.red_since && unit.yellow_since ? 'url(#excluded-both)' : unit?.red_since ? liveColors.red : unit?.yellow_since ? liveColors.yellow : '#111'}
      {#if isSelected}<path d={shape.d} class="selection-ring" fill="none" stroke-width="4" vector-effect="non-scaling-stroke" aria-hidden="true" />{/if}
      <path d={shape.d} data-raion-id={id} class="raion-path" class:selected={isSelected} class:actual-onset={onset}
        fill={row ? probabilityColor(probability(row, horizon)) : statusFill}
        stroke={onset ? '#f0efec' : row ? '#000' : '#2c2c2a'}
        stroke-width={onset ? 1.5 : 0.5} vector-effect="non-scaling-stroke"
        role="button" tabindex="0" aria-pressed={isSelected}
        aria-label={`${shape.feature.properties.name}, ${oblastName(shape.feature.properties.oblast_id)}. ${row ? `${probabilityText(probability(row, horizon))} probability within ${horizon} hours${observationsUnknown ? '. Alert status unknown during source outage' : row.active ? '. Alert active' : ''}${onset ? '. Alert actually started' : ''}` : `No forecast. Current live status: ${liveDescription(unit, now)}`}`}
        onmouseenter={() => hovered = shape.index} onmouseleave={() => hovered = null}
        onfocus={() => focused = shape.index} onblur={() => focused = null}
        onclick={() => onselect(shape.feature)} onkeydown={event => select(event, shape.feature)} />
      {#if !row && unit?.coverage === 'inherited'}<path d={shape.d} fill="url(#excluded-inherited)" class="live-overlay" aria-hidden="true" />{/if}
    {/each}
    {#each shapes as shape (shape.index)}
      {@const id = shape.feature.properties.raion_id}
      {#if !observationsUnknown && id && byId.get(id)?.active && shape.centroid.every(Number.isFinite)}
        <circle class="active-dot" cx={shape.centroid[0]} cy={shape.centroid[1]} r={5} fill="#d03b3b" stroke="#000" stroke-width="1" aria-hidden="true" />
      {/if}
    {/each}
  </svg>
  {#if hoveredShape}
    <div class="map-tooltip" role="status"
      style:left={`clamp(8px, calc(${hoveredShape.centroid[0] / width * 100}% - 80px), max(8px, calc(100% - 238px)))`}
      style:top={`clamp(8px, calc(${hoveredShape.centroid[1] / height * 100}% - 95px), max(8px, calc(100% - 110px)))`}>
      <strong>{hoveredRow ? probabilityText(probability(hoveredRow, horizon)) : 'No forecast'}</strong>
      {#if hoveredRow}<span>P within {horizon} h</span>{/if}
      <b>{hoveredShape.feature.properties.name}</b>
      <span>{oblastName(hoveredShape.feature.properties.oblast_id)}</span>
      {#if !hoveredRow}<p class="excluded-live-status">Current live status: {liveDescription(liveByKey.get(hoveredShape.feature.properties.display_key), now)}{live?.stale ? ' Last update is delayed.' : ''}</p>{/if}
    </div>
  {/if}
</div>
<div class="map-legend">
  <div class="probability-legend">
    <span>P(alert starts within {horizon} h)</span>
    <div class="gradient-bar" style:background={`linear-gradient(90deg, ${gradient})`}></div>
    <div class="legend-ticks">{#each [0, 0.25, 0.5, 0.75, 1] as tick}<span>{tick}</span>{/each}</div>
  </div>
  <div class="status-legend">
    {#if mode === 'replay'}<span><i class="outline-key"></i>Alert actually started within {horizon} h</span>{/if}
    <span><i class="selection-key"></i>Selected raion</span>
    {#if observationsUnknown}<span>Alert status unknown during source outage</span>{:else}<span><i class="dot-key"></i>Alert active now</span>{/if}
    <span><i class="empty-key"></i>No forecast for excluded regions. Red/yellow show their current live status, also during replay.</span>
  </div>
</div>
