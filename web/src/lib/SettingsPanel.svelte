<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from './api'
  import type { AuthStatus, NewsModels, ReasoningEffort, Settings } from './types'

  let { settings, auth, saving, error, loadError, authLoadError, onclose, save, refreshauth, reopennews }: {
    settings: Settings | null; auth: AuthStatus | null; saving: boolean; error: string | null
    loadError: string | null; authLoadError: string | null
    onclose: () => void; save: (next: Settings) => Promise<void>; refreshauth: () => Promise<void>; reopennews: () => void
  } = $props()
  let dialog: HTMLDialogElement
  let authBusy = $state(false)
  let authError = $state<string | null>(null)
  let confirmLogout = $state(false)
  const efforts: ReasoningEffort[] = ['low', 'medium', 'high', 'xhigh']

  onMount(() => { dialog.showModal(); return () => dialog.close() })
  $effect(() => {
    if (!auth?.login_in_progress) return
    const timer = window.setInterval(() => void refreshauth(), 1000)
    return () => window.clearInterval(timer)
  })

  async function action(kind: 'login' | 'cancel' | 'logout'): Promise<void> {
    authBusy = true; authError = null
    try { await api.authAction(kind); await refreshauth(); confirmLogout = false }
    catch (cause) { authError = cause instanceof Error ? cause.message : 'Could not change login status. Try again.' }
    finally { authBusy = false }
  }
  function modelChange<K extends keyof NewsModels>(key: K, value: NewsModels[K]): void {
    if (settings) void save({ ...settings, models: { ...settings.models, [key]: value } })
  }
  function effort(event: Event): ReasoningEffort {
    const value = (event.currentTarget as HTMLSelectElement).value
    return efforts.find(item => item === value) ?? 'high'
  }
</script>

<dialog bind:this={dialog} class="settings-panel" aria-labelledby="settings-title" onclose={onclose}>
  <div class="settings-heading"><h2 id="settings-title">Settings</h2><button aria-label="Close settings" onclick={() => dialog.close()}>×</button></div>
  <p class="settings-save" role="status">{saving ? 'Saving…' : 'Changes save automatically.'}</p>
  {#if error}<p class="settings-error" role="alert">{error} Your change was rolled back.</p>{/if}
  {#if loadError}<p class="settings-error" role="alert">{loadError} Close and reopen Settings to retry.</p>{/if}
  {#if settings}
    <section class="setting-section"><label class="toggle-row"><span><strong>News agent</strong><small>Extract reports and evaluate forecast adjustments.</small></span><input type="checkbox" checked={settings.news_enabled} disabled={saving} onchange={event => settings && void save({ ...settings, news_enabled: event.currentTarget.checked })} /></label></section>
    <section class="setting-section" aria-label="ChatGPT login"><h3>ChatGPT login</h3>
      {#if authLoadError}<p class="settings-error" role="alert">{authLoadError}</p>{/if}
      {#if auth}
        <p class="auth-status">{!auth.codex_installed ? 'Codex is not installed' : auth.logged_in ? 'Logged in with ChatGPT' : auth.login_in_progress ? 'Login in progress' : 'Not logged in'}</p>
        {#if auth.message}<p class="setting-hint">{auth.message}</p>{/if}
        {#if !auth.codex_installed}<p class="setting-hint">Install the Codex CLI, then restart the server. <a href="https://developers.openai.com/codex/cli/" target="_blank" rel="noreferrer">Installation guide</a></p>
        {:else if auth.login_in_progress}<p class="setting-hint">Continue in the browser window opened by Codex. This panel checks login status automatically.</p><button class="setting-action" disabled={authBusy} onclick={() => void action('cancel')}>Cancel login</button>
        {:else if auth.logged_in}
          {#if confirmLogout}<div class="logout-confirm" role="group" aria-label="Confirm logout"><p>This signs out Codex on this machine, including other apps using it.</p><button disabled={authBusy} onclick={() => void action('logout')}>Confirm logout</button><button onclick={() => confirmLogout = false}>Keep logged in</button></div>
          {:else}<button class="setting-action" onclick={() => confirmLogout = true}>Logout</button>{/if}
        {:else}<button class="setting-action" disabled={authBusy} onclick={() => void action('login')}>Login with ChatGPT</button>{/if}
      {:else}<p class="setting-hint">Loading login status…</p>{/if}
      {#if authError}<p class="settings-error" role="alert">{authError}</p>{/if}
    </section>
    <section class="setting-section"><h3>Models</h3>
      {#each [{ label: 'Extraction', model: 'extract_model', effort: 'extract_effort' }, { label: 'Evaluation', model: 'evaluate_model', effort: 'evaluate_effort' }] as field}
        {@const modelKey = field.model as 'extract_model' | 'evaluate_model'}
        {@const effortKey = field.effort as 'extract_effort' | 'evaluate_effort'}
        <div class="model-fields"><label>{field.label} model<select aria-label={`${field.label} model`} value={settings.models[modelKey]} disabled={saving || !auth?.available_models.length} onchange={event => modelChange(modelKey, event.currentTarget.value)}>
          {#if !auth?.available_models.includes(settings.models[modelKey])}<option value={settings.models[modelKey]}>{settings.models[modelKey]} (unavailable)</option>{/if}
          {#each auth?.available_models ?? [] as model}<option value={model}>{model}</option>{/each}
        </select></label><label>Effort<select aria-label={`${field.label} effort`} value={settings.models[effortKey]} disabled={saving} onchange={event => modelChange(effortKey, effort(event))}>{#each efforts as level}<option value={level}>{level}</option>{/each}</select></label></div>
      {/each}
    </section>
    <section class="setting-section"><h3>News polling</h3><label class="poll-setting">Poll interval, seconds<input aria-label="Poll interval, seconds" type="number" min="60" max="3600" step="1" value={settings.poll_seconds} disabled={saving} onchange={event => { const seconds = Number(event.currentTarget.value); if (settings && event.currentTarget.checkValidity() && Number.isInteger(seconds)) void save({ ...settings, poll_seconds: seconds }); else event.currentTarget.value = String(settings?.poll_seconds ?? 120) }} /></label>
      <div class="channel-list">{#each settings.channels as channel, i (channel.handle)}<label class="toggle-row"><span><strong>{channel.title}</strong><small>@{channel.handle} · {new Intl.NumberFormat('en-GB', { notation: 'compact' }).format(channel.subscribers)} subscribers</small></span><input type="checkbox" aria-label={`Channel @${channel.handle}`} checked={channel.enabled} disabled={saving} onchange={event => settings && void save({ ...settings, channels: settings.channels.map((item, index) => index === i ? { ...item, enabled: event.currentTarget.checked } : item) })} /></label>{/each}</div>
    </section>
    <section class="setting-section"><button class="setting-action" onclick={reopennews}>Show news card again</button></section>
  {:else}<p>Settings are unavailable. Close and reopen this panel to retry.</p>{/if}
</dialog>
