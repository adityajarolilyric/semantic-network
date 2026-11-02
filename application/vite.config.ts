import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  // These values are consumed by the development server, never exposed as VITE_* client variables.
  const env = { ...loadEnv(mode, process.cwd(), 'METADATA_'), ...process.env }
  return {
    plugins: [react()],
    server: {
      port: 5174,
      strictPort: true,
      proxy: {
        '/api': {
          target: env.METADATA_API_TARGET || 'http://127.0.0.1:8788',
          changeOrigin: true,
          ...(env.METADATA_API_KEY
            ? { headers: { Authorization: `Bearer ${env.METADATA_API_KEY}` } }
            : {}),
        },
      },
    },
  }
})
