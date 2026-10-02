import { ShieldCheck } from 'lucide-react';

const REPO = 'github.com/JKeeter/token-cop';

export default function AttractSplash({ variant }: { variant: 'open' | 'closing' }) {
  return (
    <div className="relative flex h-full flex-col items-center justify-center overflow-hidden bg-ink-950">
      {/* ambient glow */}
      <div className="pointer-events-none absolute -top-40 left-1/2 h-[36rem] w-[60rem] -translate-x-1/2 rounded-full bg-cop-blue/10 blur-3xl" />
      <div className="pointer-events-none absolute bottom-0 right-0 h-80 w-80 rounded-full bg-cop-teal/10 blur-3xl" />

      {variant === 'open' ? (
        <>
          <div className="flex items-center gap-5 animate-fadeup">
            <span className="flex h-20 w-20 items-center justify-center rounded-2xl border border-cop-blue/40 bg-cop-blue/10">
              <ShieldCheck className="h-11 w-11 text-cop-blue" />
            </span>
            <div className="text-left">
              <div className="text-7xl font-black tracking-[0.22em] text-white">TOKEN COP</div>
              <div className="mt-2 text-lg tracking-[0.35em] text-cop-blue">EVERY TOKEN ACCOUNTED FOR</div>
            </div>
          </div>
          <div className="mt-8 max-w-2xl text-center text-xl leading-relaxed text-slate-300 animate-fadeup">
            An LLM cost-cop agent on <span className="font-semibold text-white">Amazon Bedrock AgentCore</span> —
            spend, audits, budgets and enforcement, one question away in Claude Code.
          </div>
        </>
      ) : (
        <div className="max-w-4xl text-center animate-fadeup">
          <div className="text-5xl font-black leading-tight text-white">
            More tokens is <span className="text-cop-amber">FINE</span> —<br />
            they need to be <span className="text-cop-teal">SMART</span> tokens.
          </div>
          <div className="mt-6 text-xl tracking-[0.3em] text-cop-blue">TOKEN COP</div>
        </div>
      )}

      <div className="mt-12 flex items-center gap-6 animate-fadeup">
        <div className="rounded-xl bg-white p-3 shadow-2xl">
          <img
            src={`${import.meta.env.BASE_URL}qr/github.png`}
            alt="Repository QR code"
            className="h-36 w-36"
            style={{ imageRendering: 'pixelated' }}
            onError={(e) => {
              (e.target as HTMLImageElement).parentElement!.style.display = 'none';
            }}
          />
        </div>
        <div className="text-left">
          <div className="text-sm uppercase tracking-widest text-slate-500">Scan for the repo</div>
          <div className="mt-1 font-mono text-lg text-cop-teal">{REPO}</div>
          <div className="mt-3 text-xs text-slate-500">Touch to pause · ← → to browse · Esc Esc to exit</div>
        </div>
      </div>
    </div>
  );
}
