import { svelte } from '@sveltejs/vite-plugin-svelte'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig, loadEnv } from 'vite'
import { mockApi } from './dev/mock-api.ts'

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [svelte(), tailwindcss(), ...(loadEnv(mode, process.cwd()).VITE_MOCK_API === '1' ? [mockApi()] : [])],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
}))
