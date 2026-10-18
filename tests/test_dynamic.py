"""M8 v2 (A67): the dynamic model must equal the dense joint Laplace fit (mode, every bin's covariance, evidence), and
on simulated verdicts with a planted move it must find the move, not invent moves in outlets that stayed, and keep
its intervals honest."""
import numpy as np
import pytest

from pipeline.rate.dynamic import DynamicBT
from pipeline.rate.model import sigmoid


def simulate(seed, n=8, T=40, stories_per_bin=14, step=None, active=None, beta=0.3, sigma_a=0.4, d=1.0):
    """Stories per bin among the active outlets, every pair of a story compared with probability 0.7; a story's
    articles fall in its bin or (one in five stories) straddle into the next."""
    rng = np.random.default_rng(seed)
    outlets = [f"o{k}" for k in range(n)]
    truth = np.tile(rng.normal(scale=0.8, size=n), (T, 1))
    if step:
        o, b, size = step
        truth[b:, o] += size
    active = active or {}
    comps, wins, k = [], [], 0
    for t in range(T):
        live = [o for o in range(n) if t in active.get(o, range(T))]
        for _ in range(stories_per_bin):
            members = rng.choice(live, size=min(len(live), rng.integers(3, 6)), replace=False)
            straddle = t + 1 < T and rng.random() < 0.2
            arts = []
            for o in members:
                b = t + 1 if straddle and rng.random() < 0.5 and (t + 1) in active.get(o, range(T)) else t
                arts.append((f"a{k}", o, b, rng.normal(scale=sigma_a))); k += 1
            for i in range(len(arts)):
                for j in range(i + 1, len(arts)):
                    if rng.random() < 0.7:
                        (a, oa, ba, ea), (b_, ob, bb, eb) = arts[i], arts[j]
                        comps.append((outlets[oa], outlets[ob], a, b_, d, ba, bb))
                        wins.append(bool(rng.random() < sigmoid(d * (truth[ba, oa] + ea - truth[bb, ob] - eb + beta))))
    return outlets, truth, comps, wins


def dense_reference(model: DynamicBT, comps, wins, q):
    """The joint Laplace fit with dense matrices: θ (bins × outlets) then articles, β fixed."""
    n, T = model.n, model.T
    arts = sorted({c[2] for c in comps} | {c[3] for c in comps})
    ai = {a: k for k, a in enumerate(arts)}
    D = T * n + len(arts)
    X = np.zeros((len(comps), D))
    for r, (oa, ob, a, b, dk, ta, tb) in enumerate(comps):
        X[r, ta * n + model.index[oa]] += dk; X[r, tb * n + model.index[ob]] -= dk
        X[r, T * n + ai[a]] += dk; X[r, T * n + ai[b]] -= dk
    off = np.array([c[4] for c in comps]) * model.beta
    Qw = np.zeros((T, T))
    for t in range(T):
        Qw[t, t] = (1 / model.tau ** 2 if t == 0 else 0) + (1 / q ** 2 if t > 0 else 0) + (1 / q ** 2 if t < T - 1 else 0)
        if t < T - 1:
            Qw[t, t + 1] = Qw[t + 1, t] = -1 / q ** 2
    P = np.zeros((D, D))
    P[: T * n, : T * n] = np.kron(Qw, np.eye(n))
    P[T * n:, T * n:] = np.eye(len(arts)) / model.sigma_a ** 2
    y, phi = np.asarray(wins, float), np.zeros(D)
    for _ in range(100):
        p = sigmoid(X @ phi + off)
        H = (X.T * (p * (1 - p))) @ X + P
        step = np.linalg.solve(H, X.T @ (y - p) - P @ phi)
        phi += step
        if np.max(np.abs(step)) < 1e-11:
            break
    p = sigmoid(X @ phi + off)
    H = (X.T * (p * (1 - p))) @ X + P
    ev = float(np.sum(y * np.log(p) + (1 - y) * np.log(1 - p))) - 0.5 * phi @ P @ phi \
        + 0.5 * np.linalg.slogdet(P)[1] - 0.5 * np.linalg.slogdet(H)[1]
    C = np.linalg.inv(H)
    cov = np.array([C[t * n:(t + 1) * n, t * n:(t + 1) * n] for t in range(T)])
    return phi[: T * n].reshape(T, n), cov, ev


def test_equals_the_dense_joint_fit():
    outlets, _, comps, wins = simulate(1, n=4, T=6, stories_per_bin=4)
    m = DynamicBT(outlets, 6, sigma_a=0.4, tau=1.0, beta=0.3, tol=1e-11)
    post, ev = m.fit_at(comps, wins, q=0.3)
    mean, cov, ev_d = dense_reference(m, comps, wins, 0.3)
    assert np.allclose(post.mean, mean, atol=1e-7)
    assert np.allclose(post.cov, cov, atol=1e-8)
    assert ev == pytest.approx(ev_d, abs=1e-6)


def test_finds_a_planted_move_and_invents_none():
    outlets, truth, comps, wins = simulate(2, step=(0, 20, 1.5), active={6: range(0, 20), 7: range(20, 40)})
    post = DynamicBT(outlets, 40, sigma_a=0.4, tau=1.0, beta=0.3).fit(comps, wins)
    ref = {f"o{k}": 1.0 for k in range(1, 6)}                   # outlets present throughout
    rel, sd = post.relative(ref)
    true_rel = truth - truth[:, 1:6].mean(axis=1, keepdims=True)
    move = rel[32, 0] - rel[8, 0]
    move_sd = np.hypot(sd[32, 0], sd[8, 0])                    # conservative: ignores their positive correlation
    assert move > 0.9 and move - 2 * move_sd > 0                 # planted +1.5: found, and clearly not zero
    for o in range(1, 6):                                       # stayed put: no move beyond its own uncertainty
        assert abs(rel[32, o] - rel[8, o]) < 3 * np.hypot(sd[32, o], sd[8, o])
    live = [(t, o) for t in range(40) for o in range(8) if (o != 6 or t < 20) and (o != 7 or t >= 20)]
    inside = np.mean([abs(rel[t, o] - true_rel[t, o]) <= 1.645 * sd[t, o] for t, o in live])
    assert inside > 0.8                                         # 90 % intervals hold about their share
    assert post.q in post.evidence and len(post.evidence) > 1


def test_uncertainty_regrows_where_an_outlet_is_not_measured():
    outlets, _, comps, wins = simulate(3, n=6, T=30, active={5: list(range(0, 8)) + list(range(22, 30))})
    post = DynamicBT(outlets, 30, sigma_a=0.4, tau=1.0, beta=0.3).fit(comps, wins, q=0.1)
    _, sd = post.relative({f"o{k}": 1.0 for k in range(5)})
    # mid-gap, a random walk pinned on both sides adds q²·L/4 of variance (a Brownian bridge, gap L = 14 bins);
    # ask for at least half of it on top of the measured ends: the gap widens, the data narrow it again
    bridge = 0.1 ** 2 * 14 / 4
    assert sd[15, 5] ** 2 - max(sd[4, 5], sd[26, 5]) ** 2 > 0.5 * bridge


def test_a_comparison_across_more_than_two_bins_is_refused():
    with pytest.raises(ValueError, match="more than two"):
        DynamicBT(["a", "b"], 5).fit_at([("a", "b", "x", "y", 1.0, 0, 2)], [True], q=0.1)
