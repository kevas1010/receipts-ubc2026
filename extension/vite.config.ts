import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Builds the Chrome extension into dist/: the side panel page plus everything in public/
// (manifest.json, background.js, content.js, content.css) copied as-is. Load dist/ as an unpacked extension.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: { sidepanel: 'sidepanel.html' },
    },
  },
})
