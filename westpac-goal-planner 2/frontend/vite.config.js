import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// host: true lets the Android emulator reach the dev server at http://10.0.2.2:5173
export default defineConfig({
  plugins: [react()],
  cacheDir: '.runtime/vite-cache',
  server: { host: true, port: 5173, strictPort: true, proxy: { '/api': 'http://127.0.0.1:8000' } },
})
