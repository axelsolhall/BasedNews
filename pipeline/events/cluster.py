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
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
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
    if cfg.method == "average":
        return average_by_components(V, hours, cfg)
    if cfg.method != "components":
        raise ValueError(f"unknown method {cfg.method}")
    S = time_aware_similarity(V, hours, cfg)
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


def _distance_blocks(V: np.ndarray, hours: np.ndarray, cfg: ClusterConfig, block: int = 512):
    """(start, stop, D): D[r, j] = 1 − time-aware similarity of rows start + r and j, as float64. Every distance any
    function here uses comes from these blocks, so all of them see bit-for-bit the same numbers."""
    n = len(V)
    for start in range(0, n - 1, block):
        stop = min(start + block, n - 1)
        S = V[start:stop] @ V.T                                   # float32, as in time_aware_similarity
        dt = np.abs(hours[start:stop, None] - hours[None, :])
        S = S * np.exp(-dt / cfg.decay_h)
        S[dt > cfg.window_h] = -1.0
        yield start, stop, np.clip(1.0 - S, 0.0, 2.0).astype(np.float64)


def condensed_distances(V: np.ndarray, hours: np.ndarray, cfg: ClusterConfig, block: int = 512) -> np.ndarray:
    """1 − time-aware similarity for every pair i < j, built a block of rows at a time: the same numbers as
    `time_aware_similarity`, without the full n × n matrices (peak memory ≈ the condensed array instead of ≈ 5 n² floats)."""
    n = len(V)
    out = np.empty(n * (n - 1) // 2, dtype=np.float64)
    k = 0
    for start, stop, D in _distance_blocks(V, hours, cfg, block):
        for r, i in enumerate(range(start, stop)):
            row = D[r, i + 1:]
            out[k:k + len(row)] = row
            k += len(row)
    return out


def average_by_components(V: np.ndarray, hours: np.ndarray, cfg: ClusterConfig, block: int = 512) -> np.ndarray:
    """The same partition as `cut(average_linkage(...), threshold)`, without the n² distance array.

    An average-linkage merge at height ≤ t = 1 − threshold needs at least one pair at distance ≤ t (an average is never
    below its smallest term), so no story can span two connected components of the graph "distance ≤ t". Pass 1 finds
    those components from the few close pairs; pass 2 collects every pair inside each component and runs the same
    average linkage there. Memory ≈ the largest component squared instead of all articles squared (A61: 41 countries
    give ≈ 3× the articles, ≈ 14 GB for the full array). Equivalence: tests/test_cluster_components.py."""
    n = len(V)
    if n < 2:
        return np.zeros(n, dtype=int)
    t = 1.0 - cfg.threshold
    I, J = [], []
    for start, stop, D in _distance_blocks(V, hours, cfg, block):
        for r, i in enumerate(range(start, stop)):
            js = np.nonzero(D[r, i + 1:] <= t)[0] + i + 1
            I.append(np.full(len(js), i)); J.append(js)
    I, J = np.concatenate(I), np.concatenate(J)
    _, comp = connected_components(coo_matrix((np.ones(len(I)), (I, J)), shape=(n, n)), directed=False)
    members = {}
    for i, c in enumerate(comp):
        members.setdefault(c, []).append(i)
    multi = {c: np.array(m) for c, m in members.items() if len(m) > 1}
    pos = {i: (c, a) for c, m in multi.items() for a, i in enumerate(m)}
    cond = {c: np.empty(len(m) * (len(m) - 1) // 2) for c, m in multi.items()}
    for start, stop, D in _distance_blocks(V, hours, cfg, block):
        for r, i in enumerate(range(start, stop)):
            if i not in pos:
                continue
            c, a = pos[i]
            m = multi[c]
            k0 = len(m) * a - a * (a + 1) // 2                    # condensed index of pair (a, a + 1)
            cond[c][k0:k0 + len(m) - a - 1] = D[r, m[a + 1:]]
    labels, nxt = np.empty(n, dtype=int), 0
    for c, m in members.items():
        if len(m) == 1:
            labels[m[0]] = nxt; nxt += 1
            continue
        sub = fcluster(linkage(cond[c], method="average"), t=t, criterion="distance") - 1
        labels[m] = sub + nxt
        nxt += sub.max() + 1
    return labels


def average_linkage(V: np.ndarray, hours: np.ndarray, cfg: ClusterConfig, S: np.ndarray | None = None) -> np.ndarray:
    """The average-linkage tree; `cut` it at any threshold without recomputing (threshold sweeps, E7)."""
    if S is None:
        return linkage(condensed_distances(V, hours, cfg), method="average")
    D = np.clip(1.0 - S, 0.0, 2.0)
    np.fill_diagonal(D, 0.0)
    return linkage(squareform(D, checks=False), method="average")


def cut(Z: np.ndarray, threshold: float) -> np.ndarray:
    return fcluster(Z, t=1.0 - threshold, criterion="distance") - 1
