<script lang="ts">
  import ReliabilityChart from './ReliabilityChart.svelte'
  import SkillTable from './SkillTable.svelte'
  import { localTime, number } from './format'
  import type { OnsetScore, Report } from './types'
  let { report }: { report: Report } = $props()
  const splits = $derived((['val', 'test'] as const).filter(split => report.onset.some(s => s.split === split) || report.duration.some(s => s.split === split)))
  const rowSets = $derived([...new Set(report.onset.filter(s => s.row_set !== 'full').map(s => s.row_set))])
  function batches(scores: OnsetScore[]): OnsetScore[][] {
    const sorted = [...scores].sort((a, b) => a.model.localeCompare(b.model))
    return Array.from({ length: Math.ceil(sorted.length / 3) }, (_, i) => sorted.slice(i * 3, i * 3 + 3))
  }
</script>

<article class="model-report">
  <div class="report-heading"><h2>Model report</h2><p>How the forecasts compare, and which models are served.</p>{#if report.generated_at}<small>Generated {localTime(report.generated_at)} Kyiv</small>{/if}</div>
  {#if report.notes.length}<div class="report-notes" role="note">{#each report.notes as note}<p>{note}</p>{/each}</div>{/if}
  {#if report.selection}<section class="selection-section"><h3>Selected models</h3><div class="selected-models"><p>Alert onset <strong>{report.selection.onset ?? 'Not selected'}</strong></p><p>Duration <strong>{report.selection.duration ?? 'Not selected'}</strong></p></div>{#if report.selection.note}<p>{report.selection.note}</p>{/if}</section>{/if}
  {#if !report.onset.length && !report.duration.length}<p class="empty-state">No evaluation scores have been published yet.</p>{/if}
  {#each splits as split}
    {@const full = report.onset.filter(s => s.split === split && s.row_set === 'full')}
    {@const duration = report.duration.filter(s => s.split === split)}
    {@const horizons = [...new Set(full.map(s => s.horizon))].sort((a, b) => a - b)}
    <section class="evaluation-section">
      <h3>{split === 'val' ? 'Validation' : 'Test'} results</h3>
      {#if full.length}
        <h4>Alert onset skill</h4><SkillTable scores={full} caption={`${split === 'val' ? 'Validation' : 'Test'}, full row set`} />
        <h4 class="reliability-title">Reliability</h4>
        <div class="reliability-grid">{#each horizons as horizon}{#each batches(full.filter(s => s.horizon === horizon)) as scores}<ReliabilityChart {scores} {horizon} />{/each}{/each}</div>
      {/if}
      {#if duration.length}
        <h4>Alert duration</h4><p class="muted">Pinball loss and median absolute error are in minutes. The coverage target is 80%.</p>
        <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users need to focus the scroll region to scroll this table.) -->
        <div class="table-scroll" tabindex="0" role="region" aria-label={`${split} duration metrics`}><table><caption>{split === 'val' ? 'Validation' : 'Test'} duration scores</caption><thead><tr><th>Model</th><th>Episodes</th><th>Pinball P10</th><th>Pinball P50</th><th>Pinball P90</th><th>Median MAE</th><th>80% coverage</th></tr></thead><tbody>{#each duration as d}<tr><th scope="row">{d.model}</th><td>{d.n}</td><td>{number(d.pinball_q10, 2)}</td><td>{number(d.pinball_q50, 2)}</td><td>{number(d.pinball_q90, 2)}</td><td>{number(d.mae_q50_min, 2)}</td><td>{number(d.coverage_80 * 100, 1)}%</td></tr>{/each}</tbody></table></div>
      {/if}
    </section>
  {/each}
  {#if rowSets.length}<section class="evaluation-section text-comparison"><h3>Text signal comparison</h3><p class="muted">Compare models on the same row set. Subset scores are shown separately from the full evaluation.</p>{#each rowSets as rowSet}{#each splits as split}{@const scores = report.onset.filter(s => s.row_set === rowSet && s.split === split)}{#if scores.length}<SkillTable {scores} caption={`${split === 'val' ? 'Validation' : 'Test'}, row set ${rowSet}`} />{/if}{/each}{/each}</section>{/if}
</article>
