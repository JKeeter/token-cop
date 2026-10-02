import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import KioskApp from './KioskApp';
import { startEngine } from './kiosk/engine';
import { initFlagFromUrl, kioskEnabled } from './kiosk/flag';
import './index.css';

initFlagFromUrl();
if (kioskEnabled()) startEngine();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <KioskApp />
  </StrictMode>,
);
