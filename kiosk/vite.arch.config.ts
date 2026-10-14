import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';

// Single-entry build of arch.html only (no shared-chunk splitting), so
// scripts/bundle-arch-page.mjs can inline everything into one standalone
// HTML file. The regular multi-entry build (vite.config.ts) still serves
// /arch.html from the dev/preview server.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    outDir: 'dist-arch',
    assetsInlineLimit: 1 << 20, // data-URI every asset (AWS icon SVGs)
    rollupOptions: {
      input: resolve(__dirname, 'arch.html'),
      output: { inlineDynamicImports: true },
    },
  },
});
