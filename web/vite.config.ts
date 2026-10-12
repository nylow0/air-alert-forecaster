import { svelte } from '@sveltejs/vite-plugin-svelte'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig, loadEnv } from 'vite'
import { readFileSync } from 'node:fs'
import { mockApi } from './dev/mock-api.ts'

// https://vite.dev/config/
export default defineConfig(({ command, mode }) => {
  const mock = command === 'serve' && (mode === 'mock' || loadEnv(mode, process.cwd()).VITE_MOCK_API === '1')
  // Until Meta carries this policy, read its single source of truth at build time.
  const config = readFileSync(new URL('../src/airalert/config.py', import.meta.url), 'utf8')
  const recovery = config.match(/^DATA_GAP_FEATURE_RECOVERY_H\s*=\s*(\d+)/m)?.[1]
  if (!recovery) throw new Error('Missing DATA_GAP_FEATURE_RECOVERY_H in airalert/config.py')
  return {
    define: { __MOCK_API__: JSON.stringify(mock), __DATA_GAP_FEATURE_RECOVERY_H__: recovery },
    plugins: [svelte(), tailwindcss(), ...(mock ? [mockApi()] : [])],
    server: {
      proxy: {
        '/api': 'http://127.0.0.1:8000',
      },
    },
  }
})
