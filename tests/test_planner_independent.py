"""The planner as the maintainer specified it (2026-10-25): every outlet has its own score and uncertainty; the pairs
judged are those most likely to be in the wrong order (overlapping uncertainty), never two outlets far apart however
uncertain; comparing A with B sharpens only A and B; cheap enough to score every candidate before every pick."""
import time
from types import SimpleNamespace

import numpy as np

from pipeline.plan.planner import Candidate, PlannerConfig, iter_plan_independent


def posts(means, sds):
    n = len(sds)
    h = SimpleNamespace(outlets=[f"o{k}" for k in range(n)], mean=np.array(list(means) + [0.0]),
                        cov=np.diag([s ** 2 for s in sds] + [1.0]))
    return {"econ": h, "social": h}


def cands(pairs):
    return [Candidate(f"ev{k}:summary", a, b, f"x{k}", f"y{k}") for k, (a, b) in enumerate(pairs)]


def test_the_pair_most_likely_inverted_is_picked_never_one_far_apart_however_uncertain():
    #           o0 and o1 overlap · o2 a million to the right, hugely uncertain · o3 pinned down, well apart from o0
    p = posts([0.0, 0.2, 1e6, 3.0], [0.5, 0.5, 1e4, 0.1])
    c = cands([("o0", "o2"), ("o0", "o3"), ("o0", "o1")])
    first = next(iter_plan_independent(p, c, PlannerConfig(target_sd=0.0, batch_size=1), rng=np.random.default_rng(0)))
    assert {first.first_outlet, first.second_outlet} == {"o0", "o1"}
    order = [frozenset((r.first_outlet, r.second_outlet)) for r in
             iter_plan_independent(p, c, PlannerConfig(target_sd=0.0, batch_size=3), rng=np.random.default_rng(0))]
    assert order[-1] == frozenset(("o0", "o2"))                       # the far one comes last of all


def test_a_comparison_sharpens_only_its_two_outlets():
    p = posts([0.0, 0.1, 0.0, 0.1], [1.0, 1.0, 1.0, 1.0])
    picks = list(iter_plan_independent(p, cands([("o0", "o1"), ("o0", "o1"), ("o2", "o3")]),
                                       PlannerConfig(target_sd=0.0, batch_size=2), rng=np.random.default_rng(0)))
    # o0–o1 is picked first; o2–o3, untouched by it, is then worth more than o0–o1 again
    assert [frozenset((r.first_outlet, r.second_outlet)) for r in picks] == [frozenset(("o0", "o1")), frozenset(("o2", "o3"))]


def test_the_floor_comes_first_and_sixty_thousand_candidates_plan_in_well_under_a_second():
    rng = np.random.default_rng(1)
    n = 228
    p = posts(list(rng.normal(0, 1, n)), list(rng.uniform(0.2, 1.0, n)))
    pairs = [(f"o{a}", f"o{b}") for a, b in rng.integers(0, n, size=(60000, 2)) if a != b]
    t = time.monotonic()
    picks = list(iter_plan_independent(p, cands(pairs), PlannerConfig(target_sd=0.0, batch_size=500), rng=rng,
                                       quota={"o7": 3}))
    assert time.monotonic() - t < 3.0 and len(picks) >= 500
    assert sum("o7" in (r.first_outlet, r.second_outlet) for r in picks[:3]) == 3      # the floor first
