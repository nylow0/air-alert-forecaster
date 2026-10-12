<script lang="ts">
  import { localTime } from './format'
  import type { SystemStatus } from './types'
  let { system, now, error }: { system: SystemStatus | null; now: number; error: string | null } = $props()
  const hours = $derived(system ? Math.max(0, Math.ceil((Date.parse(system.next_nightly_at) - now) / 3_600_000)) : 0)
</script>
<div class="system-status" aria-label="System status">
  {#if system}<p>Data through {system.data_through ? `${localTime(system.data_through)} Kyiv` : 'unknown'}. Nightly update at 00:00 Kyiv, {hours ? `next in ${hours} h` : 'due now'}.</p>
    {#if system.coverage_gaps.length}<p class="coverage-gap">Coverage gaps: {#each system.coverage_gaps as gap, i}{i ? '; ' : ''}{localTime(gap.start)} to {localTime(gap.end)} Kyiv{/each}.</p>{/if}
    {#if system.last_nightly_ok === false}<p>Last nightly update failed. Showing the last available forecasts.</p>{/if}
  {:else}<p>{error ? 'System status unavailable.' : 'Loading system status…'}</p>{/if}
</div>
