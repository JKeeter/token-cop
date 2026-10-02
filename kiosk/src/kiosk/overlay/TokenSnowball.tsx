import clsx from 'clsx';
import { useCountUp } from '../lib/useCountUp';
import { useKiosk } from '../state';

// Native rebuild of the deck's "snowball effect" slide: each chat turn re-sends
// the whole history, so per-turn token cost grows every turn.
const TURNS = [
  { n: 1, tokens: 3_000 },
  { n: 2, tokens: 4_000 },
  { n: 3, tokens: 5_200 },
  { n: 4, tokens: 6_600 },
];
const TOTAL = 18_800;
const MAX = 6_600;

function TurnBar({ n, tokens, visible, delayMs }: { n: number; tokens: number; visible: boolean; delayMs: number }) {
  const value = useCountUp(tokens, visible, 1400 + delayMs);
  return (
    <div className={clsx('flex items-center gap-6 transition-opacity duration-500', visible ? 'opacity-100' : 'opacity-20')}>
      <div className="w-28 text-right text-lg font-semibold text-slate-400">Turn {n}</div>
      <div className="h-12 flex-1 overflow-hidden rounded-lg bg-ink-800">
        <div
          className="flex h-full items-center justify-end rounded-lg bg-gradient-to-r from-cop-blue/50 to-cop-blue pr-4 transition-[width] duration-[1400ms] ease-out"
          style={{ width: visible ? `${(tokens / MAX) * 100}%` : '0%', transitionDelay: `${delayMs}ms` }}
        >
          <span className="font-mono text-lg font-bold tabular-nums text-ink-950">{value.toLocaleString()}</span>
        </div>
      </div>
    </div>
  );
}

export default function TokenSnowball() {
  const step = useKiosk((s) => s.archStep);
  const totalValue = useCountUp(TOTAL, step >= 2, 1600);
  return (
    <div className="flex h-full flex-col items-center justify-center bg-ink-950 px-[12vw]">
      <div className="mb-2 text-sm uppercase tracking-[0.35em] text-cop-amber">The snowball effect</div>
      <div className="mb-10 text-4xl font-bold text-white">Same chat, growing bill</div>
      <div className="w-full max-w-4xl space-y-5">
        {TURNS.map((t, i) => (
          <TurnBar key={t.n} n={t.n} tokens={t.tokens} visible={step >= 0} delayMs={i * 650} />
        ))}
      </div>
      <div
        className={clsx(
          'mt-12 flex items-baseline gap-4 transition-all duration-700',
          step >= 2 ? 'translate-y-0 opacity-100' : 'translate-y-4 opacity-0',
        )}
      >
        <span className="text-xl text-slate-400">4-turn conversation:</span>
        <span className="font-mono text-7xl font-black tabular-nums text-cop-amber">{totalValue.toLocaleString()}</span>
        <span className="text-xl text-slate-400">tokens</span>
      </div>
      <div
        className={clsx(
          'mt-6 text-lg text-slate-500 transition-opacity duration-700',
          step >= 2 ? 'opacity-100' : 'opacity-0',
        )}
      >
        prompt caching bills the re-reads at ~10% — the volume was still 64% of our real spend
      </div>
    </div>
  );
}
