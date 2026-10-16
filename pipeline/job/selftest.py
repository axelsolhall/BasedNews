"""Smoke test of the job image (ADR-012): CI runs `python -m pipeline.job selftest` on the freshly built arm64 image
before it may become `prod`. It exercises what unit tests on the CI host cannot: the image's own native libraries
(numpy/scipy, lxml under threads, SQLite, Arrow) and every step module's imports — the kinds of failure that took
production down once (2026-10-14: lxml crashed under six ingest threads). No network, no AWS, a few seconds.
"""
from __future__ import annotations

import importlib
import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _check(name, fn, results):
    t0 = time.monotonic()
    try:
        fn()
        results.append({"check": name, "ok": True, "s": round(time.monotonic() - t0, 2)})
    except BaseException as e:                         # noqa: BLE001 — report every failure
        results.append({"check": name, "ok": False, "error": f"{type(e).__name__}: {e}"})


def imports():
    from pipeline.job.steps import STEPS
    for s in STEPS.values():
        if not s.needs_ml:
            importlib.import_module(s.module)


def clustering():
    import numpy as np

    from pipeline.events.cluster import ClusterConfig, average_linkage, average_sparse, cut
    rng = np.random.default_rng(0)
    V = rng.normal(size=(600, 64)).astype(np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    V[300:] = V[:300] + 0.05 * rng.normal(size=(300, 64)).astype(np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    hours = rng.uniform(0, 72, 600)
    cfg = ClusterConfig(method="average", threshold=0.56)
    a, b = average_sparse(V, hours, cfg), cut(average_linkage(V, hours, cfg), 0.56)
    part = lambda l: {frozenset(np.flatnonzero(l == x)) for x in set(l)}   # noqa: E731
    assert part(a) == part(b), "clustering differs from the full tree"


def extraction_threads():
    from pipeline.ingest.extract import text_from_html
    page = "<html><body><article>" + "<p>" + "Words about an event in the city. " * 60 + "</p>" * 5 + "</article></body></html>"
    with ThreadPoolExecutor(8) as pool:                # the production crash: parsing in parallel threads
        out = list(pool.map(lambda _: text_from_html(page), range(64)))
    assert all(t for t, _ in out), "extraction returned nothing"


def sqlite_and_arrow():
    import pyarrow as pa
    import pyarrow.parquet as pq

    from pipeline import store
    d = Path(tempfile.mkdtemp())
    conn = store.connect(d / "t.sqlite")
    assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    pq.write_table(pa.table({"x": list(range(1000))}), d / "t.parquet")
    assert pq.read_table(d / "t.parquet").num_rows == 1000


def lease():
    from pipeline.job.storage import LOCK_KEY, Lease, LocalBucket
    b = LocalBucket(tempfile.mkdtemp())
    a = Lease(b, "selftest-a", heartbeat_s=0.05)
    assert a.acquire(wait_s=0, beat=True)
    time.sleep(0.2)                                     # a few heartbeats
    assert a.still_ours()
    a.release()
    try:
        b.get_bytes(LOCK_KEY)
        raise AssertionError("lease not released")
    except KeyError:
        pass


def main() -> int:
    results = []
    for name, fn in (("imports", imports), ("clustering", clustering), ("extraction in threads", extraction_threads),
                     ("sqlite + arrow", sqlite_and_arrow), ("lease", lease)):
        _check(name, fn, results)
    for r in results:
        print(json.dumps(r))
    ok = all(r["ok"] for r in results)
    print(json.dumps({"selftest": "ok" if ok else "FAILED"}))
    return 0 if ok else 1
