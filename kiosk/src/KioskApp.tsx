import { useEffect } from 'react';
import ArchMcpFlow from './kiosk/overlay/ArchMcpFlow';
import ArchPlatform from './kiosk/overlay/ArchPlatform';
import AttractSplash from './kiosk/overlay/AttractSplash';
import KioskOverlay from './kiosk/overlay/KioskOverlay';
import MediaLayer from './kiosk/overlay/MediaLayer';
import TerminalReplay from './kiosk/overlay/TerminalReplay';
import TokenSnowball from './kiosk/overlay/TokenSnowball';
import { enableKiosk, kioskEnabled } from './kiosk/flag';
import { useKiosk } from './kiosk/state';

function Landing() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === 'k') {
        enableKiosk();
        window.location.reload();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  return (
    <div className="flex h-full items-center justify-center bg-ink-950 text-slate-200">
      <div className="max-w-md rounded-2xl border border-ink-700 bg-ink-900 p-10 text-center shadow-2xl">
        <div className="text-3xl font-black tracking-[0.25em] text-cop-blue">TOKEN COP</div>
        <div className="mt-2 text-sm text-slate-400">kiosk demo</div>
        <p className="mt-6 text-sm leading-relaxed text-slate-300">
          Add <code className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-cop-teal">?kiosk=1</code> to the URL
          or press <span className="font-semibold text-white">K</span> to start the loop.
        </p>
        <p className="mt-4 text-xs text-slate-500">
          ← / → jump scenes · Space pauses · Esc Esc exits · any touch pauses 60s
        </p>
        <button
          onClick={() => {
            enableKiosk();
            window.location.reload();
          }}
          className="mt-8 rounded-lg bg-cop-blue px-6 py-2.5 text-sm font-semibold text-ink-950 transition hover:brightness-110"
        >
          Start kiosk
        </button>
      </div>
    </div>
  );
}

function SceneLayer() {
  const sceneType = useKiosk((s) => s.sceneType);
  const component = useKiosk((s) => s.component);
  if (sceneType === 'attract') return <AttractSplash variant={component === 'closing' ? 'closing' : 'open'} />;
  if (sceneType === 'media') return <MediaLayer />;
  if (sceneType === 'stat') return <TokenSnowball />;
  if (sceneType === 'replay') return <TerminalReplay />;
  if (sceneType === 'arch' && component === 'arch-mcp') return <ArchMcpFlow />;
  if (sceneType === 'arch' && component === 'arch-platform') return <ArchPlatform />;
  return null;
}

export default function KioskApp() {
  const sceneId = useKiosk((s) => s.sceneId);
  if (!kioskEnabled()) return <Landing />;
  return (
    <div className="relative h-full w-full select-none overflow-hidden bg-ink-950 text-slate-100">
      <div key={sceneId} className="absolute inset-0">
        <SceneLayer />
      </div>
      <KioskOverlay />
    </div>
  );
}
