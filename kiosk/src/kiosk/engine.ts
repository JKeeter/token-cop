// The kiosk engine: an infinite pause-aware loop over the playlist.
// Adapted from the Ledgerly kiosk (AgentCoreKiosk) with the live-app pieces
// removed and presenter keys added. Every delay runs through the speed
// multiplier so ?kioskFast=1 plays the whole loop in seconds.
import { CaptureMissingError, loadCapture } from './captures';
import { disableKiosk, kioskSpeed, persistSceneIdx, restoreSceneIdx } from './flag';
import { resolvePlaylist, type Scene } from './playlist';
import { kiosk } from './state';

const PAUSE_MS = 60_000;
const LOG_KEY = 'tokencop.kiosk.log';
const RELOADS_KEY = 'tokencop.kiosk.reloads';

interface Ctx {
  cancelled: boolean;
  sleep(rawMs: number): Promise<void>;
  animate(rawMs: number, onProgress: (fraction: number) => void): Promise<void>;
}

let started = false;
let skipResolve: ((target: number) => void) | null = null;
let currentIdx = 0;
let sceneCount = 1;

export function startEngine(): void {
  if (started) return;
  started = true;
  attachInput();
  void loop();
}

export function resumeNow(): void {
  kiosk.set({ pauseUntil: 0 });
}

const rawSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const normalize = (idx: number) => ((idx % sceneCount) + sceneCount) % sceneCount;

function log(event: string, detail = ''): void {
  try {
    const ring = JSON.parse(window.localStorage.getItem(LOG_KEY) ?? '[]') as unknown[];
    ring.push({ at: new Date().toISOString(), scene: kiosk.get().sceneId, event, detail });
    window.localStorage.setItem(LOG_KEY, JSON.stringify(ring.slice(-50)));
  } catch {
    /* logging must never break the loop */
  }
}

function makeCtx(): Ctx {
  const ctx: Ctx = {
    cancelled: false,
    async animate(rawMs, onProgress) {
      const total = Math.max(1, rawMs * kioskSpeed());
      // Wall-clock elapsed (paused time excluded): setTimeout(32) really takes
      // ~35ms, so counting ticks as 32ms made every scene run ~5% long and
      // drift against a narration track.
      let elapsed = 0;
      let last = Date.now();
      while (elapsed < total && !ctx.cancelled) {
        if (Date.now() < kiosk.get().pauseUntil) {
          await rawSleep(250);
          last = Date.now();
          continue;
        }
        await rawSleep(32);
        const now = Date.now();
        elapsed += now - last;
        last = now;
        onProgress(Math.min(1, elapsed / total));
      }
      if (!ctx.cancelled) onProgress(1);
    },
    sleep: (rawMs) => ctx.animate(rawMs, () => undefined),
  };
  return ctx;
}

async function runCaptions(scene: Scene, ctx: Ctx): Promise<void> {
  for (let i = 0; i < scene.captions.length; i++) {
    if (ctx.cancelled) return;
    kiosk.set({ caption: scene.captions[i].text, archStep: i });
    const nextAt = scene.captions[i + 1]?.atMs ?? scene.dwellMs;
    await ctx.sleep(nextAt - scene.captions[i].atMs);
  }
}

async function runReplay(scene: Scene, ctx: Ctx): Promise<void> {
  const capture = await loadCapture(scene.capture!);
  const pacing = capture.pacing;
  const promptChars = capture.prompt.length;
  const responseChars = capture.responseMarkdown.length;
  const patch = (p: Partial<NonNullable<ReturnType<typeof kiosk.get>['replay']>>) => {
    const replay = kiosk.get().replay;
    if (replay) kiosk.set({ replay: { ...replay, ...p } });
  };

  kiosk.set({ replay: { capture, phase: 'typing', typed: 0, toolsShown: 0, streamed: 0 } });
  await ctx.animate(promptChars * pacing.typeMsPerChar, (f) => patch({ typed: Math.round(f * promptChars) }));

  patch({ phase: 'tools' });
  for (let i = 1; i <= capture.toolCalls.length; i++) {
    patch({ toolsShown: i });
    await ctx.sleep(pacing.toolChipMs);
  }

  // Auto-fit the stream so a genuine (possibly long) response always finishes
  // inside the scene, keeping ~20% of the remaining dwell for reading.
  const spentRaw = promptChars * pacing.typeMsPerChar + capture.toolCalls.length * pacing.toolChipMs;
  const budget = Math.max(4_000, (scene.dwellMs - spentRaw) * 0.8);
  const msPerChar = Math.min(pacing.streamMsPerChar, budget / responseChars);
  patch({ phase: 'streaming' });
  await ctx.animate(responseChars * msPerChar, (f) => patch({ streamed: Math.round(f * responseChars) }));
  patch({ phase: 'done' });
  await ctx.sleep(Math.max(2_500, scene.dwellMs - spentRaw - responseChars * msPerChar));
}

async function runScene(scene: Scene, ctx: Ctx): Promise<void> {
  const captions = runCaptions(scene, ctx);
  switch (scene.type) {
    case 'attract':
    case 'stat':
    case 'arch':
      await ctx.sleep(scene.dwellMs);
      break;
    case 'media': {
      const assets = scene.assets ?? [];
      const frameRaw = scene.dwellMs / Math.max(1, assets.length);
      for (let i = 0; i < Math.max(1, assets.length); i++) {
        kiosk.set({ media: { assets, activeIdx: i, frameMs: frameRaw * kioskSpeed() } });
        await ctx.sleep(frameRaw);
      }
      break;
    }
    case 'replay':
      await runReplay(scene, ctx);
      break;
  }
  await captions;
}

function recordReloadAndMaybeGiveUp(reason: string): void {
  let reloads: number[] = [];
  try {
    reloads = (JSON.parse(window.localStorage.getItem(RELOADS_KEY) ?? '[]') as number[]).filter(
      (t) => Date.now() - t < 10 * 60_000,
    );
  } catch {
    reloads = [];
  }
  if (reloads.length >= 2) {
    kiosk.set({ phase: 'attendant', attendantReason: reason });
    return;
  }
  reloads.push(Date.now());
  window.localStorage.setItem(RELOADS_KEY, JSON.stringify(reloads));
  window.location.reload();
}

async function loop(): Promise<void> {
  const playlist = resolvePlaylist();
  sceneCount = playlist.length;
  kiosk.set({ phase: 'running', sceneCount });
  currentIdx = restoreSceneIdx(sceneCount);
  let consecutiveFailures = 0;

  for (;;) {
    if (kiosk.get().phase === 'attendant') return;
    const idx = normalize(currentIdx);
    currentIdx = idx;
    const scene = playlist[idx];
    persistSceneIdx(idx);
    const ctx = makeCtx();
    kiosk.set({
      sceneId: scene.id,
      sceneType: scene.type,
      component: scene.component ?? null,
      sceneIndex: idx,
      badge: scene.badge,
      caption: '',
      archStep: -1,
      media: null,
      replay: null,
    });

    const skip = new Promise<number>((resolve) => {
      skipResolve = resolve;
    });
    const watchdog = ctx.sleep(scene.maxMs).then(() => 'timeout' as const);

    let next = idx + 1;
    try {
      const outcome = await Promise.race([runScene(scene, ctx).then(() => 'done' as const), watchdog, skip]);
      consecutiveFailures = 0;
      if (typeof outcome === 'number') next = outcome;
      else if (outcome === 'timeout') log('watchdog-timeout');
    } catch (err) {
      log('error', String(err));
      if (!(err instanceof CaptureMissingError)) {
        consecutiveFailures += 1;
        if (consecutiveFailures >= 2) {
          recordReloadAndMaybeGiveUp(`Scene "${scene.id}" failed repeatedly: ${String(err)}`);
          return;
        }
        next = idx; // retry the scene once
      }
    } finally {
      ctx.cancelled = true;
      skipResolve = null;
    }

    if (next > idx && normalize(next) === 0) kiosk.set({ loops: kiosk.get().loops + 1 });
    currentIdx = next;
  }
}

function bumpPause(): void {
  if (kiosk.get().pauseUntil === Infinity) return; // manual pause wins
  kiosk.set({ pauseUntil: Date.now() + PAUSE_MS });
}

function attachInput(): void {
  let lastEsc = 0;
  window.addEventListener(
    'keydown',
    (e) => {
      if (e.key === 'Escape') {
        const now = Date.now();
        if (now - lastEsc < 1_500) {
          disableKiosk();
          window.location.reload();
          return;
        }
        lastEsc = now;
        return;
      }
      if (e.key === 'ArrowRight' || e.key === 'PageDown') {
        e.preventDefault();
        kiosk.set({ pauseUntil: 0 });
        skipResolve?.(currentIdx + 1);
        return;
      }
      if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
        e.preventDefault();
        kiosk.set({ pauseUntil: 0 });
        skipResolve?.(currentIdx - 1);
        return;
      }
      if (e.key === ' ') {
        e.preventDefault();
        kiosk.set({ pauseUntil: kiosk.get().pauseUntil === Infinity ? 0 : Infinity });
        return;
      }
      bumpPause();
    },
    true,
  );
  for (const type of ['pointerdown', 'wheel', 'touchstart'] as const) {
    window.addEventListener(
      type,
      (e) => {
        const target = e.target as HTMLElement | null;
        if (target?.closest?.('[data-kiosk-resume]')) return;
        bumpPause();
      },
      true,
    );
  }
}
