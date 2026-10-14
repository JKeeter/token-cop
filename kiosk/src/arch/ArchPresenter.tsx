import clsx from 'clsx';
import { useCallback, useEffect, useState } from 'react';
import ArchMcpFlow from '../kiosk/overlay/ArchMcpFlow';
import ArchPlatform from '../kiosk/overlay/ArchPlatform';
import { KIOSK_PLAYLIST } from '../kiosk/playlist';
import { kiosk } from '../kiosk/state';

// Presenter-paced version of the two kiosk architecture scenes: same
// components, same step-synced highlight animations, but advanced by hand
// (keys / click) instead of the kiosk's caption timer — for talking through
// the architecture live without it disappearing on a dwell budget.

type DiagramId = 'mcp' | 'platform';

interface DeckStep {
  diagram: DiagramId;
  archStep: number;
  caption: string;
}

function sceneCaptions(sceneId: string): string[] {
  const scene = KIOSK_PLAYLIST.find((s) => s.id === sceneId);
  return scene ? scene.captions.map((c) => c.text) : [];
}

const MCP_CAPTIONS = sceneCaptions('arch-mcp');
const PLATFORM_CAPTIONS = sceneCaptions('arch-platform');

// Each diagram walks its kiosk caption steps, then holds on a "full picture"
// step (archStep past the last hot step renders every node as seen).
const DECK: DeckStep[] = [
  ...MCP_CAPTIONS.map((caption, i) => ({ diagram: 'mcp' as const, archStep: i, caption })),
  {
    diagram: 'mcp',
    archStep: MCP_CAPTIONS.length,
    caption: 'The whole skill path: one question, authenticated, routed, audited — seconds, not spreadsheets.',
  },
  ...PLATFORM_CAPTIONS.map((caption, i) => ({ diagram: 'platform' as const, archStep: i, caption })),
  {
    diagram: 'platform',
    archStep: PLATFORM_CAPTIONS.length,
    caption: 'The whole platform: memory, identity, policy, tracing, and hard budget enforcement around one agent.',
  },
];

const FIRST_IDX: Record<DiagramId, number> = {
  mcp: 0,
  platform: MCP_CAPTIONS.length + 1,
};

const DIAGRAM_LABELS: Record<DiagramId, string> = {
  mcp: '1 · The skill path',
  platform: '2 · The platform',
};

export default function ArchPresenter() {
  const [idx, setIdx] = useState(0);
  const cur = DECK[idx];

  useEffect(() => {
    kiosk.set({ archStep: cur.archStep });
  }, [cur]);

  const go = useCallback((delta: number) => {
    setIdx((i) => Math.min(DECK.length - 1, Math.max(0, i + delta)));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ' || e.key === 'Enter') {
        e.preventDefault();
        go(1);
      } else if (e.key === 'ArrowLeft' || e.key === 'PageUp' || e.key === 'Backspace') {
        e.preventDefault();
        go(-1);
      } else if (e.key === 'Home') {
        setIdx(0);
      } else if (e.key === 'End') {
        setIdx(DECK.length - 1);
      } else if (e.key === '1') {
        setIdx(FIRST_IDX.mcp);
      } else if (e.key === '2') {
        setIdx(FIRST_IDX.platform);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [go]);

  return (
    <div className="fixed inset-0 select-none bg-ink-950 text-slate-100" onClick={() => go(1)}>
      <div className="absolute inset-0">
        {cur.diagram === 'mcp' ? <ArchMcpFlow /> : <ArchPlatform />}
      </div>

      {/* diagram switcher */}
      <div className="absolute right-10 top-8 z-20 flex gap-2" onClick={(e) => e.stopPropagation()}>
        {(Object.keys(FIRST_IDX) as DiagramId[]).map((d) => (
          <button
            key={d}
            onClick={() => setIdx(FIRST_IDX[d])}
            className={clsx(
              'rounded-full border px-4 py-1.5 text-xs font-semibold uppercase tracking-widest transition-colors',
              cur.diagram === d
                ? 'border-cop-blue bg-ink-800 text-cop-blue'
                : 'border-ink-700 bg-ink-900 text-slate-500 hover:text-slate-300',
            )}
          >
            {DIAGRAM_LABELS[d]}
          </button>
        ))}
      </div>

      {/* caption + controls (same bottom band the kiosk reserves) */}
      <div
        className="absolute inset-x-0 bottom-0 z-20 flex flex-col items-center gap-3 pb-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="max-w-4xl rounded-full border border-ink-700 bg-ink-900/90 px-7 py-3 text-center text-lg text-slate-200 shadow-lg">
          {cur.caption}
        </div>
        <div className="flex items-center gap-4">
          <button
            onClick={() => go(-1)}
            disabled={idx === 0}
            className="rounded-full border border-ink-700 bg-ink-900 px-4 py-1.5 text-sm text-slate-300 transition-colors hover:border-cop-blue hover:text-cop-blue disabled:opacity-30 disabled:hover:border-ink-700 disabled:hover:text-slate-300"
          >
            ← Back
          </button>
          <div className="flex gap-1.5">
            {DECK.map((s, i) => (
              <button
                key={i}
                onClick={() => setIdx(i)}
                aria-label={`step ${i + 1}`}
                className={clsx(
                  'h-2 rounded-full transition-all duration-300',
                  i === idx ? 'w-6 bg-cop-blue' : 'w-2 bg-ink-700 hover:bg-ink-700/70',
                  s.diagram !== cur.diagram && 'opacity-50',
                )}
              />
            ))}
          </div>
          <button
            onClick={() => go(1)}
            disabled={idx === DECK.length - 1}
            className="rounded-full border border-ink-700 bg-ink-900 px-4 py-1.5 text-sm text-slate-300 transition-colors hover:border-cop-blue hover:text-cop-blue disabled:opacity-30 disabled:hover:border-ink-700 disabled:hover:text-slate-300"
          >
            Next →
          </button>
        </div>
        <div className="text-[11px] uppercase tracking-[0.3em] text-slate-600">
          ← → / click to step · 1 / 2 jump diagrams · no timer
        </div>
      </div>
    </div>
  );
}
