#!/usr/bin/env node
// Bundle the single-entry arch build (vite.arch.config.ts → dist-arch/) into
// one self-contained HTML file that opens directly from Finder (file://) —
// no server needed. Run via `npm run arch:page`; writes
// dist-arch/token-cop-architecture.html.
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dist = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'dist-arch');
let html = readFileSync(join(dist, 'arch.html'), 'utf8');

// Inline every stylesheet link.
html = html.replace(
  /<link[^>]+rel="stylesheet"[^>]+href="\.\/([^"]+)"[^>]*>/g,
  (_, href) => `<style>\n${readFileSync(join(dist, href), 'utf8')}\n</style>`,
);

// Inline every module script. Escape closing-tag sequences inside the bundle.
html = html.replace(
  /<script[^>]+type="module"[^>]+src="\.\/([^"]+)"[^>]*><\/script>/g,
  (_, src) => {
    const js = readFileSync(join(dist, src), 'utf8').replaceAll('</script', '<\\/script');
    return `<script type="module">\n${js}\n</script>`;
  },
);

// Catch leftover tag refs AND relative chunk imports inside the inlined JS
// (a split bundle would still fetch ./assets/*.js at runtime).
if (/<(link[^>]+stylesheet|script[^>]+src=)/.test(html) || html.includes('"./assets/') || html.includes('"assets/')) {
  console.error('bundle-arch-page: external references remain — inlining failed');
  process.exit(1);
}

const out = join(dist, 'token-cop-architecture.html');
writeFileSync(out, html);
console.log(`wrote ${out} (${(html.length / 1024).toFixed(0)} KB)`);
