import clsx from 'clsx';
import { useKiosk } from '../state';

// Full-bleed Ken Burns image layer. For montage scenes the engine advances
// activeIdx; frames crossfade. A failed image simply never shows (the engine
// moves on by time), mirroring Ledgerly's media fallback chain.
export default function MediaLayer() {
  const media = useKiosk((s) => s.media);
  if (!media) return null;
  return (
    <div className="absolute inset-0 overflow-hidden bg-ink-950">
      {media.assets.map((asset, i) => (
        <img
          key={asset}
          src={`${import.meta.env.BASE_URL}${asset}`}
          alt=""
          onError={(e) => {
            (e.target as HTMLImageElement).style.display = 'none';
          }}
          className={clsx(
            'absolute inset-0 h-full w-full object-cover transition-opacity duration-700',
            i === media.activeIdx ? 'opacity-100' : 'opacity-0',
          )}
          style={
            i === media.activeIdx
              ? { animation: `kiosk-kenburns ${Math.max(1200, media.frameMs)}ms ease-out forwards` }
              : undefined
          }
        />
      ))}
      {/* soft vignette so captions stay readable over bright slides */}
      <div className="absolute inset-0 bg-gradient-to-t from-ink-950/80 via-transparent to-ink-950/30" />
    </div>
  );
}
