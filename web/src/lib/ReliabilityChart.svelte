<script lang="ts">
  import { scaleLinear } from 'd3-scale'
  import { number, probabilityText } from './format'
  import type { OnsetScore } from './types'
  let { scores, horizon }: { scores: OnsetScore[]; horizon: number } = $props()
  const x = scaleLinear().domain([0, 1]).range([40, 272])
  const y = scaleLinear().domain([0, 1]).range([240, 12])
  const fixedColors: Record<string, string> = { B0: '#3987e5', B1: '#d95926', M1: '#199e70' }
  const chartWidth = $derived(Math.max(340, 300 + Math.max(0, ...scores.map(s => s.model.length)) * 7))
  function color(model: string): string {
    const base = model.split(/[+-]/)[0]
    if (fixedColors[base]) return fixedColors[base]
    const index = [...model].reduce((sum, c) => sum + c.charCodeAt(0), 0) % 3
    return ['#a68aca', '#d4aa59', '#73b8c6'][index]
  }
  const series = $derived(scores.map(score => {
    const bins = score.reliability.filter(b => b.count > 0 && b.mean_pred !== null && b.frac_pos !== null)
      .map(b => ({ ...b, mean_pred: b.mean_pred!, frac_pos: b.frac_pos! }))
    return { ...score, bins, color: color(score.model), path: bins.map((b, i) => `${i ? 'L' : 'M'}${x(b.mean_pred)},${y(b.frac_pos)}`).join(' ') }
  }))
  const labels = $derived.by(() => {
    const ends = series.map(s => ({ model: s.model, color: s.color, end: s.bins.at(-1) })).filter(s => s.end)
      .sort((a, b) => y(a.end!.frac_pos) - y(b.end!.frac_pos))
    let previous = -10
    return ends.map(s => {
      const labelY = Math.max(previous + 16, Math.min(232, y(s.end!.frac_pos) + 4))
      previous = labelY
      return { ...s, labelY }
    })
  })
</script>
<figure class="reliability-figure">
  <figcaption><h4>Within {horizon} h</h4><p>Observed frequency against mean predicted probability</p></figcaption>
  <svg viewBox={`0 0 ${chartWidth} 288`} role="img" aria-label={`Reliability within ${horizon} hours for ${scores.map(s => s.model).join(', ')}. Numerical bins are available below.`}>
    <title>Reliability diagram for {horizon} hours</title>
    {#each [0, 0.5, 1] as tick}<line class="gridline" x1={40} x2={272} y1={y(tick)} y2={y(tick)} /><text class="axis" x="32" y={y(tick) + 4} text-anchor="end">{tick}</text><text class="axis" x={x(tick)} y="259" text-anchor="middle">{tick}</text>{/each}
    <line x1={x(0)} x2={x(1)} y1={y(0)} y2={y(1)} stroke="#898781" stroke-width="1" />
    {#each series as s}<path d={s.path} fill="none" stroke={s.color} stroke-width="2" />{#each s.bins as bin}<circle cx={x(bin.mean_pred)} cy={y(bin.frac_pos)} r={5 * chartWidth / 340} fill={s.color} />{/each}{/each}
    {#each labels as s}<line x1="278" y1={s.labelY - 4} x2="283" y2={s.labelY - 4} stroke={s.color} stroke-width="0.7" /><text x="287" y={s.labelY} fill={s.color} class="series-label">{s.model}</text>{/each}
    <text class="axis" x="156" y="282" text-anchor="middle">Mean predicted</text>
    <text class="axis" transform="translate(12,125) rotate(-90)" text-anchor="middle">Observed frequency</text>
  </svg>
  <div class="chart-key">{#each series as s}<span><i class="line-key" style:background={s.color}></i>{s.model}</span>{/each}</div>
  <details class="chart-data"><summary>Reliability bin data</summary>
    <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users need to focus the scroll region to scroll this table.) -->
    <div class="table-scroll" tabindex="0" role="region" aria-label={`Reliability bins within ${horizon} hours`}><table><thead><tr><th>Model</th><th>Bin</th><th>Predicted</th><th>Observed</th><th>Count</th></tr></thead><tbody>{#each scores as s}{#each s.reliability as bin}<tr><th scope="row">{s.model}</th><td>{bin.lo}–{bin.hi}</td><td>{probabilityText(bin.mean_pred, 3)}</td><td>{number(bin.frac_pos)}</td><td>{bin.count}</td></tr>{/each}{/each}</tbody></table></div></details>
</figure>
