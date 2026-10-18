"""M6 — active comparison planner.

Given the current Laplace posterior (mean mu, covariance S) and candidate pairs (outlets that covered the
same event, A02), pick the comparisons that most reduce uncertainty.

For a comparison with design vector x (theta_i - theta_j + beta) and predicted p = sigmoid(x @ mu),
the Laplace update after observing it is rank-1:
    S' = S - w * (S x)(S x)^T / (1 + w * x^T S x),     w = p (1 - p)
so the variance of any target quantity v^T phi drops by
    w * (v^T S x)^2 / (1 + w * x^T S x).
Two scoring modes:
  "variance": sum that drop over targets still above the target sd  (A-optimal, default — "get the sd down")
  "eig":      0.5 * log(1 + w * x^T S x)  (D-optimal / expected information gain on the comparison)
Targets are outlets (v = e_k) and/or groups such as countries (v = weights over outlets, A08).
Batches are chosen greedily, applying the fantasised rank-1 update after each pick (mean held fixed).
Stop rule (A09): nothing selected once every target's sd <= target_sd.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.sparse import csr_matrix

from pipeline.contracts import AxisPosterior, ComparisonRequest
from pipeline.rate.model import comparison_vector, sigmoid


@dataclass(frozen=True)
class Candidate:
    event_id: str
    outlet_a: str
    outlet_b: str
    article_a: str = ""
    article_b: str = ""
    cost: float = 1.0            # A30: expected judge cost (tokens) — depends on how much text each side has
    discrimination: float = 1.0  # A30: a in sigmoid(a*d); <1 when evidence is thinner (e.g. summary-only)
    relevance: tuple[float, ...] = ()  # A32: P(story has an angle on axis k) — judge abstains otherwise; () = all 1


@dataclass
class PlannerConfig:
    mode: str = "variance"           # "variance" | "variance_per_cost" | "eig" | "random"
    target_sd: float = 0.15          # A09 placeholder
    batch_size: int = 10
    randomise_order: bool = True     # A04: required to identify the position bias beta


def _target_vectors(post: AxisPosterior, groups: dict[str, dict[str, float]] | None, include_outlets: bool):
    n = len(post.outlets)
    vecs, names = [], []
    if include_outlets:
        for k, o in enumerate(post.outlets):
            v = np.zeros(n + 1)
            v[k] = 1.0
            vecs.append(v)
            names.append(o)
    for g, weights in (groups or {}).items():
        v = np.zeros(n + 1)
        total = sum(weights.values())
        for o, w in weights.items():
            v[post.outlets.index(o)] = w / total
        vecs.append(v)
        names.append(g)
    return names, np.array(vecs)


def target_sds(post: AxisPosterior, groups=None, include_outlets=True) -> dict[str, float]:
    names, V = _target_vectors(post, groups, include_outlets)
    sds = np.sqrt(np.einsum("ij,jk,ik->i", V, post.cov, V))
    return dict(zip(names, sds.tolist()))


def plan(
    post: AxisPosterior,
    candidates: list[Candidate],
    cfg: PlannerConfig | None = None,
    groups: dict[str, dict[str, float]] | None = None,
    include_outlets: bool = True,
    budget_left: int | None = None,
    rng: np.random.Generator | None = None,
) -> list[ComparisonRequest]:
    cfg = cfg or PlannerConfig()
    rng = rng or np.random.default_rng()
    n = len(post.outlets)
    idx = {o: k for k, o in enumerate(post.outlets)}
    k_max = cfg.batch_size if budget_left is None else min(cfg.batch_size, budget_left)
    if k_max <= 0 or not candidates:
        return []

    names, V = _target_vectors(post, groups, include_outlets)
    S = post.cov.copy()
    mu = post.mean
    # A04: decide presentation order up front so the design vector matches what the judge will see
    flip = (rng.random(len(candidates)) < 0.5) if cfg.randomise_order else np.zeros(len(candidates), bool)
    oriented = [
        (c.outlet_b, c.outlet_a, c.article_b, c.article_a) if f else (c.outlet_a, c.outlet_b, c.article_a, c.article_b)
        for c, f in zip(candidates, flip)
    ]
    disc = np.array([c.discrimination for c in candidates])
    cost = np.array([c.cost for c in candidates])
    X = np.array([comparison_vector(idx[a], idx[b], n) for a, b, _, _ in oriented]) * disc[:, None]
    w = sigmoid(X @ mu)
    w = w * (1.0 - w)

    chosen: list[ComparisonRequest] = []
    available = np.ones(len(candidates), dtype=bool)
    for _ in range(k_max):
        tsd = np.sqrt(np.einsum("ij,jk,ik->i", V, S, V))
        open_targets = tsd > cfg.target_sd
        if not open_targets.any():
            break                                       # A09 stop: everything certain enough

        SX = X @ S                                      # rows: (S x)^T
        xSx = np.einsum("ij,ij->i", SX, X)
        if cfg.mode == "random":
            score = rng.random(len(candidates))
        elif cfg.mode == "eig":
            score = 0.5 * np.log1p(w * xSx)
        else:                                           # "variance" or "variance_per_cost"
            vSx = V[open_targets] @ SX.T                # (targets, candidates)
            score = (w * (vSx**2).sum(axis=0)) / (1.0 + w * xSx)
            if cfg.mode == "variance_per_cost":
                score = score / cost                    # information per token, not per call
        score = np.where(available, score, -np.inf)
        best = int(np.argmax(score))
        if not np.isfinite(score[best]):
            break
        available[best] = False

        first, second, fa, sa = oriented[best]
        chosen.append(
            ComparisonRequest(candidates[best].event_id, post.axis, first, second, fa, sa, float(score[best]))
        )

        # fantasised rank-1 update with the oriented design vector
        sx = SX[best]
        S = S - w[best] * np.outer(sx, sx) / (1.0 + w[best] * xSx[best])
    return chosen


def plan_joint(
    posts: dict[str, AxisPosterior],
    candidates: list[Candidate],
    cfg: PlannerConfig | None = None,
    groups: dict[str, dict[str, float]] | None = None,
    budget_left: float | None = None,
    rng: np.random.Generator | None = None,
) -> list[ComparisonRequest]:
    """One judge call answers BOTH axes (A31): score = Σ_axes variance reduction (÷ cost in per-cost mode).
    Fantasised rank-1 updates are applied to every axis after each pick. Returns requests tagged axis="both"."""
    cfg = cfg or PlannerConfig()
    rng = rng or np.random.default_rng()
    axes = list(posts)
    p0 = posts[axes[0]]
    n = len(p0.outlets)
    idx = {o: k for k, o in enumerate(p0.outlets)}
    if not candidates:
        return []
    flip = (rng.random(len(candidates)) < 0.5) if cfg.randomise_order else np.zeros(len(candidates), bool)
    oriented = [(c.outlet_b, c.outlet_a, c.article_b, c.article_a) if f else (c.outlet_a, c.outlet_b, c.article_a, c.article_b)
                for c, f in zip(candidates, flip)]
    disc = np.array([c.discrimination for c in candidates])
    cost = np.array([c.cost for c in candidates])
    X = np.array([comparison_vector(idx[a], idx[b], n) for a, b, _, _ in oriented]) * disc[:, None]
    _, V = _target_vectors(p0, groups, True)
    S = {ax: posts[ax].cov.copy() for ax in axes}
    # A32: expected information on an axis is scaled by the chance the judge can answer it at all
    R = {ax: np.array([(c.relevance[k] if len(c.relevance) > k else 1.0) for c in candidates]) for k, ax in enumerate(axes)}
    W = {}
    for ax in axes:
        p = sigmoid(X @ posts[ax].mean)
        W[ax] = p * (1.0 - p)
    chosen, available, spent = [], np.ones(len(candidates), bool), 0.0
    while len(chosen) < cfg.batch_size:
        score = np.zeros(len(candidates))
        any_open = False
        cache = {}
        for ax in axes:
            tsd = np.sqrt(np.einsum("ij,jk,ik->i", V, S[ax], V))
            open_t = tsd > cfg.target_sd
            SX = X @ S[ax]
            xSx = np.einsum("ij,ij->i", SX, X)
            cache[ax] = (SX, xSx)
            if open_t.any():
                any_open = True
                vSx = V[open_t] @ SX.T
                score += R[ax] * (W[ax] * (vSx ** 2).sum(axis=0)) / (1.0 + W[ax] * xSx)
        if not any_open:
            break
        if cfg.mode == "random":
            score = rng.random(len(candidates))
        elif cfg.mode == "variance_per_cost":
            score = score / cost
        if budget_left is not None:
            available &= spent + cost <= budget_left
        score = np.where(available, score, -np.inf)
        best = int(np.argmax(score))
        if not np.isfinite(score[best]):
            break
        available[best] = False
        spent += cost[best]
        a, b, fa, sa = oriented[best]
        chosen.append(ComparisonRequest(candidates[best].event_id, "both", a, b, fa, sa, float(score[best])))
        for ax in axes:
            SX, xSx = cache[ax]
            sx = SX[best]
            S[ax] = S[ax] - R[ax][best] * W[ax][best] * np.outer(sx, sx) / (1.0 + W[ax][best] * xSx[best])
    return chosen


def plan_joint_hier(
    hposts: dict,
    candidates: list[Candidate],
    cfg: PlannerConfig | None = None,
    groups: dict[str, dict[str, float]] | None = None,
    budget_left: float | None = None,
    rng: np.random.Generator | None = None,
    quota: dict[str, int] | None = None,
) -> list[ComparisonRequest]:
    """plan_joint over the hierarchical posterior (A49): parameters are outlets, β and articles.

    An article not judged yet enters with its prior variance σ_a² and no correlation with anything, so it says the most
    about its outlet; an article judged many times is pinned down, so judging it again moves the outlet little —
    diminishing returns follow from the covariance, with no extra rule. After each pick the chosen articles join the
    active parameter set and the fantasised rank-1 update is applied, so later picks in the same batch see it too.
    Targets (outlets, countries) are unchanged. Stop rule (A09): only targets above cfg.target_sd that some candidate
    still touches count, and nothing more is picked once there are none (target_sd 0 = never stop early).
    quota {outlet: k} (A69): first, while an outlet still owed comparisons has a pair available, the pick is the best
    pair involving such an outlet — whether or not it is among the most informative overall, past the target stop and
    past batch_size if the floor needs it; then the batch goes on as usual.
    hposts: {axis: HierPosterior}. Returns requests tagged axis="both"."""
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
    d = n + 1 + len(judged)
    # room for every article a full batch (and the floor, past it) can add: S grows in place, never copied
    floor_picks = sum(k for k in (quota or {}).values() if k > 0)
    cap = d + min(2 * (cfg.batch_size + floor_picks), len(in_pool) - len(judged))
    S, M = {}, {}
    for ax in axes:                     # an article judged on the other axis only enters this axis at its prior
        full = hposts[ax].article_index()
        src = list(range(n + 1)) + [full.get(a, -1) for a in judged]
        have = np.array([k >= 0 for k in src])
        srci = np.array([k if k >= 0 else 0 for k in src])
        S_ax = np.zeros((cap, cap), order="F"); M_ax = np.zeros(cap)      # Fortran order: BLAS updates it in place
        sub = hposts[ax].cov[np.ix_(srci, srci)]
        blk = np.zeros((d, d))
        blk[np.ix_(have, have)] = sub[np.ix_(have, have)]
        blk[~have, ~have] = sig2[ax]
        S_ax[:d, :d] = blk
        M_ax[:d][have] = hposts[ax].mean[srci[have]]
        S[ax], M[ax] = S_ax, M_ax

    flip = (rng.random(len(candidates)) < 0.5) if cfg.randomise_order else np.zeros(len(candidates), bool)
    oriented = [(c.outlet_b, c.outlet_a, c.article_b, c.article_a) if f else (c.outlet_a, c.outlet_b, c.article_a, c.article_b)
                for c, f in zip(candidates, flip)]
    disc = np.array([c.discrimination for c in candidates])
    cost = np.array([c.cost for c in candidates])
    R = {ax: np.array([(c.relevance[k] if len(c.relevance) > k else 1.0) for c in candidates]) for k, ax in enumerate(axes)}
    _, V0 = _target_vectors(_as_outlet_post(h0), groups, True)            # targets over outlets + β

    # per candidate: 5 parameter slots (first outlet, second outlet, β, first article, second article) → index in the
    # active space (-1 = article not active yet) and coefficient; an article that becomes active is filled in where used
    idx = np.full((len(candidates), 5), -1)
    val = np.zeros((len(candidates), 5))
    waiting: dict[str, list[tuple[int, int]]] = {}
    for r, (a, b, fa, fb) in enumerate(oriented):
        idx[r, :3] = (oidx[a], oidx[b], n)
        val[r, :3] = (disc[r], -disc[r], disc[r])
        for slot, art, sign in ((3, fa, 1.0), (4, fb, -1.0)):
            if art in act:
                idx[r, slot] = act[art]
            else:
                waiting.setdefault(art, []).append((r, slot))
            val[r, slot] = sign * disc[r]
    pair_key = [frozenset((c.article_a, c.article_b)) for c in candidates]
    same_pair: dict[frozenset, list[int]] = {}
    for r, k in enumerate(pair_key):
        same_pair.setdefault(k, []).append(r)
    # T = V S (targets × active) is kept up to date with the same rank-1 updates as S, instead of recomputed per pick;
    # a new article is uncorrelated with everything, so its column of T starts at zero
    T = {ax: np.zeros((V0.shape[0], cap)) for ax in axes}
    for ax in axes:
        T[ax][:, :d] = V0 @ S[ax][: n + 1, :d]
    # A09 stop: a target counts only while its sd is above target_sd and some candidate still touches it (an outlet
    # with no pair available today cannot be helped, so it never keeps the planner buying near-worthless pairs)
    c_out = np.array([(oidx[a], oidx[b]) for a, b, _, _ in oriented])
    members = V0[:, :n] > 0                                                 # target × outlet: whom it depends on
    chosen, available, spent, gains = [], np.ones(len(candidates), bool), 0.0, []
    owed = {oidx[o]: k for o, k in (quota or {}).items() if o in oidx and k > 0}
    indptr = np.arange(0, 5 * len(candidates) + 1, 5)

    def design():
        """Candidates' design rows over the active space (a slot whose article is not active yet counts as zero)."""
        valid = idx >= 0
        return valid, csr_matrix(((val * valid).ravel(), np.where(valid, idx, 0).ravel(), indptr),
                                 shape=(len(candidates), cap))

    # per candidate and axis, kept current with the same rank-1 updates instead of recomputed per pick:
    #   xSx = xᵀSx (+ σ_a² per article not active yet: its prior variance, uncorrelated, exactly as once active);
    #   w = p(1 − p) at the mean, which the fantasised updates hold fixed (a new article's mean is 0, its prior)
    valid, X = design()
    safe = np.where(valid, idx, 0)
    vv = val * valid
    xSx, W = {}, {}
    for ax in axes:
        Sg = S[ax][safe[:, :, None], safe[:, None, :]]                      # candidates × 5 × 5
        xSx[ax] = np.einsum("ri,rij,rj->r", vv, Sg, vv) + sig2[ax] * ((~valid[:, 3:]) * val[:, 3:] ** 2).sum(axis=1)
        p = 1 / (1 + np.exp(-(M[ax][safe] * vv).sum(axis=1)))
        W[ax] = p * (1 - p)
    while True:
        ok = available.copy()
        floor = None
        if owed:
            need = np.zeros(n, bool); need[list(owed)] = True
            floor = ok & (need[c_out[:, 0]] | need[c_out[:, 1]])
            if not floor.any():
                owed, floor = {}, None                                      # nobody owed has a pair left today
        if floor is None and len(chosen) >= cfg.batch_size:
            break
        stop_rule = cfg.target_sd > 0 and floor is None
        if stop_rule:
            reach = np.zeros(n, bool); reach[c_out[ok].ravel()] = True
            reachable = (members & reach[None, :]).any(axis=1)
        score = np.zeros(len(candidates))
        any_open = False
        for ax in axes:
            Tx = T[ax]
            if stop_rule:
                tvar = (Tx[:, : n + 1] * V0).sum(axis=1)                    # v S v, v only on outlets and β
                open_t = reachable & (np.sqrt(np.maximum(tvar, 0.0)) > cfg.target_sd)
                if not open_t.any():
                    continue
                Tx = Tx[open_t]
            any_open = True
            vSx2 = np.square(X @ Tx.T).sum(axis=1)                          # Σ_targets (v S x)² per candidate
            score += R[ax] * (W[ax] * vSx2) / (1.0 + W[ax] * xSx[ax])
        if not any_open:
            break                                                           # every reachable target certain enough
        if cfg.mode == "variance_per_cost":
            score = score / cost
        elif cfg.mode == "random":
            score = rng.random(len(candidates))
        if floor is not None:
            ok = floor
        if budget_left is not None:
            ok &= cost <= (budget_left - spent)
        if not ok.any():
            break
        best = int(np.argmax(np.where(ok, score, -np.inf)))
        chosen.append(best); spent += cost[best]
        for o in c_out[best]:
            if o in owed:
                owed[o] -= 1
                if owed[o] <= 0:
                    del owed[o]
        available[same_pair[pair_key[best]]] = False                        # a pair is bought once, in either kind
        gains.append(float(score[best]))                               # value at the moment it was picked
        # chosen articles become active parameters (prior variance σ_a², uncorrelated), then the fantasised update
        a, b, fa, fb = oriented[best]
        grew = False
        for art in (fa, fb):
            if art not in act:
                act[art] = d
                for ax in axes:
                    S[ax][d, d] = sig2[ax]
                for r, slot in waiting.pop(art, []):
                    idx[r, slot] = d
                d += 1; grew = True
        if grew:
            valid, X = design()
        cols, coef = idx[best], val[best]
        for ax in axes:
            s_ = S[ax][:, cols] @ coef                                      # S x from the chosen's five columns
            k = R[ax][best] * W[ax][best] / (1.0 + W[ax][best] * (coef @ s_[cols]))
            S[ax] = _rank1_downdate(S[ax], k, s_)
            T[ax][:, :d] -= k * np.outer(V0 @ s_[: n + 1], s_[:d])
            xSx[ax] -= k * np.square(X @ s_)
    out = []
    for k, g in zip(chosen, gains):
        a, b, fa, fb = oriented[k]
        out.append(ComparisonRequest(candidates[k].event_id, "both", a, b, fa, fb, g))
    return out


def _rank1_downdate(S: np.ndarray, k: float, s: np.ndarray) -> np.ndarray:
    """S − k s sᵀ, in place (BLAS dger on the Fortran-ordered matrix: no d² temporary per pick)."""
    from scipy.linalg.blas import dger
    return dger(-k, s, s, a=S, overwrite_a=True)


def _as_outlet_post(h):
    from pipeline.contracts import AxisPosterior
    return AxisPosterior(h.axis, h.outlets, h.mean[: h.n + 1], h.cov[: h.n + 1, : h.n + 1], h.n_obs)
