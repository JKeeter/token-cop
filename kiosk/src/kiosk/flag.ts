// Kiosk activation flag, persisted to localStorage so it survives reloads.
// ?kiosk=1 enables, ?kiosk=0 disables; ?kioskFast=1 runs every delay at 5%
// speed so a full 5-minute loop plays in ~15s (used for smoke testing).
const KEY = 'tokencop.kiosk';
const SPEED_KEY = 'tokencop.kiosk.speed';
const IDX_KEY = 'tokencop.kiosk.idx';
const FAST_SPEED = '0.05';

export function initFlagFromUrl(): void {
  const params = new URLSearchParams(window.location.search);
  const kioskParam = params.get('kiosk');
  if (kioskParam === '1') window.localStorage.setItem(KEY, '1');
  if (kioskParam === '0') disableKiosk();
  const fast = params.get('kioskFast');
  if (fast === '1') window.localStorage.setItem(SPEED_KEY, FAST_SPEED);
  if (fast === '0') window.localStorage.removeItem(SPEED_KEY);
}

export function kioskEnabled(): boolean {
  return window.localStorage.getItem(KEY) === '1';
}

export function enableKiosk(): void {
  window.localStorage.setItem(KEY, '1');
}

export function disableKiosk(): void {
  window.localStorage.removeItem(KEY);
  window.localStorage.removeItem(IDX_KEY);
}

export function kioskSpeed(): number {
  const raw = Number.parseFloat(window.localStorage.getItem(SPEED_KEY) ?? '1');
  if (!Number.isFinite(raw)) return 1;
  return Math.min(1, Math.max(0.01, raw));
}

export function restoreSceneIdx(sceneCount: number): number {
  const raw = Number.parseInt(window.localStorage.getItem(IDX_KEY) ?? '0', 10);
  if (!Number.isFinite(raw) || raw < 0 || raw >= sceneCount) return 0;
  return raw;
}

export function persistSceneIdx(idx: number): void {
  window.localStorage.setItem(IDX_KEY, String(idx));
}
