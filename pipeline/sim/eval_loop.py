"""Eval for P1 (M6 planner + M8 rating) on the synthetic world.

Run:  uv run python -m pipeline.sim.eval_loop            (full run, writes reports/)
      uv run python -m pipeline.sim.eval_loop --quick    (fast smoke run)

Reports, per selection mode and as a function of LLM calls:
  - mean / max outlet posterior sd, max country sd           (the "get the sd down" metric)
  - Spearman rank correlation of fitted vs true positions    (recovery)
  - 90 % credible-interval coverage                          (calibration, A05)
  - calls needed to reach the target sd                      (the headline USP number)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.stats import norm, spearmanr

from pipeline.plan.planner import PlannerConfig, plan, target_sds
from pipeline.rate.model import BradleyTerryLaplace, outlet_sds
from pipeline.sim.world import World, WorldConfig

MODES = ["random", "eig", "variance"]
Z90 = norm.ppf(0.95)


def run_once(mode: str, seed: int, budget: int, batch: int, target_sd: float, events_per_round: int, pool_events: int):
    world = World.make(WorldConfig(seed=seed))
    model = BradleyTerryLaplace(world.outlets)
    rng = np.random.default_rng(seed + 1000)
    groups = world.country_weights()
    truth = np.array([world.theta[o] for o in world.outlets])

    pairs, outcomes, pool, curve = [], [], [], []
    post = model.fit([], [])
    cfg = PlannerConfig(mode=mode, target_sd=target_sd, batch_size=batch)
    while len(pairs) < budget:
        for _ in range(events_per_round):
            pool.append(world.new_event())
        pool = pool[-pool_events:]                       # only recent events are still "live"
        candidates = [c for ev in pool for c in ev]
        # in "variance" mode, also target countries so the world map converges (A08)
        reqs = plan(post, candidates, cfg, groups=groups if mode == "variance" else None,
                    budget_left=budget - len(pairs), rng=rng)
        if not reqs:
            # stop rule hit (or no candidates); in eval keep going with random picks so curves are comparable
            reqs = plan(post, candidates, PlannerConfig(mode="random", target_sd=-1, batch_size=batch),
                        budget_left=budget - len(pairs), rng=rng)
        for r in reqs:
            pairs.append((r.first_outlet, r.second_outlet))
            outcomes.append(world.judge(r.first_outlet, r.second_outlet))
        post = model.fit(pairs, outcomes)

        sds = outlet_sds(post)
        mean = post.mean[: len(world.outlets)]
        csd = target_sds(post, groups, include_outlets=False)
        covered = np.abs(mean - truth) <= Z90 * sds
        curve.append({
            "calls": len(pairs),
            "mean_sd": float(sds.mean()),
            "max_sd": float(sds.max()),
            "max_country_sd": float(max(csd.values())),
            "spearman": float(spearmanr(mean, truth).statistic),
            "coverage90": float(covered.mean()),
            "beta_hat": float(post.mean[-1]),
        })
    return curve


def first_reach(curve, key, target):
    for row in curve:
        if row[key] <= target:
            return row["calls"]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--budget", type=int, default=3000)
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--target-sd", type=float, default=0.15)
    ap.add_argument("--out", default="reports")
    args = ap.parse_args()
    if args.quick:
        args.seeds, args.budget = 2, 600

    results = {m: [run_once(m, s, args.budget, args.batch, args.target_sd, 3, 15) for s in range(args.seeds)] for m in MODES}

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    (out / "p1_curves.json").write_text(json.dumps(results))

    # ---- summary table ------------------------------------------------------------
    targets = {"max_sd": [0.40, 0.30], "max_country_sd": [0.25, 0.15]}
    lines = ["# P1 — rating core on the simulator", "",
             f"Seeds: {args.seeds} · budget: {args.budget} LLM calls · batch: {args.batch} · "
             f"world: 8 countries × 5 outlets, judge bias 0.3, article noise 0.3", "",
             "## Calls needed to reach target uncertainty (median over seeds; '>' = not reached)", "",
             "| Metric | Target | " + " | ".join(MODES) + " | saving vs random |",
             "|---|---|" + "---|" * len(MODES) + "---|"]
    for key, tlist in targets.items():
        for t in tlist:
            meds = {}
            for m in MODES:
                vals = [first_reach(c, key, t) for c in results[m]]
                reached = [v for v in vals if v is not None]
                meds[m] = (int(np.median(reached)) if len(reached) == len(vals) else None)
            cells = [str(meds[m]) if meds[m] else f">{args.budget}" for m in MODES]
            best = meds["variance"]
            saving = (f"{100 * (1 - best / meds['random']):.0f} %" if best and meds["random"] else "n/a")
            lines.append(f"| {key} | {t} | " + " | ".join(cells) + f" | {saving} |")

    lines += ["", "## End of budget (mean over seeds)", "",
              "| Mode | mean outlet sd | max outlet sd | max country sd | Spearman | 90 % CI coverage | beta_hat (true 0.3) |",
              "|---|---|---|---|---|---|---|"]
    for m in MODES:
        last = [c[-1] for c in results[m]]
        f = lambda k: np.mean([r[k] for r in last])
        lines.append(f"| {m} | {f('mean_sd'):.3f} | {f('max_sd'):.3f} | {f('max_country_sd'):.3f} | "
                     f"{f('spearman'):.3f} | {f('coverage90'):.2f} | {f('beta_hat'):.2f} |")
    report = "\n".join(lines)
    (out / "p1_rating_core.md").write_text(report + "\n")
    print(report)

    # ---- plot -----------------------------------------------------------------------
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
        for m in MODES:
            calls = [r["calls"] for r in results[m][0]]
            for ax, key, title in zip(axes, ["max_sd", "max_country_sd", "spearman"],
                                      ["Max outlet sd ↓", "Max country sd ↓", "Rank recovery (Spearman) ↑"]):
                L = min(len(c) for c in results[m])
                ys = np.mean([[r[key] for r in c[:L]] for c in results[m]], axis=0)
                ax.plot(calls[:L], ys, label=m)
                ax.set_title(title); ax.set_xlabel("LLM calls")
        axes[0].legend()
        fig.tight_layout()
        fig.savefig(out / "p1_active_vs_random.png", dpi=120)
        print(f"\nplot: {out / 'p1_active_vs_random.png'}")
    except Exception as e:  # plotting must never break the eval
        print("plot skipped:", e)


if __name__ == "__main__":
    main()
