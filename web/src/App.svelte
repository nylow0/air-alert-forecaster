<script lang="ts">
  import metaFixture from './lib/fixtures/meta.json'
  import type { Meta } from './lib/types'

  // The lead validates this imported fixture against the Python source of truth.
  const meta = metaFixture as Meta
  const snapshot = new Intl.DateTimeFormat('en-GB', {
    dateStyle: 'long',
    timeStyle: 'short',
    timeZone: 'Europe/Kyiv',
  }).format(new Date(meta.snapshot))
</script>

<main>
  <h1>Air Alert Forecaster</h1>
  <p>Dataset snapshot <time datetime={meta.snapshot}>{snapshot}</time> Kyiv time</p>
</main>
