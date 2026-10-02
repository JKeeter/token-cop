import { create } from 'zustand';
import type { Capture } from './captures';
import type { Badge, SceneComponent, SceneType } from './playlist';

export type Phase = 'idle' | 'running' | 'attendant';
export type ReplayPhase = 'typing' | 'tools' | 'streaming' | 'done';

export interface ReplayProgress {
  capture: Capture;
  phase: ReplayPhase;
  typed: number;
  toolsShown: number;
  streamed: number;
}

export interface MediaState {
  assets: string[];
  activeIdx: number;
  frameMs: number;
}

export interface KioskState {
  phase: Phase;
  attendantReason: string;
  sceneId: string;
  sceneType: SceneType | null;
  component: SceneComponent | null;
  sceneIndex: number;
  sceneCount: number;
  badge: Badge;
  caption: string;
  archStep: number;
  media: MediaState | null;
  replay: ReplayProgress | null;
  pauseUntil: number; // epoch ms; Infinity = manual (Space) pause
  loops: number;
}

const initial: KioskState = {
  phase: 'idle',
  attendantReason: '',
  sceneId: '',
  sceneType: null,
  component: null,
  sceneIndex: 0,
  sceneCount: 0,
  badge: null,
  caption: '',
  archStep: -1,
  media: null,
  replay: null,
  pauseUntil: 0,
  loops: 0,
};

export const useKiosk = create<KioskState>(() => initial);

// Imperative access for the engine (non-React code).
export const kiosk = {
  get: () => useKiosk.getState(),
  set: (partial: Partial<KioskState>) => useKiosk.setState(partial),
};
