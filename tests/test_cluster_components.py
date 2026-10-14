"""average_by_components must give exactly the partition of the full average-linkage tree cut at the threshold
(it replaces the n² distance array so 41 countries fit in memory, A61)."""
import numpy as np
import pytest

from pipeline.events.cluster import ClusterConfig, average_by_components, average_linkage, cut


def partition(labels):
    groups = {}
    for i, l in enumerate(labels):
        groups.setdefault(l, set()).add(i)
    return {frozenset(g) for g in groups.values()}


def newsish(seed, n=700, stories=60, dim=64):
    rng = np.random.default_rng(seed)
    centres = rng.normal(size=(stories, dim))
    which = rng.integers(0, stories, n)
    V = centres[which] + rng.normal(scale=rng.uniform(0.4, 1.4), size=(n, dim))   # tight to loose stories
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    hours = rng.uniform(0, 168, stories)[which] + rng.normal(scale=12, size=n)        # a week, stories spread over hours
    return V.astype(np.float32), hours


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("threshold", [0.45, 0.56, 0.7])
def test_same_partition_as_the_full_tree(seed, threshold):
    V, hours = newsish(seed)
    cfg = ClusterConfig(method="average", threshold=threshold)
    full = partition(cut(average_linkage(V, hours, cfg), threshold))
    split = partition(average_by_components(V, hours, cfg, block=128))
    assert len(full) > 10                                    # a real test: many stories, not one blob or all singletons
    assert split == full


def test_the_check_would_catch_a_different_partition():
    V, hours = newsish(0)
    cfg = ClusterConfig(method="average", threshold=0.56)
    full = partition(cut(average_linkage(V, hours, cfg), 0.56))
    other = partition(average_by_components(V, hours, ClusterConfig(method="average", threshold=0.5)))
    assert other != full
