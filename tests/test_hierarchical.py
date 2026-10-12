"""A49: hierarchical Bradley–Terry with article effects, and the planner's diminishing returns on repeat articles."""
import numpy as np

from pipeline.plan.planner import Candidate, PlannerConfig, plan_joint_hier
from pipeline.rate.hierarchical import HierarchicalBT, marginal_value_of_repeats
from pipeline.rate.model import BradleyTerryLaplace


def _sim(rng, n_out=6, n_art=12, sigma=0.0, n=3000):
    theta = rng.standard_normal(n_out)
    outs = [f"o{k}" for k in range(n_out)]
    arts = {f"o{k}_a{j}": (k, rng.normal(0, sigma) if sigma else 0.0) for k in range(n_out) for j in range(n_art)}
    names = list(arts)
    comps, wins = [], []
    for _ in range(n):
        a, b = rng.choice(names, 2, replace=False)
        if arts[a][0] == arts[b][0]:
            continue
        d = theta[arts[a][0]] + arts[a][1] - theta[arts[b][0]] - arts[b][1]
        comps.append((outs[arts[a][0]], outs[arts[b][0]], a, b, 1.0))
        wins.append(rng.random() < 1 / (1 + np.exp(-(d + 0.3))))
    return outs, theta, comps, wins


def test_recovers_outlets_and_beta():
    outs, theta, comps, wins = _sim(np.random.default_rng(0), n_out=10, sigma=0.5, n=4000)
    h = HierarchicalBT(outs).fit(comps, wins)
    est = h.mean[: len(outs)]
    assert np.corrcoef(est, theta)[0, 1] > 0.95
    assert abs(h.mean[len(outs)] - 0.3) < 0.15


def test_no_article_effect_picks_a_small_spread_and_matches_flat():
    outs, theta, comps, wins = _sim(np.random.default_rng(1), sigma=0.0)
    h = HierarchicalBT(outs).fit(comps, wins, tau=1.0)
    flat = BradleyTerryLaplace(outs).fit([(c[0], c[1]) for c in comps], wins)
    assert h.sigma_a < 0.4
    assert np.allclose(h.mean[: len(outs)], flat.mean[: len(outs)], atol=0.15)


def test_repeats_of_one_article_do_not_shrink_the_outlet_like_new_articles():
    outs = ["x", "y"]
    # order alternates (A04): with x always first, "x vs y" and the first-position bias are indistinguishable
    flip = lambda k, c: c if k % 2 else (c[1], c[0], c[3], c[2], c[4])
    rep = [flip(k, ("x", "y", "x1", f"y{k}", 1.0)) for k in range(30)]       # x seen through ONE article, 30 times
    new = [flip(k, ("x", "y", f"x{k}", f"y{k}", 1.0)) for k in range(30)]    # x seen through 30 articles
    w = [k % 3 == 0 for k in range(30)]
    def sd(comps):                  # the reported quantity is relative: x minus y (the shared level is prior-only)
        cov = HierarchicalBT(outs).fit(comps, w, sigma_a=0.8, tau=1.0).cov
        return float(np.sqrt(cov[0, 0] + cov[1, 1] - 2 * cov[0, 1]))
    assert sd(rep) > 1.5 * sd(new)


def test_diminishing_returns_curve():
    g = marginal_value_of_repeats(0.5, 1.0)
    assert all(a > b for a, b in zip(g, g[1:])) and g[9] / g[0] < 0.15


def test_planner_values_a_new_article_more_than_a_repeat():
    outs = ["x", "y"]
    comps = [("x", "y", "x1", f"y{k}", 1.0) for k in range(8)]
    wins = [True, False, True, True, False, True, False, True]
    h = {ax: HierarchicalBT(outs, ax).fit(comps, wins, sigma_a=0.8, tau=1.0) for ax in ("econ", "social")}
    cfg = PlannerConfig(mode="variance", batch_size=1, randomise_order=False)
    gain = lambda art: plan_joint_hier(h, [Candidate("e", "x", "y", art, "y_new")], cfg, rng=np.random.default_rng(0))[0].expected_info_gain
    assert gain("x2") > 2 * gain("x1")
