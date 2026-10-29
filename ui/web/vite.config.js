import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// В разработке API отдаёт мост ui/server.py (python -m ui.server), фронт — vite с горячей перезагрузкой.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8642' } },
})
