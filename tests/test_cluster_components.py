"""average_sparse must give exactly the partition of the full average-linkage tree cut at the threshold
(it replaces the n² distance array so a full 41-country window fits in memory, A61)."""
import numpy as np
import pytest

from pipeline.events.cluster import ClusterConfig, average_linkage, average_sparse, close_pairs, cut


def partition(labels):
    groups = {}
    for i, l in enumerate(labels):
        groups.setdefault(l, set()).add(i)
    return {frozenset(g) for g in groups.values()}


def newsish(seed, n=700, stories=60, dim=64, span_h=168):
    rng = np.random.default_rng(seed)
    centres = rng.normal(size=(stories, dim))
    which = rng.integers(0, stories, n)
    V = centres[which] + rng.normal(scale=rng.uniform(0.4, 1.4), size=(n, dim))   # tight to loose stories
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    hours = rng.uniform(0, span_h, stories)[which] + rng.normal(scale=12, size=n)  # stories spread over hours, unsorted
    return V.astype(np.float32), hours


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
@pytest.mark.parametrize("threshold", [0.45, 0.56, 0.7])
def test_same_partition_as_the_full_tree(seed, threshold):
    V, hours = newsish(seed)
    cfg = ClusterConfig(method="average", threshold=threshold)
    full = partition(cut(average_linkage(V, hours, cfg), threshold))
    sparse = partition(average_sparse(V, hours, cfg, block_bytes=37 * 700 * 16))   # 16-row blocks: many block edges
    assert len(full) > 10                                    # a real test: many stories, not one blob or all singletons
    assert sparse == full


def test_duplicates_and_one_long_chain():
    """Identical vectors (wire copies, distance 0, ties) and a chain linking everything, the shape of the real window."""
    rng = np.random.default_rng(7)
    base = rng.normal(size=(40, 32))
    walk = np.cumsum(rng.normal(scale=0.35, size=(400, 32)), axis=0) + base[rng.integers(0, 40, 400)]
    V = np.vstack([walk, walk[:80]])                         # 80 exact duplicates
    V = (V / np.linalg.norm(V, axis=1, keepdims=True)).astype(np.float32)
    hours = np.concatenate([np.linspace(0, 160, 400), np.linspace(0, 32, 80)])
    cfg = ClusterConfig(method="average", threshold=0.56)
    assert partition(average_sparse(V, hours, cfg)) == partition(cut(average_linkage(V, hours, cfg), 0.56))


def test_close_pairs_skip_only_pairs_outside_the_time_window():
    V, hours = newsish(1, span_h=400)                        # spread wider than window_h, so the band matters
    cfg = ClusterConfig(method="average", threshold=0.56)
    order = np.argsort(hours)
    I, J, _ = close_pairs(V[order], hours[order], cfg, block_bytes=37 * 700 * 8)
    D = 1 - (V[order] @ V[order].T) * np.exp(-np.abs(hours[order][:, None] - hours[order][None, :]) / cfg.decay_h)
    D[np.abs(hours[order][:, None] - hours[order][None, :]) > cfg.window_h] = 2
    want = {(i, j) for i, j in zip(*np.nonzero(np.triu(D <= 1 - cfg.threshold, 1)))}
    assert set(zip(I.tolist(), J.tolist())) == want and len(want) > 100


def test_the_check_would_catch_a_different_partition():
    V, hours = newsish(0)
    cfg = ClusterConfig(method="average", threshold=0.56)
    full = partition(cut(average_linkage(V, hours, cfg), 0.56))
    other = partition(average_sparse(V, hours, ClusterConfig(method="average", threshold=0.5)))
    assert other != full
