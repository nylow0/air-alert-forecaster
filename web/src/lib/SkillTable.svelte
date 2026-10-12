<script lang="ts">
  import { number } from './format'
  import type { OnsetScore } from './types'
  let { scores, caption, compact = false }: { scores: OnsetScore[]; caption: string; compact?: boolean } = $props()
  const horizons = $derived([...new Set(scores.map(s => s.horizon))].sort((a, b) => a - b))
  const models = $derived([...new Set(scores.map(s => s.model))].sort())
  const metrics = [
    { key: 'bss', label: 'BSS ↑', higher: true }, { key: 'brier', label: 'Brier ↓', higher: false },
    { key: 'logloss', label: 'Log-loss ↓', higher: false }, { key: 'pr_auc', label: 'PR-AUC ↑', higher: true },
  ] as const
  function best(h: number, key: typeof metrics[number]['key'], higher: boolean): number | null {
    const values = scores.filter(s => s.horizon === h).map(s => s[key]).filter((v): v is number => v !== null)
    return values.length ? (higher ? Math.max(...values) : Math.min(...values)) : null
  }
</script>
<!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users need to focus the scroll region to scroll this table.) -->
<div class="table-scroll" tabindex="0" role="region" aria-label={caption}>
  {#if compact}<table class="skill-table selected-skill-table"><caption>{caption}. Arrows show the better direction.</caption><thead><tr><th scope="col">Within</th>{#each metrics as metric}<th scope="col">{metric.label}</th>{/each}</tr></thead><tbody>{#each scores as score}<tr><th scope="row">{score.horizon} h</th>{#each metrics as metric}<td>{number(score[metric.key])}</td>{/each}</tr>{/each}</tbody></table>
  {:else}<table class="skill-table">
    <caption>{caption}. Arrows show the better direction. The best value in each column is highlighted.</caption>
    <thead><tr><th rowspan="2" scope="col">Model</th>{#each horizons as h}<th colspan="4" scope="colgroup">Within {h} h</th>{/each}</tr><tr>{#each horizons as h}{#each metrics as metric}<th scope="col">{metric.label}</th>{/each}{/each}</tr></thead>
    <tbody>{#each models as model}<tr><th scope="row">{model}</th>{#each horizons as h}{@const score = scores.find(s => s.model === model && s.horizon === h)}{#each metrics as metric}<td class:best={score && score[metric.key] !== null && score[metric.key] === best(h, metric.key, metric.higher)}>{score ? number(score[metric.key]) : '—'}</td>{/each}{/each}</tr>{/each}</tbody>
  </table>{/if}
</div>
