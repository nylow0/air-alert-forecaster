<script lang="ts">
  import { number, oblastName, probability, type Horizon } from './format'
  import type { ForecastRow, RaionFeature, RaionFeatureCollection } from './types'
  let { geo, rows, horizon, selected, onselect }: {
    geo: RaionFeatureCollection; rows: ForecastRow[]; horizon: Horizon
    selected: string | null; onselect: (feature: RaionFeature) => void
  } = $props()
  const features = $derived(new Map(geo.features.filter(f => f.properties.raion_id).map(f => [f.properties.raion_id, f])))
  const sorted = $derived([...rows].sort((a, b) => probability(b, horizon) - probability(a, horizon)))
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users need to focus the scroll region to scroll this table.) -->
<div class="table-scroll raion-list" tabindex="0" role="region" aria-label="Raions sorted by probability">
  <table>
    <caption>Raions sorted by P within {horizon} h. Select a name to see its history.</caption>
    <thead><tr><th scope="col">Raion / oblast</th><th scope="col" class:chosen={horizon === 1}>P1</th><th scope="col" class:chosen={horizon === 3}>P3</th><th scope="col" class:chosen={horizon === 12}>P12</th><th scope="col">Status</th></tr></thead>
    <tbody>
      {#each sorted as row (row.raion_id)}
        {@const feature = features.get(row.raion_id)}
        <tr class:selected-row={selected === row.raion_id}>
          <th scope="row"><button class="raion-name" aria-pressed={selected === row.raion_id}
            onclick={() => onselect(feature ?? { type: 'Feature', properties: { raion_id: row.raion_id, oblast_id: row.raion_id.split('/')[0], name: row.raion_id }, geometry: { type: 'Polygon', coordinates: [] } })}>
            {feature?.properties.name ?? row.raion_id}<small>{oblastName(feature?.properties.oblast_id ?? row.raion_id.split('/')[0])}</small>
          </button></th>
          <td class:chosen={horizon === 1}>{number(row.p_1, 2)}</td><td class:chosen={horizon === 3}>{number(row.p_3, 2)}</td><td class:chosen={horizon === 12}>{number(row.p_12, 2)}</td>
          <td>{#if row.active}<span class="active-status"><i class="dot-key"></i>Active</span>{:else}<span class="muted">Quiet</span>{/if}</td>
        </tr>
      {/each}
    </tbody>
  </table>
  {#if sorted.length === 0}<p class="empty-state">No forecasts are available for this hour. Choose another time.</p>{/if}
</div>
