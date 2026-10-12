"""Clustering methods. Both use a time-aware similarity:
    s'(a,b) = cos(a,b) * exp(-|dt| / decay_h)   if |dt| <= window_h else -inf      (from match_algo.ipynb)
Methods (A20):
  "components" — union-find over pairs with s' >= threshold (notebook baseline; prone to chaining)
  "average"    — average-linkage agglomerative clustering, cut at distance 1 - threshold
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform


@dataclass
class ClusterConfig:
    method: str = "average"
    threshold: float = 0.55
    window_h: float = 72.0
    decay_h: float = 96.0


def time_aware_similarity(V: np.ndarray, hours: np.ndarray, cfg: ClusterConfig) -> np.ndarray:
    S = V @ V.T
    dt = np.abs(hours[:, None] - hours[None, :])
    S = S * np.exp(-dt / cfg.decay_h)
    S[dt > cfg.window_h] = -1.0
    np.fill_diagonal(S, 1.0)
    return S


def cluster(V: np.ndarray, hours: np.ndarray, cfg: ClusterConfig) -> np.ndarray:
    """Returns an integer label per row."""
    n = len(V)
    if n == 0:
        return np.zeros(0, dtype=int)
    S = time_aware_similarity(V, hours, cfg)
    if cfg.method == "components":
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        ii, jj = np.where(np.triu(S, 1) >= cfg.threshold)
        for i, j in zip(ii, jj):
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj
        roots = [find(i) for i in range(n)]
        _, labels = np.unique(roots, return_inverse=True)
        return labels
    return cut(average_linkage(V, hours, cfg, S), cfg.threshold)


def average_linkage(V: np.ndarray, hours: np.ndarray, cfg: ClusterConfig, S: np.ndarray | None = None) -> np.ndarray:
    """The average-linkage tree; `cut` it at any threshold without recomputing (threshold sweeps, E7)."""
    S = time_aware_similarity(V, hours, cfg) if S is None else S
    D = np.clip(1.0 - S, 0.0, 2.0)
    np.fill_diagonal(D, 0.0)
    return linkage(squareform(D, checks=False), method="average")


def cut(Z: np.ndarray, threshold: float) -> np.ndarray:
    return fcluster(Z, t=1.0 - threshold, criterion="distance") - 1
