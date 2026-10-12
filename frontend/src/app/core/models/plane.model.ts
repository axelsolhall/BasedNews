/** Mirrors pipeline/contracts.py PlanePoint (M9). x = economic (− left, + right); y = social (− liberal, + conservative). */
export interface PlanePoint {
  id: string;
  label: string;
  kind: 'country' | 'outlet';
  x: number;
  y: number;
  sd_x: number;
  sd_y: number;
  parent: string;
  n_outlets: number;
}

export interface PlaneData {
  meta: { oval_sd_multiplier: number; source?: string; calls_per_axis?: number };
  countries: PlanePoint[];
  outlets: PlanePoint[];
}

/** One animation frame from E3 (pipeline/sim/eval_semireal.py). tx/ty = simulated truth (relative). */
export interface FramePoint {
  id: string; label: string; country?: string; tier?: string;
  x: number; y: number; sd_x: number; sd_y: number; tx?: number; ty?: number;   // tx/ty: simulated truth only
}
export interface PlaneFrame { tokens: number; calls: number; cost_usd?: number; outlets: FramePoint[]; countries: FramePoint[]; }
export interface PlaneFrames {
  meta: { source: string; watermark: string; budget_tokens: number };
  random: PlaneFrame[];
  cost_aware: PlaneFrame[];
}
export interface RealFrames {
  meta: { source: string; experiment: string; model: string; prompt: string; calls: number; cost_usd: number };
  real: PlaneFrame[];
}

/** 4.2 Export (pipeline/export/run.py): one fit from all verdicts of the current prompt. Not validated (meta.validated). */
export interface LatestFit {
  meta: {
    source: string; kind: string; validated: boolean; prompt: string; models: string[]; rating_model: string;
    calls: number; decisive_verdicts: number; first_call: string; last_call: string; fitted_at: string; commit: string;
  };
  frame: { calls: number; cost_usd?: number; outlets: FramePoint[]; countries: FramePoint[] };
}
