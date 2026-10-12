import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { PlanePoint } from '../../../core/models';

interface Drawn {
  p: PlanePoint;
  tx?: number;
  ty?: number;
  cx: number;
  cy: number;
  rx: number;
  ry: number;
  hue: number;
}

/**
 * 2-D political plane: one dot per point, one axis-aligned oval per point.
 * Oval radii = sd × multiplier per axis (A25). Origin = average outlet (A26). Conservative is UP.
 */
@Component({
  selector: 'app-bias-plane',
  templateUrl: './bias-plane.component.html',
  styleUrls: ['./bias-plane.component.css']
})
export class BiasPlaneComponent implements OnChanges {
  @Input() points: PlanePoint[] = [];
  @Input() multiplier = 1;
  @Input() clickable = false;
  @Input() showTruth = false;
  @Input() fixedRange: number | null = null;
  @Output() pointSelected = new EventEmitter<PlanePoint>();

  readonly size = 600;
  readonly pad = 48;
  drawn: Drawn[] = [];
  hovered: PlanePoint | null = null;
  range = 1;

  ngOnChanges(): void {
    const k = this.multiplier;
    const extent = Math.max(
      0.5,
      ...this.points.map((p) => Math.max(Math.abs(p.x) + k * p.sd_x, Math.abs(p.y) + k * p.sd_y))
    );
    this.range = this.fixedRange ?? extent * 1.1;
    const scale = (this.size - 2 * this.pad) / (2 * this.range);
    this.drawn = this.points.map((p, i) => ({
      p,
      cx: this.pad + (p.x + this.range) * scale,
      cy: this.pad + (this.range - p.y) * scale,
      rx: Math.max(2, k * p.sd_x * scale),
      ry: Math.max(2, k * p.sd_y * scale),
      tx: (p as any).tx !== undefined ? this.pad + ((p as any).tx + this.range) * scale : undefined,
      ty: (p as any).ty !== undefined ? this.pad + (this.range - (p as any).ty) * scale : undefined,
      hue: Math.round((360 * i) / Math.max(1, this.points.length))
    }));
  }

  get center(): number {
    return this.size / 2;
  }

  select(d: Drawn): void {
    if (this.clickable) {
      this.pointSelected.emit(d.p);
    }
  }

  fmt(v: number): string {
    return (v >= 0 ? '+' : '') + v.toFixed(2);
  }
}
