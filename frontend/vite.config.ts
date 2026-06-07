import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// base: './' makes built asset URLs relative so pywebview can load dist/index.html
// directly from the filesystem (file://) when packaged into the .exe.
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  server: { port: 5173, strictPort: true },
  build: { outDir: 'dist', emptyOutDir: true },
})
