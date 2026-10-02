import clsx from 'clsx';
import { useEffect, useState } from 'react';
import { resumeNow } from '../engine';
import { useKiosk } from '../state';

function Badge() {
  const badge = useKiosk((s) => s.badge);
  const sceneType = useKiosk((s) => s.sceneType);
  // replay scenes carry their own RECORDED chip inside the terminal chrome
  if (!badge || sceneType === 'replay') return null;
  const recorded = badge === 'RECORDED';
  return (
    <div
      className={clsx(
        'absolute right-8 top-7 flex items-center gap-2 rounded-full border px-4 py-1.5 text-xs font-bold tracking-[0.2em]',
        recorded ? 'border-cop-amber/40 bg-cop-amber/10 text-cop-amber' : 'border-slate-500/40 bg-slate-500/10 text-slate-300',
      )}
    >
      <span className={clsx('h-2 w-2 rounded-full', recorded ? 'animate-pulsebadge bg-cop-amber' : 'bg-slate-400')} />
      {badge}
    </div>
  );
}

function CaptionPill() {
  const caption = useKiosk((s) => s.caption);
  if (!caption) return null;
  return (
    <div className="absolute inset-x-0 bottom-16 flex justify-center px-10">
      <div
        key={caption}
        className="max-w-4xl animate-fadeup rounded-2xl border border-white/10 bg-ink-900/85 px-7 py-4 text-center text-xl font-medium leading-snug text-slate-100 shadow-2xl backdrop-blur"
      >
        {caption}
      </div>
    </div>
  );
}

function ProgressDots() {
  const index = useKiosk((s) => s.sceneIndex);
  const count = useKiosk((s) => s.sceneCount);
  if (count < 2) return null;
  return (
    <div className="absolute inset-x-0 bottom-6 flex justify-center gap-2.5">
      {Array.from({ length: count }, (_, i) => (
        <span
          key={i}
          className={clsx(
            'h-1.5 rounded-full transition-all duration-500',
            i === index ? 'w-7 bg-cop-blue' : 'w-1.5 bg-slate-600',
          )}
        />
      ))}
    </div>
  );
}

function PausePill() {
  const pauseUntil = useKiosk((s) => s.pauseUntil);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(t);
  }, []);
  if (pauseUntil <= now) return null;
  const manual = pauseUntil === Infinity;
  const secondsLeft = manual ? null : Math.max(0, Math.ceil((pauseUntil - now) / 1000));
  return (
    <div className="pointer-events-auto absolute inset-x-0 top-7 flex justify-center" data-kiosk-resume>
      <div className="flex items-center gap-4 rounded-full border border-cop-amber/40 bg-ink-900/90 px-6 py-2.5 shadow-xl backdrop-blur">
        <span className="text-sm font-semibold text-cop-amber">
          {manual ? 'Paused — press Space to resume' : `Paused — resuming in ${secondsLeft}s`}
        </span>
        <button
          data-kiosk-resume
          onClick={resumeNow}
          className="rounded-full bg-cop-amber px-4 py-1 text-xs font-bold text-ink-950 transition hover:brightness-110"
        >
          Resume now
        </button>
      </div>
    </div>
  );
}

function AttendantScreen() {
  const phase = useKiosk((s) => s.phase);
  const reason = useKiosk((s) => s.attendantReason);
  if (phase !== 'attendant') return null;
  return (
    <div className="pointer-events-auto absolute inset-0 z-50 flex items-center justify-center bg-ink-950">
      <div className="max-w-lg rounded-2xl border border-cop-red/40 bg-ink-900 p-10 text-center shadow-2xl">
        <div className="text-2xl font-bold text-cop-red">Demo attendant needed</div>
        <p className="mt-4 text-sm leading-relaxed text-slate-300">{reason}</p>
        <p className="mt-4 text-xs text-slate-500">
          Reload the page to retry. Scene log: localStorage key <code>tokencop.kiosk.log</code>.
        </p>
        <button
          onClick={() => window.location.reload()}
          className="mt-8 rounded-lg bg-cop-blue px-6 py-2.5 text-sm font-semibold text-ink-950"
        >
          Reload
        </button>
      </div>
    </div>
  );
}

export default function KioskOverlay() {
  return (
    <div className="pointer-events-none absolute inset-0 z-40">
      <Badge />
      <CaptionPill />
      <ProgressDots />
      <PausePill />
      <AttendantScreen />
    </div>
  );
}
