<script lang="ts">
  import ReliabilityChart from './ReliabilityChart.svelte'
  import SkillTable from './SkillTable.svelte'
  import { number } from './format'
  import type { DurationScore, OnsetScore } from './types'
  let { onset, duration, caption, durationLabel, reliability = false }: {
    onset: OnsetScore[]; duration: DurationScore[]; caption: string; durationLabel: string; reliability?: boolean
  } = $props()
</script>

{#if onset.length}
  <h4>Alert onset skill</h4><SkillTable scores={onset} {caption} compact={reliability} />
  {#if reliability}<h4 class="reliability-title">Reliability</h4><div class="reliability-grid">{#each [...new Set(onset.map(s => s.horizon))].sort((a, b) => a - b) as horizon}<ReliabilityChart scores={onset.filter(s => s.horizon === horizon)} {horizon} />{/each}</div>{/if}
{/if}
{#if duration.length}
  <h4>Alert duration</h4><p class="muted">Loss and median error are in minutes. Coverage targets 80%. A model suffix such as @60 measures remaining time after 60 minutes of an active alert.</p>
  <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users need to focus the scroll region to scroll this table.) -->
  <div class="table-scroll" tabindex="0" role="region" aria-label={durationLabel}><table><caption>{durationLabel}</caption><thead><tr><th>Model</th><th>Samples</th><th>Pinball P10</th><th>Pinball P50</th><th>Pinball P90</th><th>P50 mean absolute error</th><th>80% coverage</th></tr></thead><tbody>{#each duration as d}<tr><th scope="row">{d.model}</th><td>{d.n}</td><td>{number(d.pinball_q10, 2)}</td><td>{number(d.pinball_q50, 2)}</td><td>{number(d.pinball_q90, 2)}</td><td>{number(d.mae_q50_min, 2)}</td><td>{number(d.coverage_80 * 100, 1)}%</td></tr>{/each}</tbody></table></div>
{/if}
