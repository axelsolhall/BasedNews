"""3.3 rating at scale: the hierarchical model's Laplace fit without the dense (n+1+m)² matrices (A66).

The parameters are n outlets, β and m articles. Every comparison touches two outlets, β and two articles, so the
Hessian H = Xᵀ W X + P is sparse: the article block is diagonal plus one entry per compared pair, and each outlet row
reaches only its own articles. Newton steps are solved with a sparse LU of H (SuperLU, fill-reducing ordering), and
log|H| for the evidence is read off U's diagonal. The covariance H⁻¹ is never formed: `SparseCov` answers the blocks
callers actually read — outlets × outlets, and the articles in today's candidate pool — by solving H x = e_j for those
columns only. Results equal the dense fit (`pipeline.rate.hierarchical`) to solver precision; memory grows with the
comparisons, not with m².
"""
from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import splu

from pipeline.rate.model import sigmoid

SOLVE_CHUNK = 16                       # right-hand sides per solve: a chunk is (n+1+m) × 16 floats in memory
CACHE_BYTES = 2 ** 27                  # solved columns kept per factorisation (≈ 128 MB): callers re-read the same few
                                       # (the planner's outlets and pool, the measure loop's single entries)


class SparseCov:
    """The covariance H⁻¹ of one fit, answered block by block. Supports what callers index: `cov[np.ix_(rows, cols)]`,
    `cov[a:b, c:d]`, `cov[i, j]` and paired 1-D index arrays, plus `.shape` and `.copy()`. `lu` factors H with the
    parameters in the order `order` (articles first); indices given and returned are in the posterior's own order."""

    def __init__(self, lu, size: int, order: np.ndarray | None = None):
        from collections import OrderedDict
        self.lu, self.shape, self.ndim = lu, (size, size), 2
        self.order = np.arange(size) if order is None else order
        self.at = np.empty(size, dtype=int)
        self.at[self.order] = np.arange(size)                 # parameter → position in the factorised order
        self.cache: OrderedDict[int, np.ndarray] = OrderedDict()
        self.keep = max(1, CACHE_BYTES // (8 * size))         # columns that fit the cache

    def _columns(self, cols: np.ndarray) -> dict[int, np.ndarray]:
        """Full columns of H⁻¹ (in the posterior's order), from the cache or solved SOLVE_CHUNK at a time."""
        got = {}
        todo = [int(c) for c in cols if int(c) not in self.cache]
        for c in cols:
            if int(c) in self.cache:
                self.cache.move_to_end(int(c))
                got[int(c)] = self.cache[int(c)]
        for k in range(0, len(todo), SOLVE_CHUNK):
            c = np.array(todo[k:k + SOLVE_CHUNK])
            E = np.zeros((self.shape[0], len(c)))
            E[self.at[c], np.arange(len(c))] = 1.0
            X = self.lu.solve(E)[self.at]                      # back to the posterior's order
            for j, cj in enumerate(c):
                got[int(cj)] = X[:, j].copy()
                self.cache[int(cj)] = got[int(cj)]
                if len(self.cache) > self.keep:
                    self.cache.popitem(last=False)
        return got

    def block(self, rows, cols) -> np.ndarray:
        rows, cols = np.asarray(rows, dtype=int).ravel(), np.asarray(cols, dtype=int).ravel()
        uniq, inv = np.unique(cols, return_inverse=True)
        got = self._columns(uniq)
        out = np.stack([got[int(c)][rows] for c in uniq], axis=1) if len(uniq) else np.empty((len(rows), 0))
        return out[:, inv]

    def _index(self, key, axis):
        if isinstance(key, slice):
            return np.arange(self.shape[axis])[key]
        return np.asarray(key, dtype=int)

    def __getitem__(self, key):
        if not isinstance(key, tuple) or len(key) != 2:
            raise TypeError("SparseCov is indexed by two indices, e.g. cov[np.ix_(rows, cols)]")
        a, b = key
        if np.isscalar(a) and np.isscalar(b):
            return float(self.block([a], [b])[0, 0])
        ra, rb = self._index(a, 0), self._index(b, 1)
        if ra.ndim == 2 and rb.ndim == 2:                      # np.ix_(rows, cols)
            return self.block(ra.ravel(), rb.ravel())
        if ra.ndim == 1 and rb.ndim == 1 and not isinstance(a, slice) and not isinstance(b, slice):
            return self.block(ra, rb)[np.arange(len(ra)), np.arange(len(rb))]     # paired, numpy semantics
        return self.block(ra.ravel(), rb.ravel())

    def copy(self):
        return self                                            # read-only: nothing to protect

    def __array__(self, *a, **k):
        raise TypeError(f"a {self.shape[0]:,}² covariance is not materialised: index the block you need")


class MixtureCov(SparseCov):
    """The covariance of an evidence-weighted average of fits (Bayesian model averaging over the spreads' grid):
    Σ = Σ_k w_k (Σ_k + (μ_k − μ)(μ_k − μ)ᵀ), block by block."""

    def __init__(self, parts: list[tuple[float, np.ndarray, SparseCov]], mean: np.ndarray):
        self.parts, self.mean, self.shape, self.ndim = parts, mean, parts[0][2].shape, 2

    def block(self, rows, cols) -> np.ndarray:
        rows, cols = np.asarray(rows, dtype=int).ravel(), np.asarray(cols, dtype=int).ravel()
        tot = sum(w for w, _, _ in self.parts)
        out = np.zeros((len(rows), len(cols)))
        for w, mu, cov in self.parts:
            d_r, d_c = mu[rows] - self.mean[rows], mu[cols] - self.mean[cols]
            out += w * (cov.block(rows, cols) + np.outer(d_r, d_c))
        return out / tot


def design(comps, outlet_index: dict, n: int, articles: list[str]) -> sparse.csr_matrix:
    """The sparse version of HierarchicalBT._design: [θ_outlets (n), β, ε_articles (m)] per comparison."""
    aidx = {a: n + 1 + k for k, a in enumerate(articles)}
    rows, cols, vals = [], [], []
    for r, (oa, ob, aa, ab, disc) in enumerate(comps):
        rows += [r] * 5
        cols += [outlet_index[oa], outlet_index[ob], n, aidx[aa], aidx[ab]]
        vals += [disc, -disc, disc, disc, -disc]
    return sparse.coo_matrix((vals, (rows, cols)), shape=(len(comps), n + 1 + len(articles))).tocsr()   # duplicates add


def elimination_order(n1: int, size: int) -> np.ndarray:
    """Articles first, then outlets and β. Eliminating articles first keeps fill-in inside each story's articles and
    one dense (n+1)² outlet block — the structure real verdicts have (pairs are compared within a story)."""
    return np.concatenate([np.arange(n1, size), np.arange(n1)])


def factorise(H, order: np.ndarray):
    """LU of H in the given order, without pivoting away from it (H is symmetric positive definite)."""
    Hp = H[order][:, order].tocsc()
    return splu(Hp, permc_spec="NATURAL", diag_pivot_thresh=0.0, options={"SymmetricMode": True})


def fit_at(X: sparse.csr_matrix, y: np.ndarray, prec: np.ndarray, max_iter: int, tol: float,
           phi0: np.ndarray | None = None, n1: int | None = None):
    """Newton to the posterior mode; returns (φ̂, (LU of H at φ̂, its order), Laplace evidence) — the dense fit's
    numbers. n1 = outlets + β (the dense block); the articles are eliminated first."""
    size = X.shape[1]
    order = elimination_order(n1, size) if n1 is not None else np.arange(size)
    at = np.empty(size, dtype=int); at[order] = np.arange(size)
    phi = np.zeros(size) if phi0 is None else phi0.copy()
    P = sparse.diags(prec)

    def hessian(p):
        return (X.T @ X.multiply((p * (1 - p))[:, None])).tocsr() + P

    for _ in range(max_iter):
        p = sigmoid(X @ phi)
        grad = X.T @ (y - p) - prec * phi
        step = factorise(hessian(p), order).solve(grad[order])[at]
        phi += step
        if np.max(np.abs(step)) < tol:
            break
    p = np.clip(sigmoid(X @ phi), 1e-12, 1 - 1e-12)
    lu = factorise(hessian(p), order)
    logdet = float(np.sum(np.log(np.abs(lu.U.diagonal()))))
    loglik = float(np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)))
    evidence = loglik - 0.5 * float(np.sum(prec * phi ** 2)) + 0.5 * float(np.sum(np.log(prec))) - 0.5 * logdet
    return phi, (lu, order), evidence
