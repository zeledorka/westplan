import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backend = process.env.WESTPLAN_API_URL || 'http://127.0.0.1:8000'
const demoOrigins = new Set(['http://127.0.0.1:5180', 'http://localhost:5180', 'http://127.0.0.1:5181', 'http://localhost:5181'])

export default defineConfig({
  plugins: [react()],
  cacheDir: '.runtime/vite-cache',
  server: { host: true, port: 5173, strictPort: true, proxy: {
    '/api': {
      target: backend,
      changeOrigin: true,
      configure(proxy) {
        proxy.on('proxyReq', (outgoing, incoming) => {
          const origin = incoming.headers.origin
          // Only normalize an exact local demo origin matching this frontend host.
          // Foreign origins pass through unchanged and are rejected by the backend.
          if (demoOrigins.has(origin) && new URL(origin).host === incoming.headers.host) {
            outgoing.setHeader('Origin', new URL(backend).origin)
          }
        })
      },
    },
  } },
})
