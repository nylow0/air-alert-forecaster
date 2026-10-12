<script lang="ts">
  import { geoArea, geoMercator, geoPath } from 'd3-geo'
  import { oblastName } from './format'
  import { coverageNote, liveColors, liveDescription, liveLabel } from './live-format'
  import type { LiveState, RaionFeatureCollection } from './types'

  let { geo, live, layer, now }: { geo: RaionFeatureCollection; live: LiveState; layer: 'yellow' | 'red'; now: number } = $props()
  let hovered = $state<string | null>(null)
  let focused = $state<string | null>(null)
  let pinned = $state<string | null>(null)
  const units = $derived(new Map(live.units.map(unit => [unit.display_key, unit])))
  const count = $derived(live.units.filter(unit => unit[`${layer}_since`] !== null).length)
  const shapes = $derived.by(() => {
    // D3's exterior winding differs from RFC 7946. Reverse only large spherical areas.
    const geometry = { ...geo, features: geo.features.map(feature => {
      if (geoArea(feature) <= 2 * Math.PI) return feature
      const g = feature.geometry
      return { ...feature, geometry: g.type === 'Polygon'
        ? { ...g, coordinates: g.coordinates.map(ring => [...ring].reverse()) }
        : { ...g, coordinates: g.coordinates.map(polygon => polygon.map(ring => [...ring].reverse())) } }
    }) }
    const path = geoPath(geoMercator().fitExtent([[12, 12], [788, 488]], geometry))
    return geometry.features.map(feature => ({ feature, d: path(feature) ?? '', center: path.centroid(feature) }))
  })
  const tooltip = $derived(shapes.find(shape => shape.feature.properties.display_key === (focused ?? hovered ?? pinned)))
  const unit = $derived(tooltip ? units.get(tooltip.feature.properties.display_key) : undefined)
</script>

<section class="live-layer" aria-label={`${layer === 'yellow' ? 'Yellow' : 'Red'} alerts`} data-layer={layer}>
  <div class="live-layer-heading">
    <h3><i style:background={liveColors[layer]}></i>{layer === 'yellow' ? 'Yellow' : 'Red'} alerts</h3>
    <p data-testid={`${layer}-count`}><strong>{count}</strong> raions {layer}</p>
    <span class="muted">{layer === 'yellow' ? 'Yellow status, independently reported' : 'Red status, independently reported'}</span>
  </div>
  <div class="live-map-canvas">
    <svg class="live-map" viewBox="0 0 800 500" aria-label={`Ukraine live ${layer} alerts`}>
      <title>All {geo.features.length} display units, {layer} alerts</title>
      <defs>
        <pattern id={`inherited-${layer}`} patternUnits="userSpaceOnUse" width="7" height="7"><path d="M-1 1L1-1M0 7L7 0M6 8L8 6" stroke="#000" stroke-opacity="0.3" stroke-width="1.3" /></pattern>
        <pattern id={`unknown-${layer}`} patternUnits="userSpaceOnUse" width="8" height="8"><rect width="8" height="8" fill="#111" /><circle cx="4" cy="4" r="0.8" fill="#555" /></pattern>
      </defs>
      {#each shapes as shape (shape.feature.properties.display_key)}
        {@const key = shape.feature.properties.display_key}
        {@const current = units.get(key)}
        {@const unknown = !current || current.coverage === 'unknown'}
        {@const active = current?.[`${layer}_since`] != null}
        <path d={shape.d} class="live-path" data-display-key={key} data-active={active} data-coverage={current?.coverage ?? 'unknown'}
          fill={unknown ? `url(#unknown-${layer})` : active ? liveColors[layer] : '#181819'}
          stroke="#000" stroke-width="0.7" vector-effect="non-scaling-stroke"
          role="button" tabindex="0" aria-pressed={pinned === key}
          aria-label={`${shape.feature.properties.name}, ${oblastName(shape.feature.properties.oblast_id)}. ${liveDescription(current, now)}`}
          onmouseenter={() => hovered = key} onmouseleave={() => hovered = null}
          onfocus={() => focused = key} onblur={() => focused = null}
          onclick={() => pinned = pinned === key ? null : key}
          onkeydown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pinned = pinned === key ? null : key } if (event.key === 'Escape') { pinned = null; focused = null } }}>
          <title>{liveLabel(current, now)}</title>
        </path>
        {#if current?.coverage === 'inherited'}<path d={shape.d} fill={`url(#inherited-${layer})`} class="live-overlay" aria-hidden="true" />{/if}
      {/each}
    </svg>
    {#if tooltip}
      <div class="map-tooltip live-tooltip" role="status" style:left={`clamp(8px, ${tooltip.center[0] / 8}%, calc(100% - 248px))`}>
        <b>{tooltip.feature.properties.name}</b><span>{oblastName(tooltip.feature.properties.oblast_id)}</span>
        <p>{liveDescription(unit, now)}</p>
      </div>
    {/if}
  </div>
</section>
<span class="sr-only">{coverageNote(undefined)}</span>
