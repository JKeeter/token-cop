import { useEffect, useRef, useState } from 'react';
import { kioskSpeed } from '../flag';

// rAF count-up from 0 to target once `active` turns true; duration honors the
// kiosk speed multiplier so fast mode stays in sync.
export function useCountUp(target: number, active: boolean, durationMs = 1200): number {
  const [value, setValue] = useState(0);
  const raf = useRef(0);
  useEffect(() => {
    if (!active) return;
    const duration = Math.max(80, durationMs * kioskSpeed());
    const start = performance.now();
    const tick = (now: number) => {
      const f = Math.min(1, (now - start) / duration);
      const eased = 1 - (1 - f) * (1 - f);
      setValue(Math.round(target * eased));
      if (f < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [active, target, durationMs]);
  return active ? value : 0;
}
