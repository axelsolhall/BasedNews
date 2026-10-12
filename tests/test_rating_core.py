import numpy as np
import pytest

from pipeline.plan.planner import Candidate, PlannerConfig, plan, target_sds
from pipeline.rate.model import BradleyTerryLaplace, comparison_vector, group_sd, outlet_sds, sigmoid
from pipeline.sim.world import World, WorldConfig


def simulate(theta, beta, n_obs, rng):
    outlets = list(theta)
    pairs, ys = [], []
    for _ in range(n_obs):
        a, b = rng.choice(outlets, size=2, replace=False)
        pairs.append((a, b))
        ys.append(rng.random() < sigmoid(theta[a] - theta[b] + beta))
    return pairs, ys


def test_recovers_positions_and_bias_with_lots_of_data():
    rng = np.random.default_rng(0)
    theta = {f"o{k}": v for k, v in enumerate(rng.normal(0, 1, 8))}
    pairs, ys = simulate(theta, 0.4, 20000, rng)
    post = BradleyTerryLaplace(list(theta)).fit(pairs, ys)
    est = post.mean[:-1]
    truth = np.array(list(theta.values()))
    # positions are identified only up to the prior's centring; compare centred values
    assert np.allclose(est - est.mean(), truth - truth.mean(), atol=0.1)
    assert abs(post.mean[-1] - 0.4) < 0.08


def test_sd_shrinks_with_more_data():
    rng = np.random.default_rng(1)
    theta = {f"o{k}": v for k, v in enumerate(rng.normal(0, 1, 6))}
    m = BradleyTerryLaplace(list(theta))
    small = outlet_sds(m.fit(*simulate(theta, 0.0, 100, rng)))
    big = outlet_sds(m.fit(*simulate(theta, 0.0, 2000, rng)))
    assert (big < small).all()


def test_planner_rank1_update_matches_refit_reasonably():
    rng = np.random.default_rng(2)
    theta = {f"o{k}": v for k, v in enumerate(rng.normal(0, 1, 5))}
    m = BradleyTerryLaplace(list(theta))
    pairs, ys = simulate(theta, 0.0, 200, rng)
    post = m.fit(pairs, ys)
    # predicted variance drop for one comparison vs actual after refitting with that comparison added
    x = comparison_vector(0, 1, 5)
    p = sigmoid(x @ post.mean); w = p * (1 - p)
    Sx = post.cov @ x
    pred = post.cov - w * np.outer(Sx, Sx) / (1 + w * x @ Sx)
    after = m.fit(pairs + [("o0", "o1")], ys + [True])
    assert np.allclose(np.diag(pred)[:5], np.diag(after.cov)[:5], atol=5e-3)


def test_planner_stops_when_target_reached():
    post = BradleyTerryLaplace(["a", "b"]).fit([], [])
    cands = [Candidate("e1", "a", "b")]
    assert plan(post, cands, PlannerConfig(target_sd=10.0)) == []        # prior sd 1 < 10 -> already done
    assert len(plan(post, cands, PlannerConfig(target_sd=0.01, batch_size=3))) == 1  # one candidate only


def test_planner_respects_budget_and_randomises_order():
    post = BradleyTerryLaplace(["a", "b", "c"]).fit([], [])
    cands = [Candidate(f"e{k}", "a", "b") for k in range(50)]
    reqs = plan(post, cands, PlannerConfig(target_sd=0.0, batch_size=50), budget_left=20, rng=np.random.default_rng(3))
    assert len(reqs) == 20
    firsts = {r.first_outlet for r in reqs}
    assert firsts == {"a", "b"}                                          # A04: both orders occur


def test_group_sd_uses_covariance():
    world = World.make(WorldConfig(seed=4, n_countries=2, outlets_per_country=3))
    post = BradleyTerryLaplace(world.outlets).fit([], [])
    g = world.country_weights()
    sds = target_sds(post, g, include_outlets=False)
    # with an independent unit prior, sd of a mean of 3 outlets = 1/sqrt(3)
    assert sds["C0"] == pytest.approx(1 / np.sqrt(3), rel=1e-6)
    assert group_sd(post, world.countries["C0"]) == pytest.approx(1 / np.sqrt(3), rel=1e-6)
