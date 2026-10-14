import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import ArchPresenter from './ArchPresenter';
import '../index.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ArchPresenter />
  </StrictMode>,
);
