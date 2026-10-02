import clsx from 'clsx';
import type { ReactNode } from 'react';

// Shared pieces for the step-synced architecture scenes, modeled on
// Ledgerly's ArchitectureSlide: node cards light up per caption step while
// the rest dim; arrows render in an SVG underlay beneath the cards.

export type Tone = 'hot' | 'seen' | 'dim';

export interface ArchNode {
  id: string;
  x: number; // 0-100 (% of stage width)
  y: number; // 0-100 (% of stage height)
  title: string;
  sub?: string;
  icon?: string; // SVG asset URL
  glyph?: ReactNode; // lucide fallback for non-AWS nodes
  w?: number; // px
}

export interface ArchEdge {
  from: string;
  to: string;
  step: number; // step at which the edge lights up
  dashed?: boolean;
  curve?: { cx: number; cy: number }; // optional quadratic control point (0-100 space)
}

export function toneFor(id: string, step: number, hotByStep: string[][]): Tone {
  if (step < 0) return 'dim';
  if (hotByStep[step]?.includes(id)) return 'hot';
  for (let s = 0; s < Math.min(step, hotByStep.length); s++) {
    if (hotByStep[s].includes(id)) return 'seen';
  }
  return 'dim';
}

export function NodeCard({ node, tone }: { node: ArchNode; tone: Tone }) {
  return (
    <div
      className={clsx(
        'absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1.5 rounded-xl border px-4 py-3 text-center transition-all duration-500',
        tone === 'hot' && 'z-10 scale-110 border-cop-blue bg-ink-800 shadow-[0_0_32px_rgba(96,139,250,0.35)]',
        tone === 'seen' && 'border-ink-700 bg-ink-900 opacity-90',
        tone === 'dim' && 'border-ink-800 bg-ink-900 opacity-40',
      )}
      style={{ left: `${node.x}%`, top: `${node.y}%`, width: node.w ?? 158 }}
    >
      {node.icon ? (
        <img src={node.icon} alt="" className="h-10 w-10" />
      ) : node.glyph ? (
        <span className={clsx('flex h-10 w-10 items-center justify-center', tone === 'hot' ? 'text-cop-blue' : 'text-slate-400')}>
          {node.glyph}
        </span>
      ) : null}
      <div className="text-[13px] font-semibold leading-tight text-slate-100">{node.title}</div>
      {node.sub && <div className="text-[11px] leading-tight text-slate-500">{node.sub}</div>}
    </div>
  );
}

export function ArrowLayer({ nodes, edges, step }: { nodes: ArchNode[]; edges: ArchEdge[]; step: number }) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  return (
    <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
      {edges.map((edge, i) => {
        const from = byId.get(edge.from);
        const to = byId.get(edge.to);
        if (!from || !to) return null;
        const lit = step === edge.step;
        const seen = step > edge.step;
        const d = edge.curve
          ? `M ${from.x} ${from.y} Q ${edge.curve.cx} ${edge.curve.cy} ${to.x} ${to.y}`
          : `M ${from.x} ${from.y} L ${to.x} ${to.y}`;
        return (
          <path
            key={i}
            d={d}
            fill="none"
            stroke={lit ? '#608bfa' : seen ? '#334368' : '#1d2a4a'}
            strokeWidth={lit ? 2.5 : 1.5}
            strokeDasharray={edge.dashed ? '6 5' : undefined}
            vectorEffect="non-scaling-stroke"
            className="transition-all duration-500"
          />
        );
      })}
    </svg>
  );
}

export function ArchStage({
  kicker,
  title,
  nodes,
  edges,
  hotByStep,
  step,
}: {
  kicker: string;
  title: string;
  nodes: ArchNode[];
  edges: ArchEdge[];
  hotByStep: string[][];
  step: number;
}) {
  return (
    <div className="relative h-full bg-ink-950">
      <div className="absolute left-10 top-8 z-10">
        <div className="text-xs uppercase tracking-[0.35em] text-cop-amber">{kicker}</div>
        <div className="mt-1 text-3xl font-bold text-white">{title}</div>
      </div>
      {/* stage leaves the bottom ~22% clear for the caption pill */}
      <div className="absolute inset-x-0 bottom-[20%] top-[12%]">
        <ArrowLayer nodes={nodes} edges={edges} step={step} />
        {nodes.map((node) => (
          <NodeCard key={node.id} node={node} tone={toneFor(node.id, step, hotByStep)} />
        ))}
      </div>
    </div>
  );
}
