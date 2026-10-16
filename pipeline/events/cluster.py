"""Clustering methods. Both use a time-aware similarity:
    s'(a,b) = cos(a,b) * exp(-|dt| / decay_h)   if |dt| <= window_h else -inf      (from match_algo.ipynb)
Methods (A20):
  "components" — union-find over pairs with s' >= threshold (notebook baseline; prone to chaining)
  "average"    — average-linkage agglomerative clustering, cut at distance 1 - threshold
"""
from __future__ import annotations

import heapq
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
    if cfg.method == "average":
        return average_sparse(V, hours, cfg)
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


def _distances(V: np.ndarray, hours: np.ndarray, cfg: ClusterConfig, a, b) -> np.ndarray:
    """D[r, c] = 1 − time-aware similarity of rows a[r] and b[c] (index arrays or slices), as float64. Every distance any
    function here uses comes from this one formula."""
    S = V[a] @ V[b].T                                             # float32, as in time_aware_similarity
    dt = np.abs(hours[a][:, None] - hours[b][None, :])
    S = S * np.exp(-dt / cfg.decay_h)
    S[dt > cfg.window_h] = -1.0
    return np.clip(1.0 - S, 0.0, 2.0).astype(np.float64, copy=False)


def _distance_blocks(V: np.ndarray, hours: np.ndarray, cfg: ClusterConfig, block: int = 512):
    """(start, stop, D): D[r, j] = distance of rows start + r and j, a block of rows at a time."""
    n = len(V)
    for start in range(0, n - 1, block):
        stop = min(start + block, n - 1)
        yield start, stop, _distances(V, hours, cfg, slice(start, stop), slice(None))


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


BLOCK_BYTES = 2 ** 28      # ≈ 37 bytes per distance in flight (float32 product, float64 gap, decay and result): ≈ 0.25 GB


def close_pairs(V: np.ndarray, hours: np.ndarray, cfg: ClusterConfig, block_bytes: int = BLOCK_BYTES):
    """(I, J, D) for every pair i < j at distance ≤ 1 − threshold. Rows must be sorted by time: a pair more than
    window_h apart has distance 2 by definition, so each block of rows only computes the columns within window_h of it."""
    n, t = len(V), 1.0 - cfg.threshold
    rows = max(1, block_bytes // (37 * n))
    I, J, W = [], [], []
    for start in range(0, n - 1, rows):
        stop = min(start + rows, n - 1)
        hi = int(np.searchsorted(hours, hours[stop - 1] + cfg.window_h, side="right"))
        D = _distances(V, hours, cfg, slice(start, stop), slice(start, hi))
        r, c = np.nonzero(D <= t)
        keep = c > r                                              # i < j: columns start at the block's first row
        I.append(r[keep] + start); J.append(c[keep] + start); W.append(D[r[keep], c[keep]])
    return np.concatenate(I), np.concatenate(J), np.concatenate(W)


def average_sparse(V: np.ndarray, hours: np.ndarray, cfg: ClusterConfig, block_bytes: int = BLOCK_BYTES) -> np.ndarray:
    """The same partition as `cut(average_linkage(...), threshold)`, in memory that grows with the close pairs, not n².

    Average linkage merges the two stories with the smallest average distance, up to t = 1 − threshold. Two facts make
    that sparse and still exact:
    - an average is never below its smallest term, so a merge at height ≤ t needs at least one pair of articles at
      distance ≤ t: only stories joined by such a close pair are ever candidates;
    - the average distance of A ∪ B to X is (sum(A, X) + sum(B, X)) / (|A ∪ B| · |X|) (Lance–Williams): each candidate
      keeps the sum of its distances, and a sum not yet known is computed from the two stories' vectors.
    Merging the closest candidate until none is ≤ t makes exactly the tree's merges up to t, i.e. its cut (average
    linkage is reducible: merge heights never go down). Memory ≈ the close pairs; work ≈ n × the articles within
    window_h of each. A61: a full 7-day window of 41 countries is ≈ 170 k articles with one linked group of ≈ 85 k, which
    as a condensed array (the previous method) needs ≈ 60 GB. Equivalence: tests/test_cluster_components.py."""
    n = len(V)
    if n < 2:
        return np.zeros(n, dtype=int)
    t = 1.0 - cfg.threshold
    order = np.argsort(hours, kind="stable")
    V, hours = V[order], hours[order]
    I, J, W = close_pairs(V, hours, cfg, block_bytes)
    members: list = [[i] for i in range(n)]                       # story id → article rows; merged stories get new ids
    sums: list = [{} for _ in range(n)]                           # story id → {candidate story: sum of distances}
    heap = []
    for i, j, d in zip(I.tolist(), J.tolist(), W.tolist()):
        sums[i][j] = sums[j][i] = d
        heap.append((d, i, j))
    heapq.heapify(heap)

    def total(a: int, x: int) -> float:
        return float(_distances(V, hours, cfg, np.array(members[a]), np.array(members[x])).sum())

    while heap:
        _, a, b = heapq.heappop(heap)
        if members[a] is None or members[b] is None:              # one of them has merged since: a stale entry
            continue
        c = len(members)
        members.append(members[a] + members[b])
        sums.append({})
        for x in sums[a].keys() | sums[b].keys():
            if x == a or x == b:
                continue
            s = (sums[a][x] if x in sums[a] else total(a, x)) + (sums[b][x] if x in sums[b] else total(b, x))
            sums[x].pop(a, None); sums[x].pop(b, None)
            sums[x][c] = sums[c][x] = s
            if s / (len(members[c]) * len(members[x])) <= t:
                heapq.heappush(heap, (s / (len(members[c]) * len(members[x])), c, x))
        members[a] = members[b] = sums[a] = sums[b] = None
    labels = np.empty(n, dtype=int)
    for k, m in enumerate(m for m in members if m is not None):
        labels[order[m]] = k
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
