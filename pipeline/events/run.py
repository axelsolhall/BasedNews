"""M5 run: cluster recent embedded articles into events; store membership under a run id.

    uv run --group ml python -m pipeline.events.run [--days 7] [--method average|components] [--threshold 0.55]
"""
from __future__ import annotations

import argparse
import datetime as dt

import numpy as np

from pipeline import store
from pipeline.embed.run import MODEL
from pipeline.events.cluster import ClusterConfig, cluster
from pipeline.ingest.feeds import utc_now_iso


def load_window(conn, days: int, model: str | None = None):
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute("SELECT id, published, outlet_id, country FROM articles WHERE published >= ? "
                        "AND published LIKE '____-__-__T%Z' AND canonical_id IS NULL AND kind IS NULL ORDER BY published",
                        (since,)).fetchall()                    # copies merged by M3 are not separate stories
    ids, V = store.load_embeddings(conn, model or MODEL, [r["id"] for r in rows])
    pos = {i: k for k, i in enumerate(ids)}
    rows = [r for r in rows if r["id"] in pos]
    V = V[[pos[r["id"]] for r in rows]] if rows else V
    hours = np.array([dt.datetime.fromisoformat(r["published"].replace("Z", "+00:00")).timestamp() / 3600 for r in rows])
    return rows, V, hours


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--method", default="average")
    ap.add_argument("--threshold", type=float, default=None, help="default: the embedding profile (A47)")
    args = ap.parse_args()
    conn = store.connect()
    if args.threshold is None:
        from pipeline.embed.profiles import profile
        args.threshold = profile(MODEL)["story"]
    rows, V, hours = load_window(conn, args.days)
    cfg = ClusterConfig(method=args.method, threshold=args.threshold)
    labels = cluster(V, hours, cfg)
    run_id = f"{utc_now_iso()}_{cfg.method}_{cfg.threshold}"
    store.save_events(conn, run_id, {r["id"]: f"ev{l}" for r, l in zip(rows, labels)})
    # same-text pairs across outlets inside one story = known ties, never judged (A44)
    from collections import defaultdict
    from pipeline.events.wire import same_item
    conn.execute("""CREATE TABLE IF NOT EXISTS same_text_pairs (
        run_id TEXT, a TEXT, b TEXT, jaccard REAL, PRIMARY KEY (run_id, a, b))""")
    item = {r["id"]: (r["title"], r["full_text"] or r["summary"]) for r in conn.execute(
        "SELECT id, title, full_text, summary FROM articles WHERE id IN (%s)" % ",".join("?" * len(rows)),
        [r["id"] for r in rows])}
    by_event = defaultdict(list)
    for r, l in zip(rows, labels):
        by_event[l].append(r)
    same = []
    for members in by_event.values():
        for i, x in enumerate(members):
            for y in members[i + 1:]:
                if x["outlet_id"] != y["outlet_id"] and (j := same_item(*item[x["id"]], *item[y["id"]])) is not None:
                    same.append((run_id, *sorted((x["id"], y["id"])), round(j, 3)))
    conn.executemany("INSERT OR REPLACE INTO same_text_pairs VALUES (?,?,?,?)", same)
    conn.commit()
    print(f"same-item pairs — same headline and text, known ties (A44): {len(same)}")
    sizes = np.bincount(labels) if len(labels) else np.array([])
    print(f"run {run_id}: {len(rows)} articles -> {len(sizes)} clusters, {int((sizes >= 2).sum())} with ≥2 articles. "
          f"Run `python -m pipeline.events.eval`.")


if __name__ == "__main__":
    main()
