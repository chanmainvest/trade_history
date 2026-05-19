import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: {
    preserveSymlinks: true,
  },
  server: {
    proxy: {
      '/trades': 'http://localhost:8000',
      '/asset-values': 'http://localhost:8000',
      '/sectors': 'http://localhost:8000',
      '/statements': 'http://localhost:8000',
      '/monthly-balances': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 600,
  },
})
