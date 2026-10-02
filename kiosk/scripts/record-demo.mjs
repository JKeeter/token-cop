// Records one full kiosk loop as 1920x1080 webm and writes timeline.json with
// the wall-clock start time of every scene (read from the progress dots), so
// a narration track can be muxed at exact scene offsets.
// Usage: node scripts/record-demo.mjs   (expects `npm run preview` on :4173,
// override with KIOSK_URL)
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const BASE = process.env.KIOSK_URL ?? 'http://localhost:4173';
const OUT = join(dirname(fileURLToPath(import.meta.url)), '..', 'demo-video');
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  recordVideo: { dir: OUT, size: { width: 1920, height: 1080 } },
});
const page = await context.newPage();
const t0 = Date.now();
await page.goto(`${BASE}/?kiosk=1`);

const events = [];
let last = -1;
for (;;) {
  const idx = await page.evaluate(() => {
    const container = document.querySelector('.bottom-6');
    if (!container) return -1;
    return Array.from(container.children).findIndex((d) => d.classList.contains('w-7'));
  });
  const t = (Date.now() - t0) / 1000;
  if (idx !== -1 && idx !== last) {
    events.push({ idx, t: Number(t.toFixed(2)) });
    console.log(`scene ${idx} @ ${t.toFixed(1)}s`);
    if (idx === 0 && last !== -1) break; // wrapped — full loop recorded
    last = idx;
  }
  if (t > 420) {
    console.error('safety timeout — loop never wrapped');
    break;
  }
  await new Promise((r) => setTimeout(r, 150));
}
const tEnd = Number(((Date.now() - t0) / 1000).toFixed(2));
await new Promise((r) => setTimeout(r, 400));

const video = page.video();
await context.close(); // finalizes the webm
const videoPath = await video.path();
await browser.close();

writeFileSync(join(OUT, 'timeline.json'), JSON.stringify({ events, tEnd, videoPath }, null, 2));
console.log('video:', videoPath);
console.log('timeline:', join(OUT, 'timeline.json'));
