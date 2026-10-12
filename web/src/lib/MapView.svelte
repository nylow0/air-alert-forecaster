<script lang="ts">
  import { geoArea, geoMercator, geoPath } from 'd3-geo'
  import { actual, number, oblastName, probability, probabilityColor, type Horizon } from './format'
  import type { ForecastRow, RaionFeature, RaionFeatureCollection } from './types'

  let { geo, rows, horizon, mode, selected, onselect }: {
    geo: RaionFeatureCollection; rows: ForecastRow[]; horizon: Horizon
    mode: 'replay' | 'latest'; selected: string | null; onselect: (feature: RaionFeature) => void
  } = $props()
  let width = $state(800)
  let hovered = $state<number | null>(null)
  let focused = $state<number | null>(null)
  const height = $derived(Math.max(270, Math.min(550, width * 0.66)))
  const byId = $derived(new Map(rows.map(row => [row.raion_id, row])))

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

<div class="map-canvas" bind:clientWidth={width}>
  <svg class="ukraine-map" viewBox={`0 0 ${width} ${height}`} aria-label={`Ukraine raions. Probability of an alert starting within ${horizon} hours.`}>
    <title>Alert onset probability by raion</title>
    {#each ordered as shape (shape.index)}
      {@const id = shape.feature.properties.raion_id}
      {@const row = id ? byId.get(id) : undefined}
      {@const isSelected = id !== null && id === selected}
      {@const onset = row && mode === 'replay' && actual(row, horizon)}
      <path d={shape.d} data-raion-id={id} class="raion-path" class:selected={isSelected}
        fill={row ? probabilityColor(probability(row, horizon)) : '#111'}
        stroke={isSelected || onset ? '#f0efec' : row ? '#000' : '#2c2c2a'}
        stroke-width={isSelected ? 2 : onset ? 1.5 : 0.5} vector-effect="non-scaling-stroke"
        role="button" tabindex="0" aria-pressed={isSelected}
        aria-label={`${shape.feature.properties.name}, ${oblastName(shape.feature.properties.oblast_id)}. ${row ? `${number(probability(row, horizon), 2)} probability within ${horizon} hours${row.active ? '. Alert active' : ''}${onset ? '. Alert actually started' : ''}` : 'No data'}`}
        onmouseenter={() => hovered = shape.index} onmouseleave={() => hovered = null}
        onfocus={() => focused = shape.index} onblur={() => focused = null}
        onclick={() => onselect(shape.feature)} onkeydown={event => select(event, shape.feature)} />
    {/each}
    {#each shapes as shape (shape.index)}
      {@const id = shape.feature.properties.raion_id}
      {#if id && byId.get(id)?.active && shape.centroid.every(Number.isFinite)}
        <circle class="active-dot" cx={shape.centroid[0]} cy={shape.centroid[1]} r={3.5} fill="#d03b3b" stroke="#000" stroke-width="1" aria-hidden="true" />
      {/if}
    {/each}
  </svg>
  {#if hoveredShape}
    <div class="map-tooltip" role="status"
      style:left={`${Math.max(8, Math.min(width - Math.min(230, width - 16) - 8, hoveredShape.centroid[0] - 80))}px`}
      style:top={`${Math.max(8, Math.min(height - 110, hoveredShape.centroid[1] - 95))}px`}>
      <strong>{hoveredRow ? number(probability(hoveredRow, horizon), 2) : 'No data'}</strong>
      {#if hoveredRow}<span>P within {horizon} h</span>{/if}
      <b>{hoveredShape.feature.properties.name}</b>
      <span>{oblastName(hoveredShape.feature.properties.oblast_id)}</span>
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
    <span><i class="dot-key"></i>Alert active now</span>
    <span><i class="empty-key"></i>No data, including excluded regions</span>
  </div>
</div>
