import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const here = path.dirname(fileURLToPath(import.meta.url))

// https://vite.dev/config/
export default defineConfig({
  // Tailwind only compiles src/styles/bklit.css (utilities for Bklit charts,
  // no global reset); the rest of the app's CSS is untouched.
  plugins: [react(), tailwindcss()],
  resolve: {
    // "@/" = src/, the import path shadcn/Bklit components use.
    alias: { '@': path.resolve(here, 'src') },
  },
})
