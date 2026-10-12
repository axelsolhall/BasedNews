"""3.3 rating, hierarchical (A49): an outlet is measured over its articles, not over its verdicts.

    article position  = θ_outlet + ε_article,      ε_article ~ N(0, σ_a²)
    P(first article further +) = σ( a · ((θ_o + ε_A) − (θ_o' + ε_B) + β) )

Repeated comparisons of one article pin down that article (its θ_o + ε_A); what they say about the outlet is capped at
one article's worth, because ε_A is unknown a priori. So repeat comparisons keep their value but with diminishing
returns, and the outlet's uncertainty reflects how many DIFFERENT articles back it. σ_a — how much an outlet's articles
differ from each other — is chosen by the Laplace-approximated marginal likelihood (evidence) over a grid, not by hand.
σ_a → 0 recovers the flat model (pipeline/rate/model.py).

σ_a itself is uncertain when data are thin (E5: picking the single best σ_a underestimated it, 0.18 for a true 0.5).
So by default the posterior AVERAGES over the σ_a grid, each value weighted by its evidence (Bayesian model averaging);
the returned mean and covariance are the mixture's moments, so the extra uncertainty reaches the outlet intervals.
The outlet spread τ is averaged the same way (real data, E5: the article spread σ_a was at the top of the first grid, so
only the ratio σ_a/τ is meaningful). Intra-class correlation ICC = τ² / (τ² + σ_a²): the share of article-to-article
variation that is "which outlet", not "which article".

The outlet block of the posterior is returned as an ordinary AxisPosterior (outlets + β), so aggregation and the
planner's outlet/country targets work unchanged; the full posterior (with articles) is kept for the planner.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pipeline.contracts import AxisPosterior, Axis
from pipeline.rate.model import BTConfig, sigmoid

SIGMA_GRID = (0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0, 4.0)   # article spread around its outlet
TAU_GRID = (0.25, 0.5, 1.0, 2.0)                                       # spread of outlets (was fixed at 1)


@dataclass
class HierPosterior:
    axis: Axis
    outlets: list[str]
    articles: list[str]
    mean: np.ndarray            # [θ_outlets (n), β, ε_articles (m)]
    cov: np.ndarray             # full Laplace covariance, same order
    sigma_a: float
    n_obs: int = 0
    evidence: dict = field(default_factory=dict)   # (τ, σ_a) → log marginal likelihood (Laplace)
    tau: float = 1.0

    @property
    def icc(self) -> float:
        return self.tau ** 2 / (self.tau ** 2 + self.sigma_a ** 2)

    @property
    def n(self) -> int:
        return len(self.outlets)

    def outlet_posterior(self) -> AxisPosterior:
        k = self.n + 1
        return AxisPosterior(self.axis, self.outlets, self.mean[:k].copy(), self.cov[:k, :k].copy(), self.n_obs,
                             meta={"model": "hierarchical", "sigma_a": self.sigma_a, "tau": self.tau, "icc": self.icc,
                                   "n_articles": len(self.articles)})

    def article_index(self) -> dict[str, int]:
        return {a: self.n + 1 + k for k, a in enumerate(self.articles)}


class HierarchicalBT:
    def __init__(self, outlets: list[str], axis: Axis = "econ", config: BTConfig | None = None):
        self.outlets = list(outlets)
        self.index = {o: k for k, o in enumerate(self.outlets)}
        self.axis = axis
        self.cfg = config or BTConfig()

    def _design(self, comps, articles):
        n, m = len(self.outlets), len(articles)
        aidx = {a: n + 1 + k for k, a in enumerate(articles)}
        X = np.zeros((len(comps), n + 1 + m))
        for r, (oa, ob, aa, ab, disc) in enumerate(comps):
            X[r, self.index[oa]] += disc
            X[r, self.index[ob]] -= disc
            X[r, n] += disc                               # β: first-position bias (A04)
            X[r, aidx[aa]] += disc
            X[r, aidx[ab]] -= disc
        return X

    def _prior_precision(self, m: int, sigma_a: float, tau: float | None = None) -> np.ndarray:
        n = len(self.outlets)
        tau = self.cfg.prior_sd if tau is None else tau
        return np.array([1.0 / tau ** 2] * n + [1.0 / self.cfg.beta_prior_sd ** 2] + [1.0 / sigma_a ** 2] * m)

    def _fit_at(self, X, y, prec):
        phi = np.zeros(X.shape[1])
        for _ in range(self.cfg.max_iter):
            p = sigmoid(X @ phi)
            grad = X.T @ (y - p) - prec * phi
            H = (X.T * (p * (1 - p))) @ X + np.diag(prec)
            step = np.linalg.solve(H, grad)
            phi += step
            if np.max(np.abs(step)) < self.cfg.tol:
                break
        p = np.clip(sigmoid(X @ phi), 1e-12, 1 - 1e-12)
        H = (X.T * (p * (1 - p))) @ X + np.diag(prec)
        loglik = float(np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)))
        # Laplace evidence: log p(y|φ̂) + log N(φ̂; 0, P⁻¹) + D/2 log 2π − ½ log|H|  (the 2π terms cancel)
        evidence = loglik - 0.5 * float(np.sum(prec * phi ** 2)) + 0.5 * float(np.sum(np.log(prec))) \
            - 0.5 * float(np.linalg.slogdet(H)[1])
        return phi, H, evidence

    def fit(self, comps: list[tuple[str, str, str, str, float]], first_wins: list[bool],
            sigma_a: float | None = None, average: bool = True, tau: float | None = None) -> HierPosterior:
        """comps[k] = (first_outlet, second_outlet, first_article, second_article, discrimination a_k).
        sigma_a given → fixed. Else over SIGMA_GRID: average=True → evidence-weighted average (default), else the best."""
        articles = sorted({c[2] for c in comps} | {c[3] for c in comps})
        n = len(self.outlets)
        if not comps:
            prec = self._prior_precision(0, sigma_a or 0.5, tau)
            return HierPosterior(self.axis, self.outlets, [], np.zeros(n + 1), np.diag(1 / prec), sigma_a or 0.5, 0,
                                 tau=tau or self.cfg.prior_sd)
        X = self._design(comps, articles)
        y = np.asarray(first_wins, dtype=float)
        sgrid = [sigma_a] if sigma_a is not None else list(SIGMA_GRID)
        tgrid = [tau] if tau is not None else list(TAU_GRID)
        fits, ev = [], {}
        for t in tgrid:
            for s in sgrid:
                phi, H, e = self._fit_at(X, y, self._prior_precision(len(articles), s, t))
                ev[(t, s)] = e
                fits.append(((t, s), phi, H, e))
        if len(fits) == 1 or not average:
            (t, s), phi, H, _ = max(fits, key=lambda f: f[3])
            return HierPosterior(self.axis, self.outlets, articles, phi, np.linalg.inv(H), s, len(comps), ev, t)
        e = np.array([f[3] for f in fits])
        w = np.exp(e - e.max()); w /= w.sum()                      # flat prior over the (log-spaced) grid
        keep = [(wk, f) for wk, f in zip(w, fits) if wk > 1e-4]
        tot = sum(wk for wk, _ in keep)
        mean = sum(wk * f[1] for wk, f in keep) / tot
        cov = sum(wk * (np.linalg.inv(f[2]) + np.outer(f[1] - mean, f[1] - mean)) for wk, f in keep) / tot
        sig = float(sum(wk * f[0][1] for wk, f in keep) / tot)
        tau_m = float(sum(wk * f[0][0] for wk, f in keep) / tot)
        return HierPosterior(self.axis, self.outlets, articles, mean, cov, sig, len(comps), ev, tau_m)


def marginal_value_of_repeats(sigma_a: float, comparison_sd: float, n_max: int = 10) -> list[float]:
    """Information about the outlet (1/variance) from ONE article after n comparisons, in a Gaussian approximation:
    Var(outlet | article seen n times) contribution = σ_a² + comparison_sd²/n. Returns the marginal gain of each look."""
    info = [0.0] + [1.0 / (sigma_a ** 2 + comparison_sd ** 2 / k) for k in range(1, n_max + 1)]
    return [info[k] - info[k - 1] for k in range(1, n_max + 1)]
