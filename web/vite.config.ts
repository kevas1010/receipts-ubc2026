import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: the FastAPI backend (uvicorn receipts.web:app) serves /api and /badge on :8000.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/badge': 'http://localhost:8000',
    },
  },
})
