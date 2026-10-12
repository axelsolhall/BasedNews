"""E5 (simulation): does the flat model overstate certainty when one article is compared many times — and does the
hierarchical article model (A49) fix it?

    uv run python -m pipeline.sim.eval_articles [--seeds 40]

World built like the real data (E4c): outlets have a typical position θ_o ~ N(0,1); every article belongs to ONE story
and deviates from its outlet by ε ~ N(0, σ_true²); story sizes are heavy-tailed (most 2–3 outlets, a few 10+), so an
article in a big story appears in many pairs; the judge prefers the second-shown article (β = −0.9, measured in E4).
Comparisons are drawn at random from all within-story pairs. Both models see exactly the same verdicts.

Reported per model: coverage of the nominal 90 % interval for each outlet's position relative to the average outlet
(the plane's coordinates), mean reported sd, mean absolute error, rank correlation with truth, chosen σ_a.
Writes reports/e5_article_effects.md and .png. Also σ_true = 0 (no article effect): the hierarchical model must not hurt.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy.stats import norm, spearmanr  # noqa: E402

from pipeline.aggregate.plane import _contrast_stats  # noqa: E402
from pipeline.rate.hierarchical import HierarchicalBT, marginal_value_of_repeats  # noqa: E402
from pipeline.rate.model import BradleyTerryLaplace  # noqa: E402

Z90 = norm.ppf(0.95)


def world(rng, n_outlets=30, n_stories=160, sigma_true=0.5):
    theta = rng.standard_normal(n_outlets)
    outlets = [f"o{k}" for k in range(n_outlets)]
    sizes = np.minimum(n_outlets, 2 + rng.geometric(0.35, n_stories) - 1 + (rng.random(n_stories) < 0.08) * rng.integers(6, 12, n_stories))
    stories, eps = [], {}
    for s, size in enumerate(sizes):
        members = rng.choice(n_outlets, size=size, replace=False)
        arts = []
        for o in members:
            a = f"s{s}_o{o}"
            eps[a] = rng.normal(0, sigma_true) if sigma_true > 0 else 0.0
            arts.append((outlets[o], a, int(o)))
        stories.append(arts)
    pairs = [(x, y) for arts in stories for i, x in enumerate(arts) for y in arts[i + 1:]]
    return outlets, theta, eps, pairs


def simulate(rng, outlets, theta, eps, pairs, n_comp, beta=-0.9, a=1.0):
    pick = rng.choice(len(pairs), size=min(n_comp, len(pairs)), replace=False)
    comps, wins = [], []
    for k in pick:
        x, y = pairs[k]
        if rng.random() < 0.5:
            x, y = y, x                                   # random order (A04)
        d = theta[x[2]] + eps[x[1]] - theta[y[2]] - eps[y[1]]
        comps.append((x[0], y[0], x[1], y[1], a))
        wins.append(rng.random() < 1 / (1 + np.exp(-(a * d + beta))))
    return comps, wins


def score(post, outlets, theta):
    t = theta - theta.mean()
    cov, err, sds, est = [], [], [], []
    for k, o in enumerate(outlets):
        m, s = _contrast_stats(post, {o: 1.0})
        cov.append(abs(m - t[k]) <= Z90 * s); err.append(abs(m - t[k])); sds.append(s); est.append(m)
    return {"coverage": float(np.mean(cov)), "sd": float(np.mean(sds)), "mae": float(np.mean(err)),
            "spearman": float(spearmanr(est, t).statistic)}


def reuse_stats(comps):
    use = defaultdict(int)
    for c in comps:
        use[c[2]] += 1; use[c[3]] += 1
    v = np.array(list(use.values()))
    return float(np.mean(v)), int(v.max())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=40)
    ap.add_argument("--budgets", type=int, nargs="*", default=[400, 1500])
    args = ap.parse_args()
    rows = []
    for sigma_true in (0.5, 0.0):
        for budget in args.budgets:
            agg = defaultdict(list)
            for seed in range(args.seeds):
                rng = np.random.default_rng(seed)
                outlets, theta, eps, pairs = world(rng, sigma_true=sigma_true)
                comps, wins = simulate(rng, outlets, theta, eps, pairs, budget)
                flat = BradleyTerryLaplace(outlets).fit([(c[0], c[1]) for c in comps], wins, [c[4] for c in comps])
                hier = HierarchicalBT(outlets).fit(comps, wins)
                for name, post in (("flat", flat), ("hierarchical", hier.outlet_posterior())):
                    for k, v in score(post, outlets, theta).items():
                        agg[(name, k)].append(v)
                agg[("hierarchical", "sigma_a")].append(hier.sigma_a)
                mu, mx = reuse_stats(comps)
                agg[("data", "reuse_mean")].append(mu); agg[("data", "reuse_max")].append(mx)
            rows.append((sigma_true, budget, {k: float(np.mean(v)) for k, v in agg.items()}))
            print(f"σ_true {sigma_true} · {budget} comparisons · flat coverage {np.mean(agg[('flat', 'coverage')]):.2f} · "
                  f"hierarchical {np.mean(agg[('hierarchical', 'coverage')]):.2f} · σ_a chosen {np.mean(agg[('hierarchical', 'sigma_a')]):.2f}")

    L = ["# E5 — one article compared many times: flat vs hierarchical model (simulation)", "",
         f"{args.seeds} simulated worlds per row, 30 outlets, 160 stories with heavy-tailed size, articles deviate from their "
         "outlet by σ_true, judge order bias β = −0.9, comparisons drawn at random from within-story pairs. Nominal "
         "coverage is 0.90: an honest model's 90 % intervals contain the truth 90 % of the time.", "",
         "| σ_true | comparisons | uses per article (mean / max) | model | 90 % coverage | mean sd | mean abs error | Spearman | σ_a chosen |",
         "|---|---:|---|---|---:|---:|---:|---:|---:|"]
    for sigma_true, budget, r in rows:
        for name in ("flat", "hierarchical"):
            sa = f"{r[('hierarchical', 'sigma_a')]:.2f}" if name == "hierarchical" else "–"
            L.append(f"| {sigma_true} | {budget} | {r[('data', 'reuse_mean')]:.1f} / {r[('data', 'reuse_max')]:.0f} | {name} | "
                     f"**{r[(name, 'coverage')]:.2f}** | {r[(name, 'sd')]:.2f} | {r[(name, 'mae')]:.2f} | {r[(name, 'spearman')]:.2f} | {sa} |")
    gains = marginal_value_of_repeats(0.5, 1.0)
    L += ["", "Diminishing returns (Gaussian approximation, σ_a = 0.5, one comparison ≈ ±1.0): value for the outlet of the "
          "n-th comparison of the same article, relative to the first look at a new article:", "",
          "| n | " + " | ".join(str(k) for k in range(1, 11)) + " |", "|---|" + "---:|" * 10,
          "| value | " + " | ".join(f"{g / gains[0]:.2f}" for g in gains) + " |", "", "![E5](e5_article_effects.png)", ""]
    Path("reports/e5_article_effects.md").write_text("\n".join(L))

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.2))
    labels = [f"σ={s}\n{b} comps" for s, b, _ in rows]
    xs = np.arange(len(rows))
    for off, name, col in ((-0.18, "flat", "#9a9a9a"), (0.18, "hierarchical", "#1b7f79")):
        a1.bar(xs + off, [r[(name, "coverage")] for *_, r in rows], width=0.36, color=col, label=name)
    a1.axhline(0.9, color="#c0392b", ls="--", lw=1); a1.text(len(rows) - 0.5, 0.91, "nominal 0.90", color="#c0392b", fontsize=8, ha="right")
    a1.set_xticks(xs); a1.set_xticklabels(labels, fontsize=8); a1.set_ylim(0, 1); a1.legend(fontsize=8, frameon=False)
    a1.set_title("How often the 90 % interval contains the truth", loc="left", fontsize=10)
    a2.bar(range(1, 11), [g / gains[0] for g in gains], color="#1b7f79")
    a2.set_xlabel("n-th comparison of the same article"); a2.set_ylabel("value for the outlet (1 = first look)")
    a2.set_title("Repeat comparisons: diminishing returns (σ_a 0.5)", loc="left", fontsize=10)
    for a in (a1, a2):
        a.spines[["top", "right"]].set_visible(False)
    fig.suptitle("E5 · article effects (simulation)", x=0.01, ha="left", fontweight="bold")
    fig.tight_layout(); fig.savefig("reports/e5_article_effects.png", dpi=150, facecolor="white"); plt.close(fig)
    print("\n".join(L))


if __name__ == "__main__":
    main()
