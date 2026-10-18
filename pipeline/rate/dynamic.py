"""M8 v2: positions over time (A22, A67) — the hierarchical model with each outlet's position on a random walk.

    article position = θ_outlet(bin) + ε_article,   ε ~ N(0, σ_a²)
    θ_outlet(0) ~ N(0, τ²),   θ_outlet(b) − θ_outlet(b−1) ~ N(0, q²)            (bins of BIN_DAYS days)
    P(first article further +) = σ( a · ((θ_o(b_A) + ε_A) − (θ_o'(b_B) + ε_B) + β) )

Uncertainty regrows between measurements: an outlet not compared for a while widens, and moves are believed only as
far as the verdicts carry them. q — how fast an outlet may drift — is chosen by the Laplace evidence over a grid, as
σ_a is in the static model; σ_a, τ and β come from the static fit over the same verdicts (they describe articles and
the judge, not time).

Exact and sparse. A story spans at most a week, so a comparison touches one bin or two neighbouring ones. Articles
are eliminated per connected group of compared articles (small dense blocks); what remains for the positions is
block-tridiagonal in time (one n × n block per bin and per neighbouring pair). Newton steps, log|H| for the evidence
and the marginal covariance of every bin's positions come from a block Cholesky and its backward pass — the Kalman
smoother's recursion — in O(bins · n³), never the (bins · n + m)² matrix. Comparisons are relative: what the data fix
is each outlet against the others at the same time, so results are read relative to the reference average per bin.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.linalg import cho_factor, cho_solve

from pipeline.rate.model import sigmoid

BIN_DAYS = 7                                  # one bin per week: ≥ the story window, so a comparison spans ≤ 2 bins
Q_GRID = (0.02, 0.05, 0.1, 0.2, 0.4)          # drift sd per bin, on the scale of positions (τ ≈ 1)


@dataclass
class DynamicPosterior:
    axis: str
    outlets: list[str]
    n_bins: int
    mean: np.ndarray                  # bins × outlets
    cov: np.ndarray                   # bins × outlets × outlets: each bin's joint covariance of the outlets
    q: float
    sigma_a: float
    tau: float
    beta: float
    n_obs: int = 0
    evidence: dict = field(default_factory=dict)          # q → log marginal likelihood (Laplace)
    articles: list[str] = field(default_factory=list)
    article_mean: np.ndarray | None = None
    seen: dict = field(default_factory=dict)              # outlet → (first bin, last bin) with a verdict

    def sd(self) -> np.ndarray:
        return np.sqrt(np.einsum("bii->bi", self.cov))

    def relative(self, weights: dict[str, float]) -> tuple[np.ndarray, np.ndarray]:
        """Positions minus the weighted average of `weights`' outlets, per bin, with their sds (A26)."""
        w = np.zeros(len(self.outlets))
        for o, v in weights.items():
            w[self.outlets.index(o)] = v
        w /= w.sum()
        rel = self.mean - (self.mean @ w)[:, None]
        E = np.eye(len(self.outlets)) - w[None, :]                  # row o: e_o − w
        var = np.einsum("oi,bij,oj->bo", E, self.cov, E)
        return rel, np.sqrt(np.maximum(var, 0.0))


class DynamicBT:
    def __init__(self, outlets: list[str], n_bins: int, axis: str = "econ", sigma_a: float = 0.5, tau: float = 1.0,
                 beta: float = 0.0, max_iter: int = 50, tol: float = 1e-8):
        self.outlets, self.T, self.n = list(outlets), n_bins, len(outlets)
        self.index = {o: k for k, o in enumerate(self.outlets)}
        self.axis, self.sigma_a, self.tau, self.beta = axis, sigma_a, tau, beta
        self.max_iter, self.tol = max_iter, tol

    # ---- structure -------------------------------------------------------------------------------------------
    def _setup(self, comps):
        """comps[k] = (first_outlet, second_outlet, first_article, second_article, a_k, first_bin, second_bin)."""
        arts = sorted({c[2] for c in comps} | {c[3] for c in comps})
        aidx = {a: k for k, a in enumerate(arts)}
        A = np.array([aidx[c[2]] for c in comps]); B = np.array([aidx[c[3]] for c in comps])
        tA = np.array([c[5] for c in comps]); tB = np.array([c[6] for c in comps])
        sA = tA * self.n + np.array([self.index[c[0]] for c in comps])
        sB = tB * self.n + np.array([self.index[c[1]] for c in comps])
        if np.any(np.abs(tA - tB) > 1) or np.any((tA < 0) | (tA >= self.T) | (tB < 0) | (tB >= self.T)):
            raise ValueError("a comparison spans more than two neighbouring bins (or a bin is out of range): "
                             "make bins at least as long as the story window")
        d = np.array([c[4] for c in comps], dtype=float)
        parent = list(range(len(arts)))                         # groups of articles linked by comparisons

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for a, b in zip(A, B):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        groups: dict[int, list[int]] = {}
        for k in range(len(comps)):
            groups.setdefault(find(A[k]), []).append(k)
        return arts, A, B, sA, sB, d, list(groups.values())

    def _prior_theta(self, q: float):
        """The random walk's precision, block-tridiagonal: diagonal blocks are multiples of I, off-diagonal −I/q²."""
        diag = np.full(self.T, 2.0 / q ** 2)
        diag[0], diag[-1] = 1.0 / self.tau ** 2 + 1.0 / q ** 2, 1.0 / q ** 2
        if self.T == 1:
            diag[0] = 1.0 / self.tau ** 2
        return diag, -1.0 / q ** 2

    def _prior_logdet(self, q: float) -> float:
        diag, off = self._prior_theta(q)
        ld, dprev = 0.0, None                                    # scalar tridiagonal, the same for every outlet
        for t in range(self.T):
            dt_ = diag[t] - (off ** 2 / dprev if dprev is not None else 0.0)
            ld += np.log(dt_)
            dprev = dt_
        return self.n * ld

    # ---- one fit at a given q ----------------------------------------------------------------------------------
    def _assemble(self, theta, eps, y, A, B, sA, sB, d, q):
        n, T = self.n, self.T
        flat = theta.ravel()
        x = d * (flat[sA] + eps[A] - flat[sB] - eps[B] + self.beta)
        p = sigmoid(x)
        r, c = d * (y - p), d * d * p * (1 - p)
        diag, off = self._prior_theta(q)
        g_t = np.zeros(T * n)
        np.add.at(g_t, sA, r); np.add.at(g_t, sB, -r)
        g_t -= (np.repeat(diag, n) * flat)
        g_t[: -n] -= off * flat[n:]
        g_t[n:] -= off * flat[: -n]
        g_e = np.zeros(len(eps))
        np.add.at(g_e, A, r); np.add.at(g_e, B, -r)
        g_e -= eps / self.sigma_a ** 2
        D = np.zeros((T, n, n)); O = np.zeros((max(T - 1, 0), n, n))
        D[np.arange(T)[:, None], np.arange(n)[None, :], np.arange(n)[None, :]] += diag[:, None]
        if T > 1:
            O[:, np.arange(n), np.arange(n)] += off
        self._add_tt(D, O, sA, sA, c); self._add_tt(D, O, sB, sB, c)
        self._add_tt(D, O, sA, sB, -c); self._add_tt(D, O, sB, sA, -c)
        loglik = float(np.sum(y * np.log(np.clip(p, 1e-12, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-12, 1))))
        return g_t, g_e, D, O, c, loglik

    def _add_tt(self, D, O, si, sj, v):
        """Add v at θ-θ entries (si, sj) of the block-tridiagonal matrix (only the upper off-diagonal blocks are kept)."""
        n = self.n
        ti, oi, tj, oj = si // n, si % n, sj // n, sj % n
        same = ti == tj
        np.add.at(D, (ti[same], oi[same], oj[same]), v[same])
        up = tj == ti + 1                                       # (t, t+1) block
        np.add.at(O, (ti[up], oi[up], oj[up]), v[up])

    def _eliminate(self, D, O, g_t, g_e, A, B, sA, sB, c, groups):
        """Fold every article group into the θ blocks (Schur complement). Returns what back-substitution needs."""
        s2 = 1.0 / self.sigma_a ** 2
        kept, logdet = [], 0.0
        for ks in groups:
            ks = np.asarray(ks)
            arts = np.unique(np.concatenate([A[ks], B[ks]]))
            slots = np.unique(np.concatenate([sA[ks], sB[ks]]))
            ai = {a: k for k, a in enumerate(arts)}; si = {s: k for k, s in enumerate(slots)}
            Hc = np.eye(len(arts)) * s2
            Bc = np.zeros((len(arts), len(slots)))
            for k in ks:
                a, b, ta, tb, w = ai[A[k]], ai[B[k]], si[sA[k]], si[sB[k]], c[k]
                Hc[a, a] += w; Hc[b, b] += w; Hc[a, b] -= w; Hc[b, a] -= w
                Bc[a, ta] += w; Bc[a, tb] -= w; Bc[b, ta] -= w; Bc[b, tb] += w
            f = cho_factor(Hc)
            logdet += 2.0 * float(np.sum(np.log(np.diag(f[0]))))
            HiB = cho_solve(f, Bc)
            S = Bc.T @ HiB                                      # subtract from θ-θ
            gs = HiB.T @ g_e[arts]                              # subtract from g_θ
            ii, jj = np.meshgrid(np.arange(len(slots)), np.arange(len(slots)), indexing="ij")
            self._add_tt(D, O, slots[ii.ravel()], slots[jj.ravel()], -S.ravel())
            np.add.at(g_t, slots, -gs)
            kept.append((arts, slots, f, Bc))
        return kept, logdet

    @staticmethod
    def _block_solve(D, O, g):
        """Block Cholesky of the symmetric block-tridiagonal matrix (D diagonal, O upper); solve, log|·|, and the
        diagonal blocks of its inverse (the backward pass)."""
        T, n = D.shape[0], D.shape[1]
        G = g.reshape(T, n)
        piv, z, logdet = [], np.zeros_like(G), 0.0
        for t in range(T):
            Dt = D[t] - (O[t - 1].T @ cho_solve(piv[-1], O[t - 1]) if t else 0.0)
            f = cho_factor(Dt)
            piv.append(f)
            logdet += 2.0 * float(np.sum(np.log(np.diag(f[0]))))
            z[t] = G[t] - (O[t - 1].T @ cho_solve(piv[t - 1], z[t - 1]) if t else 0.0)
        x = np.zeros_like(G)
        x[T - 1] = cho_solve(piv[T - 1], z[T - 1])
        for t in range(T - 2, -1, -1):
            x[t] = cho_solve(piv[t], z[t] - O[t] @ x[t + 1])
        Sig = np.zeros((T, n, n))
        Sig[T - 1] = cho_solve(piv[T - 1], np.eye(n))
        for t in range(T - 2, -1, -1):
            Di_O = cho_solve(piv[t], O[t])
            Sig[t] = cho_solve(piv[t], np.eye(n)) + Di_O @ Sig[t + 1] @ Di_O.T
        return x.ravel(), logdet, Sig

    def fit_at(self, comps, first_wins, q: float, start=None) -> tuple[DynamicPosterior, float]:
        arts, A, B, sA, sB, d, groups = self._setup(comps)
        y = np.asarray(first_wins, dtype=float)
        theta = np.zeros((self.T, self.n)) if start is None else start[0].copy()
        eps = np.zeros(len(arts)) if start is None else start[1].copy()
        for _ in range(self.max_iter):
            g_t, g_e, D, O, c, _ = self._assemble(theta, eps, y, A, B, sA, sB, d, q)
            kept, _ = self._eliminate(D, O, g_t, g_e, A, B, sA, sB, c, groups)
            dt_, _, _ = self._block_solve(D, O, g_t)
            de = np.zeros_like(eps)
            for arts_k, slots, f, Bc in kept:
                de[arts_k] = cho_solve(f, g_e[arts_k] - Bc @ dt_[slots])
            theta += dt_.reshape(self.T, self.n); eps += de
            if max(np.max(np.abs(dt_)), np.max(np.abs(de)) if len(de) else 0.0) < self.tol:
                break
        g_t, g_e, D, O, c, loglik = self._assemble(theta, eps, y, A, B, sA, sB, d, q)
        _, ld_art = self._eliminate(D, O, g_t, g_e, A, B, sA, sB, c, groups)
        _, ld_theta, Sig = self._block_solve(D, O, np.zeros(self.T * self.n))
        diag, off = self._prior_theta(q)
        flat = theta.ravel()
        quad = float(np.sum(np.repeat(diag, self.n) * flat ** 2) + 2 * off * np.sum(flat[: -self.n] * flat[self.n:]))
        quad += float(np.sum(eps ** 2) / self.sigma_a ** 2)
        prior_ld = self._prior_logdet(q) + len(arts) * np.log(1.0 / self.sigma_a ** 2)
        evidence = loglik - 0.5 * quad + 0.5 * prior_ld - 0.5 * (ld_art + ld_theta)
        first, last = np.full(self.n, self.T), np.full(self.n, -1)
        for s_ in (sA, sB):
            np.minimum.at(first, s_ % self.n, s_ // self.n); np.maximum.at(last, s_ % self.n, s_ // self.n)
        seen = {o: (int(first[k]), int(last[k])) for k, o in enumerate(self.outlets) if last[k] >= 0}
        post = DynamicPosterior(self.axis, self.outlets, self.T, theta, Sig, q, self.sigma_a, self.tau, self.beta,
                                len(comps), articles=arts, article_mean=eps, seen=seen)
        return post, evidence

    def fit(self, comps, first_wins, q: float | None = None, q_grid=Q_GRID) -> DynamicPosterior:
        """At a given q, or the q with the highest evidence over q_grid (each start from the previous mode)."""
        grid = [q] if q is not None else list(q_grid)
        best, ev, start = None, {}, None
        for qk in grid:
            post, e = self.fit_at(comps, first_wins, qk, start)
            ev[qk] = e
            start = (post.mean, post.article_mean)
            if best is None or e > best[1]:
                best = (post, e)
        best[0].evidence = ev
        return best[0]
