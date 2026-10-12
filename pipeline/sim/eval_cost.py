"""Experiment E2 — information per token: Elo baseline vs Bayesian BT, random vs active vs cost-aware selection.

    uv run python -m pipeline.sim.eval_cost            (writes reports/e2_*.md|png|json)
    uv run python -m pipeline.sim.eval_cost --quick

World: 8 countries × 5 outlets; each outlet has a full-text availability rate (like real M2 data: some ~0 %, most high).
Every candidate pair can be judged in two modes (A21: like-with-like):
  full    — both sides full text: ~2,700 tokens, discrimination a = 1.0      (only if both sides have text)
  summary — headline + summary only: ~540 tokens, discrimination a = A_SUMMARY (default 0.6, A30)
Judge: P(first wins) = sigmoid(a · (theta_i − theta_j + article noise) + beta).
Contenders at an equal TOKEN budget:
  elo_random      — Elo (K-factor), random pairs and modes          (industry baseline; no uncertainty)
  bt_random       — Bayesian Bradley–Terry, random pairs and modes
  bt_variance     — BT + variance-targeted selection, cost-blind     (P1 planner)
  bt_cost_aware   — BT + variance reduction per token                (this project)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.stats import norm, spearmanr

from pipeline.plan.planner import Candidate, PlannerConfig, plan, target_sds
from pipeline.rate.model import BradleyTerryLaplace, outlet_sds
from pipeline.sim.world import World, WorldConfig

PROMPT_TOKENS = 300
FULL_TOKENS, SUMMARY_TOKENS = 1200, 120          # per article side
COST_FULL = PROMPT_TOKENS + 2 * FULL_TOKENS      # 2,700
COST_SUMMARY = PROMPT_TOKENS + 2 * SUMMARY_TOKENS  # 540
Z90 = norm.ppf(0.95)
MODES = ["elo_random", "bt_random", "bt_variance", "bt_cost_aware"]


class CostWorld(World):
    """World + per-outlet full-text availability and mode-dependent judge sharpness."""
    a_summary: float = 0.6

    def text_rates(self, rng):
        # mirrors M2: a few outlets ~0 % text (paywall/TDM), most 60–100 %
        return {o: (0.0 if rng.random() < 0.2 else rng.uniform(0.6, 1.0)) for o in self.outlets}

    def expand(self, cands, rates, rng):
        out = []
        for c in cands:
            has_a, has_b = rng.random() < rates[c.outlet_a], rng.random() < rates[c.outlet_b]
            out.append(Candidate(c.event_id, c.outlet_a, c.outlet_b, c.article_a, c.article_b,
                                 cost=COST_SUMMARY, discrimination=self.a_summary))
            if has_a and has_b:
                out.append(Candidate(c.event_id + ":full", c.outlet_a, c.outlet_b, c.article_a, c.article_b,
                                     cost=COST_FULL, discrimination=1.0))
        return out

    def judge_a(self, first, second, a):
        x = self.theta[first] + self.rng.normal(0, self.cfg.article_sd)
        y = self.theta[second] + self.rng.normal(0, self.cfg.article_sd)
        return bool(self.rng.random() < 1 / (1 + np.exp(-(a * (x - y) + self.cfg.judge_bias))))


def run(mode, seed, budget_tokens, a_summary, batch=20):
    w = CostWorld.make(WorldConfig(seed=seed)); w.__class__ = CostWorld; w.a_summary = a_summary
    rng = np.random.default_rng(seed + 7)
    rates = w.text_rates(rng)
    truth = np.array([w.theta[o] for o in w.outlets])
    groups = w.country_weights()
    model = BradleyTerryLaplace(w.outlets)
    elo = {o: 1500.0 for o in w.outlets}
    pairs, ys, discs, pool, curve, spent, n_full = [], [], [], [], [], 0, 0
    post = model.fit([], [])
    while spent < budget_tokens:
        for _ in range(3):
            pool.append(w.expand(w.new_event(), rates, rng))
        pool = pool[-15:]
        cands = [c for ev in pool for c in ev]
        if mode in ("elo_random", "bt_random"):
            idx = rng.choice(len(cands), size=min(batch, len(cands)), replace=False)
            chosen = []
            for i in idx:
                c = cands[i]
                a, b = (c.outlet_b, c.outlet_a) if rng.random() < 0.5 else (c.outlet_a, c.outlet_b)
                chosen.append((a, b, c.discrimination, c.cost))
        else:
            cfg = PlannerConfig(mode="variance" if mode == "bt_variance" else "variance_per_cost",
                                target_sd=0.0, batch_size=batch)
            reqs = plan(post, cands, cfg, groups=groups, rng=rng)
            look = {(c.event_id, c.outlet_a, c.outlet_b): c for c in cands}
            look.update({(c.event_id, c.outlet_b, c.outlet_a): c for c in cands})
            chosen = [(r.first_outlet, r.second_outlet, look[(r.event_id, r.first_outlet, r.second_outlet)].discrimination,
                       look[(r.event_id, r.first_outlet, r.second_outlet)].cost) for r in reqs]
        bought = 0
        for a, b, disc, cost in chosen:
            if spent + cost > budget_tokens:
                continue                       # can't afford this one; a cheaper one later in the batch may fit
            bought += 1
            y = w.judge_a(a, b, disc)
            spent += cost; n_full += cost == COST_FULL
            pairs.append((a, b)); ys.append(y); discs.append(disc)
            if mode == "elo_random":            # standard Elo update, K = 16 (his prototype's best region)
                ea = 1 / (1 + 10 ** ((elo[b] - elo[a]) / 400))
                elo[a] += 16 * (y - ea); elo[b] -= 16 * (y - ea)
        if bought == 0 and spent + COST_SUMMARY > budget_tokens:
            break                              # budget exhausted: nothing affordable remains (bug fix: was an infinite loop)
        if mode == "elo_random":
            est = np.array([elo[o] for o in w.outlets])
            curve.append({"tokens": spent, "spearman": float(spearmanr(est, truth).statistic), "calls": len(pairs)})
            continue
        post = model.fit(pairs, ys, discs)
        sds = outlet_sds(post); mean = post.mean[: len(w.outlets)]
        csd = target_sds(post, groups, include_outlets=False)
        mc, tc = mean - mean.mean(), truth - truth.mean()            # A26: compare relative positions
        curve.append({"tokens": spent, "calls": len(pairs), "full_share": n_full / max(1, len(pairs)),
                      "max_sd": float(sds.max()), "mean_sd": float(sds.mean()), "max_country_sd": float(max(csd.values())),
                      "spearman": float(spearmanr(mean, truth).statistic),
                      "coverage90": float((np.abs(mc - tc) <= Z90 * sds).mean())})
    return curve


def at(curve, key, tokens):
    rows = [r for r in curve if r["tokens"] <= tokens]
    return rows[-1][key] if rows and key in rows[-1] else float("nan")


def tokens_to(curve, key, target):
    for r in curve:
        if key in r and r[key] <= target:
            return r["tokens"]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--budget", type=int, default=2_000_000, help="token budget")
    ap.add_argument("--a-summary", type=float, default=0.6)
    ap.add_argument("--out", default="reports")
    args = ap.parse_args()
    if args.quick:
        args.seeds, args.budget = 2, 400_000
    res = {m: [run(m, s, args.budget, args.a_summary) for s in range(args.seeds)] for m in MODES}
    out = Path(args.out); out.mkdir(exist_ok=True)
    tag = f"a{args.a_summary:g}"
    (out / f"e2_curves_{tag}.json").write_text(json.dumps(res))

    L = ["# E2 — information per token", "",
         f"Seeds {args.seeds} · token budget {args.budget:,} · summary discrimination a={args.a_summary} · "
         f"cost/call: full {COST_FULL:,} tok, summary {COST_SUMMARY:,} tok", "",
         "## At fixed spend (mean over seeds)", "",
         "| Mode | tokens | calls | full-text share | Spearman | max outlet sd | max country sd | 90 % CI coverage |",
         "|---|---|---|---|---|---|---|---|"]
    for m in MODES:
        for frac in (0.25, 1.0):
            t = int(args.budget * frac)
            f = lambda k: np.nanmean([at(c, k, t) for c in res[m]])
            L.append(f"| {m} | {t:,} | {f('calls'):.0f} | {f('full_share'):.0%} | {f('spearman'):.3f} | "
                     f"{f('max_sd'):.3f} | {f('max_country_sd'):.3f} | {f('coverage90'):.2f} |")
    L += ["", "## Tokens to reach target uncertainty (median; '>' = not reached)", "",
          "| Target | bt_random | bt_variance | bt_cost_aware | saving cost-aware vs random | vs variance |", "|---|---|---|---|---|---|"]
    for key, t in (("max_sd", 0.40), ("max_sd", 0.30), ("max_country_sd", 0.25), ("max_country_sd", 0.20)):
        med = {}
        for m in ("bt_random", "bt_variance", "bt_cost_aware"):
            v = [tokens_to(c, key, t) for c in res[m]]
            med[m] = int(np.median(v)) if all(x is not None for x in v) else None
        cell = lambda x: f"{x:,}" if x else f">{args.budget:,}"
        sav = lambda a, b: f"{100 * (1 - a / b):.0f} %" if a and b else "n/a"
        L.append(f"| {key} ≤ {t} | {cell(med['bt_random'])} | {cell(med['bt_variance'])} | {cell(med['bt_cost_aware'])} | "
                 f"{sav(med['bt_cost_aware'], med['bt_random'])} | {sav(med['bt_cost_aware'], med['bt_variance'])} |")
    rep = "\n".join(L)
    (out / f"e2_info_per_token_{tag}.md").write_text(rep + "\n")
    print(rep)

    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.2))
    # fixed colour per method so every panel matches (Elo has no sd → absent from the sd panels)
    colours = {"elo_random": "#1f77b4", "bt_random": "#ff7f0e", "bt_variance": "#2ca02c", "bt_cost_aware": "#d62728"}
    labels = {"elo_random": "Elo, random pairs", "bt_random": "Bradley–Terry, random", "bt_variance": "BT + active (cost-blind)",
              "bt_cost_aware": "BT + active per token"}
    for m in MODES:
        Lm = min(len(c) for c in res[m])
        xs = np.mean([[r["tokens"] / 1e6 for r in c[:Lm]] for c in res[m]], axis=0)
        for a_, key, ttl in zip(ax, ["spearman", "max_sd", "max_country_sd"],
                                ["Rank recovery (Spearman) ↑", "Max outlet sd ↓", "Max country sd ↓"]):
            if key in res[m][0][0]:
                a_.plot(xs, np.mean([[r[key] for r in c[:Lm]] for c in res[m]], axis=0), label=labels[m], color=colours[m], lw=1.8)
            a_.set_title(ttl); a_.set_xlabel("tokens spent (millions)")
    for a_ in ax:
        a_.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(out / f"e2_info_per_token_{tag}.png", dpi=120)
    print(f"\nplot: {out}/e2_info_per_token_{tag}.png")


if __name__ == "__main__":
    main()
