import { Component, OnDestroy, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { LatestFit, PlaneFrame, PlaneFrames, PlanePoint, RealFrames } from '../../core/models';

type Strategy = 'cost_aware' | 'random' | 'real' | 'latest';

@Component({
  selector: 'app-plane-page',
  templateUrl: './plane.page.html',
  styleUrls: ['./plane.page.css']
})
export class PlanePage implements OnInit, OnDestroy {
  frames: PlaneFrames | null = null;
  real: RealFrames | null = null;                 // E4: the first run with the real judge (no truth to show)
  latest: LatestFit | null = null;               // 4.2 export: the current fit, not validated (only if published)
  strategy: Strategy = 'real';
  index = 0;
  playing = false;
  showTruth = true;
  country: string | null = null;
  error = '';
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(private http: HttpClient) {}

  ngOnInit(): void {
    // E3 semi-real replay (pipeline/sim/eval_semireal.py): real outlets & events, SIMULATED positions.
    this.http.get<PlaneFrames>('assets/plane-frames.json').subscribe({
      next: (f) => { this.frames = f; if (this.strategy !== 'real') this.index = this.series.length - 1; },
      error: () => (this.error = 'Could not load frames (run: uv run python -m pipeline.sim.eval_semireal)')
    });
    // E4 replay of the real judge's verdicts (pipeline/experiments/e4_frames.py)
    this.http.get<RealFrames>('assets/plane-real.json').subscribe({
      next: (r) => { this.real = r; if (this.strategy === 'real') this.index = this.series.length - 1; },
      error: () => { this.real = null; if (this.strategy === 'real') this.setStrategy('cost_aware'); }
    });
    // the export job's file next to the site (pipeline/export/run.py); absent until positions are published (A54)
    this.http.get<LatestFit>('data/plane-latest.json').subscribe({
      next: (l) => (this.latest = l),
      error: () => (this.latest = null)
    });
  }

  get isReal(): boolean { return this.strategy === 'real' || this.strategy === 'latest'; }
  get isLatest(): boolean { return this.strategy === 'latest'; }

  ngOnDestroy(): void { this.stop(); }

  get series(): PlaneFrame[] {
    if (this.strategy === 'latest') return this.latest ? [{ ...this.latest.frame, tokens: 0 }] : [];
    if (this.strategy === 'real') return this.real ? this.real.real : [];
    return this.frames ? this.frames[this.strategy] : [];
  }
  get frame(): PlaneFrame | null { return this.series[Math.min(this.index, this.series.length - 1)] ?? null; }

  get points(): PlanePoint[] {
    const f = this.frame;
    if (!f) return [];
    const src = this.country ? f.outlets.filter((o) => o.country === this.country) : f.countries;
    return src.map((p) => ({
      ...p,
      kind: this.country ? 'outlet' : 'country',
      parent: p.country ?? '',
      n_outlets: this.country ? 0 : f.outlets.filter((o) => o.country === p.id && o.tier === 'reference').length
    }) as any);
  }

  get range(): number {
    // fixed axis range across the whole animation so movement is visible, not re-scaled away
    const all = this.series.flatMap((f) => (this.country ? f.outlets.filter((o) => o.country === this.country) : f.countries));
    // real data spreads far less than the simulation (unit-variance truth): let it fill the plane
    const floor = this.isReal ? 0.4 : 1.5;
    const m = Math.max(floor, ...all.map((p) => Math.max(Math.abs(p.x) + p.sd_x, Math.abs(p.y) + p.sd_y, Math.abs(p.tx ?? 0), Math.abs(p.ty ?? 0))));
    return m * 1.08;
  }

  open(p: PlanePoint): void { if (!this.country) this.country = p.id; }
  setStrategy(s: Strategy): void { this.strategy = s; this.index = Math.max(0, this.series.length - 1); }

  play(): void {
    if (this.playing) { this.stop(); return; }
    if (this.index >= this.series.length - 1) this.index = 0;
    this.playing = true;
    this.timer = setInterval(() => {
      if (this.index >= this.series.length - 1) { this.stop(); return; }
      this.index++;
    }, 400);
  }

  stop(): void {
    this.playing = false;
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
  }
}
