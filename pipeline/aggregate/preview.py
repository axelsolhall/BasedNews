"""M9 eval/preview on a simulated two-axis world (no LLM, no real data yet).

    uv run python -m pipeline.aggregate.preview [--calls 1500]
- Runs the active loop (M6 → simulated judge → M8) separately per axis.
- Renders reports/plane_world.png (country dots + ovals) and reports/plane_country.png (one country's outlets).
- Checks calibration of the ovals: share of TRUE positions inside the oval vs the theoretical 2-D coverage
  1 − exp(−k²/2) for multiplier k (k=1 → 39 %).
- Writes frontend/src/assets/plane-demo.json so the Angular component can be developed against realistic data.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from pipeline.aggregate.plane import OVAL_SD_MULTIPLIER, country_points, export_json, outlet_points
from pipeline.plan.planner import PlannerConfig, plan
from pipeline.rate.model import BradleyTerryLaplace
from pipeline.sim.world import World, WorldConfig


def run_axis(world: World, axis: str, calls: int, seed: int):
    model = BradleyTerryLaplace(world.outlets, axis=axis)
    rng = np.random.default_rng(seed)
    pairs, outcomes, pool = [], [], []
    post = model.fit([], [])
    while len(pairs) < calls:
        for _ in range(3):
            pool.append(world.new_event())
        pool = pool[-15:]
        reqs = plan(post, [c for ev in pool for c in ev], PlannerConfig(target_sd=0.0, batch_size=20),
                    groups=world.country_weights(), budget_left=calls - len(pairs), rng=rng)
        for r in reqs:
            pairs.append((r.first_outlet, r.second_outlet))
            outcomes.append(world.judge(r.first_outlet, r.second_outlet))
        post = model.fit(pairs, outcomes)
    return post


def inside(px, py, sx, sy, tx, ty, k):
    return ((tx - px) / (k * sx)) ** 2 + ((ty - py) / (k * sy)) ** 2 <= 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=1500, help="judge calls per axis")
    ap.add_argument("--seed", type=int, default=3)
    args = ap.parse_args()
    econ_w = World.make(WorldConfig(seed=args.seed))
    social_w = World.make(WorldConfig(seed=args.seed + 100))       # same outlets/countries, different truths
    econ = run_axis(econ_w, "econ", args.calls, args.seed)
    social = run_axis(social_w, "social", args.calls, args.seed + 1)

    labels = {o: o for o in econ_w.outlets}
    opts = outlet_points(econ, social, labels, econ_w.country_of)
    cpts = country_points(econ, social, econ_w.country_weights())

    # A26: compare in centred coordinates (truth minus the mean of all true outlet positions)
    mx = np.mean([econ_w.theta[o] for o in econ_w.outlets]); my = np.mean([social_w.theta[o] for o in econ_w.outlets])
    for w, m in ((econ_w, mx), (social_w, my)):
        w.theta = {o: v - m for o, v in w.theta.items()}
    k = OVAL_SD_MULTIPLIER
    t_out = [inside(p.x, p.y, p.sd_x, p.sd_y, econ_w.theta[p.id], social_w.theta[p.id], k) for p in opts]
    t_cty = [inside(p.x, p.y, p.sd_x, p.sd_y,
                    np.mean([econ_w.theta[o] for o in econ_w.countries[p.id]]),
                    np.mean([social_w.theta[o] for o in econ_w.countries[p.id]]), k) for p in cpts]
    expected = 1 - np.exp(-k ** 2 / 2)
    print(f"Oval multiplier k={k}: theoretical 2-D coverage {expected:.0%}")
    print(f"  outlets : truth inside oval {np.mean(t_out):.0%} ({sum(t_out)}/{len(t_out)})")
    print(f"  countries: truth inside oval {np.mean(t_cty):.0%} ({sum(t_cty)}/{len(t_cty)})  (small n — noisy)")

    export_json("frontend/src/assets/plane-demo.json", cpts, opts, {"source": "simulation", "calls_per_axis": args.calls})
    print("wrote frontend/src/assets/plane-demo.json")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Ellipse

    def draw(points, truth, fname, title):
        fig, ax = plt.subplots(figsize=(6.5, 6.5))
        for p in points:
            ax.add_patch(Ellipse((p.x, p.y), 2 * k * p.sd_x, 2 * k * p.sd_y, alpha=0.18))
            ax.plot(p.x, p.y, "o", ms=4)
            ax.annotate(p.label, (p.x, p.y), fontsize=7, xytext=(3, 3), textcoords="offset points")
            if truth:
                tx, ty = truth[p.id]
                ax.plot(tx, ty, "x", ms=4, color="black")
        ax.axhline(0, lw=0.5, color="grey"); ax.axvline(0, lw=0.5, color="grey")
        ax.set_xlabel("← left        economic        right →"); ax.set_ylabel("← liberal        social        conservative →")
        ax.set_title(title, fontsize=10); ax.set_aspect("equal", adjustable="datalim")
        fig.tight_layout(); fig.savefig(fname, dpi=120); plt.close(fig)

    Path("reports").mkdir(exist_ok=True)
    draw(cpts, {p.id: (np.mean([econ_w.theta[o] for o in econ_w.countries[p.id]]),
                       np.mean([social_w.theta[o] for o in econ_w.countries[p.id]])) for p in cpts},
         "reports/plane_world.png", f"World (simulated) — dots = estimate, ovals = ±{k:g} sd, × = truth")
    c0 = cpts[0].id
    draw([p for p in opts if p.parent == c0], {p.id: (econ_w.theta[p.id], social_w.theta[p.id]) for p in opts},
         "reports/plane_country.png", f"Country {c0} (simulated) — outlets, ovals = ±{k:g} sd, × = truth")
    print("plots: reports/plane_world.png, reports/plane_country.png")


if __name__ == "__main__":
    main()
