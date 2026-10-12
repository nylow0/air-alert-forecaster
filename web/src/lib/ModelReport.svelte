<script lang="ts">
  import ReportScores from './ReportScores.svelte'
  import { localTime } from './format'
  import type { DurationScore, Meta, OnsetScore, Report } from './types'
  let { report, meta }: { report: Report; meta: Meta | null } = $props()
  const splits = $derived((['test', 'val'] as const).filter(split => report.onset.some(s => s.split === split) || report.duration.some(s => s.split === split)))
  const notes = $derived([...new Set(report.notes)])
  const onsetModel = $derived(report.selection?.onset ?? meta?.onset_model)
  const durationModel = $derived(report.selection?.duration ?? meta?.duration_model)

  function selectedOnset(split: 'val' | 'test'): OnsetScore[] {
    const scores = report.onset.filter(s => s.split === split && s.model === onsetModel)
    const rowSet = scores.find(s => /(^|-)served$/.test(s.row_set))?.row_set ?? scores.find(s => s.row_set === 'full')?.row_set ?? scores[0]?.row_set
    return scores.filter(s => s.row_set === rowSet)
  }
  function selectedDuration(split: 'val' | 'test'): DurationScore[] {
    // D2@60 is the selected D2 model evaluated after an alert survives 60 minutes.
    return report.duration.filter(s => s.split === split && (s.model === durationModel || s.model.startsWith(`${durationModel}@`)))
  }
</script>

<article class="model-report">
  <div class="report-heading"><h2>Model report</h2><p>Selected forecasts first, then all model comparisons.</p>{#if report.generated_at}<small>Generated {localTime(report.generated_at)} Kyiv</small>{/if}</div>
  {#if report.selection || onsetModel || durationModel}<section class="selection-section"><h3>Selected models</h3><div class="selected-models"><p>Alert onset <strong>{onsetModel ?? 'Not selected'}</strong></p><p>Duration <strong>{durationModel ?? 'Not selected'}</strong></p></div>{#if report.selection?.note}<p>{report.selection.note}</p>{/if}</section>{/if}
  {#if !report.onset.length && !report.duration.length}<p class="empty-state">No evaluation scores have been published yet.</p>{/if}
  {#each splits as split}
    {@const onset = selectedOnset(split)}
    {@const duration = selectedDuration(split)}
    {#if onset.length || duration.length}<section class="evaluation-section" aria-label={`${split === 'test' ? 'Test' : 'Validation'} selected results`}>
      <h3>{split === 'test' ? 'Test' : 'Validation'} results for selected models</h3>
      {#if meta}<p class="muted">{localTime(meta.splits[split].start)} to {localTime(meta.splits[split].end)} Kyiv</p>{/if}
      <ReportScores {onset} {duration} caption={`${split === 'test' ? 'Test' : 'Validation'}, selected ${onset[0]?.row_set ?? 'models'}`} durationLabel={`${split} selected duration metrics`} reliability={true} />
    </section>{/if}
  {/each}
  {#if notes.length}<section class="report-notes" aria-label="Report notes"><h3>Evaluation notes and live track record</h3>{#each notes as note}<p>{note}</p>{/each}</section>{/if}
  <details class="model-comparisons"><summary>All model comparisons ({report.onset.length} onset scores, {report.duration.length} duration scores)</summary>
    <p class="muted">Compare models within the same split and row set. Served scores include the horizon monotonicity adjustment. Unprojected scores use each model's original probabilities.</p>
    {#each splits as split}<section class="evaluation-section">
      <h3>{split === 'test' ? 'Test' : 'Validation'} comparisons</h3>
      {#each [...new Set(report.onset.filter(s => s.split === split).map(s => s.row_set))] as rowSet}
        {@const onset = report.onset.filter(s => s.split === split && s.row_set === rowSet)}
        <ReportScores {onset} duration={[]} caption={`${split === 'test' ? 'Test' : 'Validation'}, ${rowSet === 'full' ? 'full row set' : `row set ${rowSet}`}`} durationLabel="" />
      {/each}
      <ReportScores onset={[]} duration={report.duration.filter(s => s.split === split)} caption="" durationLabel={`${split} duration metrics`} />
    </section>{/each}
  </details>
</article>
