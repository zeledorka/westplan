import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// host: true lets the Android emulator reach the dev server at http://10.0.2.2:5173
export default defineConfig({
  plugins: [react()],
  server: { host: true, port: 5173 },
})
