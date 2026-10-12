"""M8 — Bayesian Bradley–Terry with judge position bias, Laplace posterior.

Model (one axis):
    theta_o ~ N(0, prior_sd^2)                  latent position of outlet o           (A03)
    beta    ~ N(0, beta_prior_sd^2)              judge's first-position bias           (A04)
    P(first wins | i shown first, j second) = sigmoid(theta_i - theta_j + beta)       (A05)
Abstentions are ignored (A06).

Fit = MAP by Newton's method; posterior covariance = inverse negative Hessian at the MAP (Laplace, A05).
Parameter vector phi = [theta_0 .. theta_{n-1}, beta].
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pipeline.contracts import AxisPosterior, Axis, PositionEstimate


def sigmoid(x: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-x))


# ---- design ------------------------------------------------------------------
def comparison_vector(i: int, j: int, n: int) -> np.ndarray:
    """x such that x @ phi = theta_i - theta_j + beta."""
    x = np.zeros(n + 1)
    x[i] += 1.0
    x[j] -= 1.0
    x[n] = 1.0
    return x


@dataclass
class BTConfig:
    prior_sd: float = 1.0          # scale of outlet positions (unit-variance scale, A09 targets refer to it)
    beta_prior_sd: float = 1.0
    max_iter: int = 50
    tol: float = 1e-9


class BradleyTerryLaplace:
    def __init__(self, outlets: list[str], axis: Axis = "econ", config: BTConfig | None = None):
        self.outlets = list(outlets)
        self.index = {o: k for k, o in enumerate(self.outlets)}
        self.axis = axis
        self.cfg = config or BTConfig()
        n = len(self.outlets)
        self.prior_precision = np.diag(
            [1.0 / self.cfg.prior_sd**2] * n + [1.0 / self.cfg.beta_prior_sd**2]
        )

    # ---- fitting ---------------------------------------------------------------
    def design(self, pairs: list[tuple[str, str]]) -> np.ndarray:
        n = len(self.outlets)
        X = np.zeros((len(pairs), n + 1))
        for r, (a, b) in enumerate(pairs):
            X[r] = comparison_vector(self.index[a], self.index[b], n)
        return X

    def fit(self, pairs: list[tuple[str, str]], first_wins: list[bool],
            discrimination: list[float] | None = None) -> AxisPosterior:
        """pairs[k] = (first_outlet, second_outlet); first_wins[k] = outcome. Abstentions must be filtered out.
        discrimination[k] = a_k in P = sigmoid(a_k * (theta_i - theta_j + beta)); a<1 for noisier evidence (A30)."""
        n = len(self.outlets)
        phi = np.zeros(n + 1)
        if not pairs:
            return AxisPosterior(self.axis, self.outlets, phi, np.linalg.inv(self.prior_precision), 0)

        X = self.design(pairs)
        if discrimination is not None:
            X = X * np.asarray(discrimination, dtype=float)[:, None]
        y = np.asarray(first_wins, dtype=float)
        for _ in range(self.cfg.max_iter):
            p = sigmoid(X @ phi)
            grad = X.T @ (y - p) - self.prior_precision @ phi
            w = p * (1.0 - p)
            neg_hess = (X.T * w) @ X + self.prior_precision
            step = np.linalg.solve(neg_hess, grad)
            phi = phi + step
            if np.max(np.abs(step)) < self.cfg.tol:
                break
        p = sigmoid(X @ phi)
        neg_hess = (X.T * (p * (1.0 - p))) @ X + self.prior_precision
        cov = np.linalg.inv(neg_hess)
        return AxisPosterior(self.axis, self.outlets, phi, cov, n_obs=len(pairs))


# ---- outputs -----------------------------------------------------------------
def estimates(post: AxisPosterior, counts: dict[str, int] | None = None) -> list[PositionEstimate]:
    n = len(post.outlets)
    sd = np.sqrt(np.diag(post.cov)[:n])
    counts = counts or {}
    return [
        PositionEstimate(o, post.axis, float(post.mean[k]), float(sd[k]), counts.get(o, 0))
        for k, o in enumerate(post.outlets)
    ]


def outlet_sds(post: AxisPosterior) -> np.ndarray:
    n = len(post.outlets)
    return np.sqrt(np.diag(post.cov)[:n])


def group_sd(post: AxisPosterior, members: list[str], weights: list[float] | None = None) -> float:
    """Posterior sd of a weighted mean of outlets (a country position, A08). Uses covariances, not just sds."""
    n = len(post.outlets)
    idx = [post.outlets.index(m) for m in members]
    w = np.asarray(weights if weights is not None else [1.0] * len(idx), dtype=float)
    w = w / w.sum()
    v = np.zeros(n + 1)
    v[idx] = w
    return float(np.sqrt(v @ post.cov @ v))
