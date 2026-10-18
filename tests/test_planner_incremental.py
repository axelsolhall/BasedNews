"""The joint planner keeps its per-pick quantities current with rank-1 updates (T = V S, each candidate's xᵀSx, S grown
in place) instead of recomputing them. It must pick exactly what the plain version — everything recomputed from S at
every pick — picks, with the same gains, in every mode: with and without a target sd, a cost budget, random order."""
import numpy as np
import pytest

from pipeline.contracts import ComparisonRequest
from pipeline.plan import planner as PL
from pipeline.plan.planner import Candidate, PlannerConfig, plan_joint_hier
from pipeline.rate.hierarchical import HierarchicalBT


def reference(
    hposts: dict,
    candidates: list[Candidate],
    cfg: PlannerConfig | None = None,
    groups: dict[str, dict[str, float]] | None = None,
    budget_left: float | None = None,
    rng: np.random.Generator | None = None,
) -> list[ComparisonRequest]:
    """The planner as first written: everything recomputed from S at every pick."""
    cfg = cfg or PlannerConfig()
    rng = rng or np.random.default_rng()
    if not candidates:
        return []
    axes = list(hposts)
    h0 = hposts[axes[0]]
    n = h0.n
    oidx = {o: k for k, o in enumerate(h0.outlets)}
    # active parameters: outlets, β, then judged articles that appear among the candidates
    in_pool = {a for c in candidates for a in (c.article_a, c.article_b)}
    judged = sorted({a for ax in axes for a in hposts[ax].articles if a in in_pool})   # union over axes
    act = {a: n + 1 + k for k, a in enumerate(judged)}
    sig2 = {ax: hposts[ax].sigma_a ** 2 for ax in axes}
    S, M = {}, {}
    for ax in axes:                     # an article judged on the other axis only enters this axis at its prior
        full = hposts[ax].article_index()
        src = list(range(n + 1)) + [full.get(a, -1) for a in judged]
        have = np.array([k >= 0 for k in src])
        srci = np.array([k if k >= 0 else 0 for k in src])
        d = len(src)
        S_ax = np.zeros((d, d)); M_ax = np.zeros(d)
        sub = hposts[ax].cov[np.ix_(srci, srci)]
        S_ax[np.ix_(have, have)] = sub[np.ix_(have, have)]
        S_ax[~have, ~have] = sig2[ax]
        M_ax[have] = hposts[ax].mean[srci[have]]
        S[ax], M[ax] = S_ax, M_ax

    flip = (rng.random(len(candidates)) < 0.5) if cfg.randomise_order else np.zeros(len(candidates), bool)
    oriented = [(c.outlet_b, c.outlet_a, c.article_b, c.article_a) if f else (c.outlet_a, c.outlet_b, c.article_a, c.article_b)
                for c, f in zip(candidates, flip)]
    disc = np.array([c.discrimination for c in candidates])
    cost = np.array([c.cost for c in candidates])
    R = {ax: np.array([(c.relevance[k] if len(c.relevance) > k else 1.0) for c in candidates]) for k, ax in enumerate(axes)}
    _, V0 = PL._target_vectors(PL._as_outlet_post(h0), groups, True)            # targets over outlets + β

    def arrays():
        """Per candidate: 5 parameter slots (first outlet, second outlet, β, first article, second article) → index in
        the active space (-1 = article not active yet) and coefficient."""
        idx = np.full((len(candidates), 5), -1)
        val = np.zeros((len(candidates), 5))
        for r, (a, b, fa, fb) in enumerate(oriented):
            idx[r, :3] = (oidx[a], oidx[b], n)
            val[r, :3] = (disc[r], -disc[r], disc[r])
            for slot, art, sign in ((3, fa, 1.0), (4, fb, -1.0)):
                if art in act:
                    idx[r, slot] = act[art]
                val[r, slot] = sign * disc[r]
        return idx, val

    idx, val = arrays()
    pair_key = [frozenset((c.article_a, c.article_b)) for c in candidates]
    # A09 stop: a target counts only while its sd is above target_sd and some candidate still touches it (an outlet
    # with no pair available today cannot be helped, so it never keeps the planner buying near-worthless pairs)
    c_out = np.array([(oidx[a], oidx[b]) for a, b, _, _ in oriented])
    members = V0[:, :n] > 0                                                 # target × outlet: whom it depends on
    chosen, available, spent, taken, gains = [], np.ones(len(candidates), bool), 0.0, set(), []
    while len(chosen) < cfg.batch_size:
        d = S[axes[0]].shape[0]
        V = np.zeros((V0.shape[0], d)); V[:, : n + 1] = V0
        valid = idx >= 0
        safe = np.where(valid, idx, 0)
        ok = available & np.array([k not in taken for k in pair_key])
        if cfg.target_sd > 0:
            reach = np.zeros(n, bool); reach[c_out[ok].ravel()] = True
            reachable = (members & reach[None, :]).any(axis=1)
        score = np.zeros(len(candidates))
        any_open = False
        for ax in axes:
            T = V @ S[ax]                                                   # targets × active
            if cfg.target_sd > 0:
                open_t = reachable & (np.sqrt(np.maximum((T * V).sum(axis=1), 0.0)) > cfg.target_sd)
                if not open_t.any():
                    continue
                T = T[open_t]
            any_open = True
            vSx = (T[:, safe] * (val * valid)[None, :, :]).sum(axis=2)     # targets × candidates
            Sg = S[ax][safe[:, :, None], safe[:, None, :]]                  # candidates × 5 × 5
            vv = (val * valid)
            xSx = np.einsum("ri,rij,rj->r", vv, Sg, vv) + sig2[ax] * ((~valid[:, 3:]) * val[:, 3:] ** 2).sum(axis=1)
            mean_x = (M[ax][safe] * vv).sum(axis=1)                         # unjudged articles have prior mean 0
            p = 1 / (1 + np.exp(-mean_x)); w = p * (1 - p)
            score += R[ax] * (w * (vSx ** 2).sum(axis=0)) / (1.0 + w * xSx)
        if not any_open:
            break                                                           # every reachable target certain enough
        if cfg.mode == "variance_per_cost":
            score = score / cost
        elif cfg.mode == "random":
            score = rng.random(len(candidates))
        if budget_left is not None:
            ok &= cost <= (budget_left - spent)
        if not ok.any():
            break
        best = int(np.argmax(np.where(ok, score, -np.inf)))
        chosen.append(best); available[best] = False; taken.add(pair_key[best]); spent += cost[best]
        gains.append(float(score[best]))                               # value at the moment it was picked
        # chosen articles become active parameters (prior variance σ_a², uncorrelated), then the fantasised update
        a, b, fa, fb = oriented[best]
        for art in (fa, fb):
            if art not in act:
                act[art] = S[axes[0]].shape[0]
                for ax in axes:
                    k = S[ax].shape[0]
                    S2 = np.zeros((k + 1, k + 1)); S2[:k, :k] = S[ax]; S2[k, k] = sig2[ax]
                    S[ax] = S2; M[ax] = np.append(M[ax], 0.0)
        idx, val = arrays()
        x = np.zeros(S[axes[0]].shape[0])
        for slot in range(5):
            x[idx[best, slot]] += val[best, slot]
        for ax in axes:
            s = S[ax] @ x
            p = 1 / (1 + np.exp(-(M[ax] @ x))); w = p * (1 - p)
            S[ax] = S[ax] - R[ax][best] * w * np.outer(s, s) / (1.0 + w * (x @ s))
    out = []
    for k, g in zip(chosen, gains):
        a, b, fa, fb = oriented[k]
        out.append(ComparisonRequest(candidates[k].event_id, "both", a, b, fa, fb, g))
    return out


def case(seed, n_out=12, n_judged=150, n_pool=400):
    rng = np.random.default_rng(seed)
    outs = [f"o{k}" for k in range(n_out)]
    comps, wins = [], []
    for k in range(n_judged):
        a, b = rng.choice(n_out, 2, replace=False)
        comps.append((outs[a], outs[b], f"j{a}_{k}", f"j{b}_{k}x", 1.0 if k % 3 else 0.6))
        wins.append(bool(rng.random() < 0.5))
    h = {ax: HierarchicalBT(outs, ax).fit(comps, wins, sigma_a=0.5, tau=0.8) for ax in ("econ", "social")}
    judged = [c[2] for c in comps] + [c[3] for c in comps]
    pool = []
    for k in range(n_pool):                             # some judged articles again, new ones shared between pairs,
        a, b = rng.choice(n_out, 2, replace=False)      # and pairs offered in two kinds (bought once)
        fa = judged[rng.integers(len(judged))] if rng.random() < 0.3 else f"n{a}_{k % 90}"
        kinds = (("summary", 0.6), ("full", 1.0)) if rng.random() < 0.3 else (("summary", 0.6),)
        for kind, disc in kinds:
            pool.append(Candidate(f"e{k}:{kind}", outs[a], outs[b], fa, f"m{b}_{k}", cost=float(rng.uniform(1, 3)),
                                  discrimination=disc, relevance=(0.14, 0.5)))
    groups = {"G1": {o: 1.0 for o in outs[:4]}, "G2": {outs[5]: 2.0, outs[6]: 1.0}}
    return h, pool, groups


CONFIGS = [(PlannerConfig(mode="variance_per_cost", target_sd=0.0, batch_size=60), None),
           (PlannerConfig(mode="variance_per_cost", target_sd=0.45, batch_size=300), None),
           (PlannerConfig(mode="variance", target_sd=0.0, batch_size=40), 30.0),
           (PlannerConfig(mode="random", target_sd=0.5, batch_size=50), None)]


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("k", range(len(CONFIGS)))
def test_incremental_planner_picks_what_the_plain_one_picks(seed, k):
    h, pool, groups = case(seed)
    cfg, budget = CONFIGS[k]
    want = reference(h, pool, cfg, groups=groups, budget_left=budget, rng=np.random.default_rng(seed))
    got = plan_joint_hier(h, pool, cfg, groups=groups, budget_left=budget, rng=np.random.default_rng(seed))
    assert [(r.event_id, r.first_outlet, r.first_article_id) for r in got] == \
           [(r.event_id, r.first_outlet, r.first_article_id) for r in want]
    assert np.allclose([r.expected_info_gain for r in got], [r.expected_info_gain for r in want], rtol=1e-9, atol=0)
    if cfg.target_sd > 0 and cfg.mode != "random":
        assert len(got) < cfg.batch_size                # the target stop ended the batch, not its size
