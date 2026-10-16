import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: { host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: { '/workspace/api': { target: 'http://127.0.0.1:8001', changeOrigin: false },
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } } },
})
