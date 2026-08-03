/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Split the heavy, rarely-changing libraries out of the app chunk so a
        // code change does not invalidate 200 kB of vendor code in the cache.
        manualChunks: {
          react: ['react', 'react-dom'],
          markdown: ['react-markdown', 'remark-gfm', 'remark-math', 'rehype-katex'],
          katex: ['katex'],
          d3: ['d3'],
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      // Keep SSE unbuffered in dev; nginx does the same in production.
      '/api': { target: 'http://backend:8000', changeOrigin: true, ws: false },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
