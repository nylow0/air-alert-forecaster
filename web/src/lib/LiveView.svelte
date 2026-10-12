<script lang="ts">
  import LiveMap from './LiveMap.svelte'
  import { clock } from './format'
  import { elapsed } from './live-format'
  import type { LiveState, RaionFeatureCollection } from './types'
  let { geo, live, now, error, retry }: { geo: RaionFeatureCollection | null; live: LiveState | null; now: number; error: string | null; retry: () => void } = $props()
</script>

<section class="live-view" aria-label="Live alert maps">
  <div class="live-heading"><h2>Alerts right now</h2><p>{#if live?.fetched_at}Updated {elapsed(live.fetched_at, now)} ago <span>at {clock(live.fetched_at)} Kyiv</span>{:else}Waiting for the first live update{/if}</p></div>
  {#if live?.stale || error}<p class="stale-banner" role="status">Live updates are delayed. The last known status stays on the maps.{#if error} <button onclick={retry}>Retry live alerts</button>{/if}</p>{/if}
  {#if geo && live}
    <LiveMap {geo} {live} layer="yellow" {now} />
    <LiveMap {geo} {live} layer="red" {now} />
    <div class="live-key"><span><i class="inherited-key"></i>Hatched: oblast-level status</span><span><i class="unknown-key"></i>Dotted: unknown, not all clear</span><span>Both alerts can be active at once.</span></div>
  {:else}<p class="initial-loading">Loading live alert maps…</p>{/if}
</section>
