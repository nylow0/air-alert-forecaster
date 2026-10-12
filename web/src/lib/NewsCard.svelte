<script lang="ts">
  import { oblastName } from './format'
  import { elapsed, eventLabels, newsStateLabels, sourceUrl } from './live-format'
  import type { NewsFeed } from './types'
  let { feed, now, error, onclose, opensettings }: { feed: NewsFeed | null; now: number; error: string | null; onclose: () => void; opensettings: () => void } = $props()
</script>

<aside class="news-card" aria-label="News agent">
  <div class="news-heading"><h2>News agent</h2><button aria-label="Close news card" onclick={onclose}>×</button></div>
  {#if error}<p role="status">News updates unavailable. <button class="text-button" onclick={opensettings}>Open Settings</button></p>
  {:else if !feed}<p class="muted">Loading news status…</p>
  {:else if !feed.status.enabled || feed.status.state === 'off'}<p class="news-off">News agent is off · <button class="text-button" onclick={opensettings}>enable in Settings</button></p>
  {:else}
    <p class="news-state"><i class:running={feed.status.state === 'running'}></i>{newsStateLabels[feed.status.state]}</p>
    {#if feed.status.message}<p class="news-message">{feed.status.message}</p>{/if}
    {#if ['needs_login', 'needs_codex'].includes(feed.status.state)}<button class="text-button" onclick={opensettings}>Open Settings</button>{/if}
    {#each feed.events.slice(0, 3) as event (event.id)}
      <article class="news-event"><div><span aria-hidden="true">{eventLabels[event.type].icon}</span><h3>{eventLabels[event.type].label}</h3><small>{elapsed(event.published_at, now)} ago</small></div>
        <p>{event.summary}</p><small>{event.oblast_ids.length ? event.oblast_ids.map(oblastName).join(', ') : 'Across Ukraine'} · {event.status}</small>
        <div class="source-links">{#each event.sources as source}<a href={sourceUrl(source.url)} target="_blank" rel="noreferrer">@{source.handle}</a>{/each}</div>
      </article>
    {/each}
    {#if feed.status.state === 'running' && !feed.events.length}<p class="muted">No validated events yet.</p>{/if}
    <small class="news-footnote">Experimental. Check the linked reports.</small>
  {/if}
</aside>
