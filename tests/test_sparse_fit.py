"""A66: the sparse fit must give the dense fit's numbers — mode, covariance blocks, evidence, the spreads' averaging —
and the planner must pick the same pairs from either; and it must fit sizes the dense one cannot hold."""
import time

import numpy as np
import pytest

from pipeline.plan.planner import Candidate, PlannerConfig, plan_joint_hier
from pipeline.rate.hierarchical import HierarchicalBT
from pipeline.rate.model import sigmoid
from pipeline.rate.sparse import SparseCov


def data(seed=0, n_outlets=8, per_outlet=18, n_comps=500, stories=False):
    """Simulated verdicts. stories=False: random article pairs across the whole set (the hardest structure for the
    sparse algebra); stories=True: like real verdicts, pairs within a story of 3–6 articles from different outlets."""
    rng = np.random.default_rng(seed)
    outlets = [f"o{k}" for k in range(n_outlets)]
    theta = dict(zip(outlets, rng.normal(size=n_outlets)))
    comps, wins = [], []

    def add(a, oa, ea, b, ob, eb):
        d = 1.0 if rng.random() < 0.6 else 0.6
        comps.append((oa, ob, a, b, d))
        wins.append(bool(rng.random() < sigmoid(d * (theta[oa] + ea - theta[ob] - eb) + 0.3)))

    if stories:
        s = 0
        while len(comps) < n_comps:
            members = [(f"s{s}-{o}", o, rng.normal(scale=0.5))
                       for o in rng.choice(outlets, size=rng.integers(3, 7), replace=False)]
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    if rng.random() < 0.5:
                        add(*members[i], *members[j])
            s += 1
        return outlets, comps, wins
    arts = [(f"{o}-a{k}", o, rng.normal(scale=0.5)) for o in outlets for k in range(per_outlet)]
    while len(comps) < n_comps:
        i, j = rng.integers(0, len(arts), 2)
        if arts[i][1] != arts[j][1]:
            add(*arts[i], *arts[j])
    return outlets, comps, wins


@pytest.fixture(scope="module")
def both():
    outlets, comps, wins = data()
    dense = HierarchicalBT(outlets, "econ").fit(comps, wins, sigma_a=0.5, tau=1.0, sparse=False)
    sp = HierarchicalBT(outlets, "econ").fit(comps, wins, sigma_a=0.5, tau=1.0, sparse=True)
    return outlets, comps, wins, dense, sp


def test_same_mode_and_evidence(both):
    *_, dense, sp = both
    assert dense.articles == sp.articles
    assert np.allclose(dense.mean, sp.mean, atol=1e-8)
    assert dense.evidence[(1.0, 0.5)] == pytest.approx(sp.evidence[(1.0, 0.5)], abs=1e-6)


def test_same_covariance_blocks_by_every_kind_of_index(both):
    outlets, _, _, dense, sp = both
    n = len(outlets)
    assert isinstance(sp.cov, SparseCov)
    assert np.allclose(dense.cov[:n + 1, :n + 1], sp.cov[:n + 1, :n + 1], atol=1e-9)              # outlets + β
    idx = np.array([0, 3, n, n + 1, n + 7, n + 40, n + 100])
    assert np.allclose(dense.cov[np.ix_(idx, idx)], sp.cov[np.ix_(idx, idx)], atol=1e-9)        # the planner's view
    assert dense.cov[2, n + 5] == pytest.approx(sp.cov[2, n + 5], abs=1e-9)                       # one entry
    assert np.allclose(dense.cov[idx, idx], sp.cov[idx, idx], atol=1e-9)                          # paired indices
    op_d, op_s = dense.outlet_posterior(), sp.outlet_posterior()
    assert np.allclose(op_d.mean, op_s.mean) and np.allclose(op_d.cov, op_s.cov, atol=1e-9)
    with pytest.raises(TypeError):
        np.asarray(sp.cov)                                    # never materialised


def test_same_averaging_over_the_spreads_grid():
    outlets, comps, wins = data(seed=1, n_comps=300)
    dense = HierarchicalBT(outlets, "social").fit(comps, wins, sparse=False)
    sp = HierarchicalBT(outlets, "social").fit(comps, wins, sparse=True)
    n = len(outlets)
    assert set(dense.evidence) == set(sp.evidence)
    assert all(dense.evidence[k] == pytest.approx(sp.evidence[k], abs=1e-5) for k in dense.evidence)
    assert np.allclose(dense.mean, sp.mean, atol=1e-6)
    assert dense.sigma_a == pytest.approx(sp.sigma_a) and dense.tau == pytest.approx(sp.tau)
    idx = np.array([0, 1, n, n + 3, n + 50])
    assert np.allclose(dense.cov[np.ix_(idx, idx)], sp.cov[np.ix_(idx, idx)], atol=1e-7)


def test_the_planner_picks_the_same_pairs_from_either(both):
    outlets, comps, wins, dense, sp = both
    rng = np.random.default_rng(5)
    arts = sorted({c[2] for c in comps} | {c[3] for c in comps})
    owner = {a: a.split("-")[0] for a in arts}
    pool = []
    for k in range(60):                                       # half judged articles, half new ones
        a = arts[rng.integers(len(arts))] if k % 2 else f"{outlets[k % 8]}-new{k}"
        b = arts[rng.integers(len(arts))]
        oa, ob = owner.get(a, a.split("-")[0]), owner[b]
        if oa != ob:
            pool.append(Candidate(f"ev{k}:full", oa, ob, a, b, cost=1e-4, discrimination=1.0, relevance=(1.0, 1.0)))
    cfg = PlannerConfig(mode="variance_per_cost", target_sd=0.0, batch_size=8)
    pick_d = plan_joint_hier({"econ": dense}, pool, cfg, rng=np.random.default_rng(0))
    pick_s = plan_joint_hier({"econ": sp}, pool, cfg, rng=np.random.default_rng(0))
    assert [(r.event_id, r.first_article_id, r.second_article_id) for r in pick_d] == \
           [(r.event_id, r.first_article_id, r.second_article_id) for r in pick_s]


def test_story_shaped_verdicts_give_the_dense_numbers_too():
    outlets, comps, wins = data(seed=3, n_outlets=12, n_comps=600, stories=True)
    dense = HierarchicalBT(outlets, "econ").fit(comps, wins, sigma_a=0.7, tau=0.5, sparse=False)
    sp = HierarchicalBT(outlets, "econ").fit(comps, wins, sigma_a=0.7, tau=0.5, sparse=True)
    n = len(outlets)
    assert np.allclose(dense.mean, sp.mean, atol=1e-8)
    idx = np.array([0, 5, n, n + 2, n + 77, len(dense.mean) - 1])
    assert np.allclose(dense.cov[np.ix_(idx, idx)], sp.cov[np.ix_(idx, idx)], atol=1e-9)


def test_it_fits_a_size_the_dense_fit_cannot_hold():
    """≈ 60 k articles in stories over 200 outlets: the dense fit would need a 60 k² matrix (≈ 29 GB) per Newton step."""
    outlets, comps, wins = data(seed=2, n_outlets=200, n_comps=60_000, stories=True)
    t = time.monotonic()
    h = HierarchicalBT(outlets, "econ").fit(comps, wins, sigma_a=0.5, tau=1.0)          # auto: sparse
    assert isinstance(h.cov, SparseCov) and len(h.articles) > 40_000
    assert np.all(np.isfinite(h.mean)) and np.all(np.diag(h.outlet_posterior().cov) > 0)
    assert time.monotonic() - t < 60
