import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';

export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    outDir: 'dist',
    // Inline SVG icons etc. as data URIs so arch.html can be bundled into a
    // single standalone file (scripts/bundle-arch-page.mjs).
    assetsInlineLimit: 65536,
    rollupOptions: {
      input: {
        kiosk: resolve(__dirname, 'index.html'),
        arch: resolve(__dirname, 'arch.html'),
      },
    },
  },
});
