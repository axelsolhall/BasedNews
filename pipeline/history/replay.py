"""H4 replay: the live pipeline run over the past, on a 7-day window that moves forward one day at a time (A61).

    PYTHONPATH=. uv run python -m pipeline.history.replay --start 2016-03-01 --end 2016-03-14 \
        --embed-model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 --threshold 0.55 --judge simulated
    (on the gaming PC: a stronger local embedding model with its calibrated threshold, --judge local --judge-url ...)

Per day d (resumable; each finished day is recorded in history_progress):
  1. candidates: d's GDELT articles that share people/places/organisations or headline words with another outlet's
     article that day (a cheap pre-filter — it only decides what to fetch; matching is step 4), ≤ CAP per outlet;
  2. fetch: headline + description + publish time from the publisher (pipeline.history.fetch); a page whose date is
     far from GDELT's (a dead link redirected to a section page) is dropped;
  3. vectorize: only the new day's articles ("title\\nsummary", as live embeds a feed article);
  4. stories: live's exact grouping over the window [d-6, d] (pipeline.events.cluster) → a story run for d;
  5. judge: live's candidate pairs and planner (pipeline.measure.run, pipeline.plan.planner) on that run; pairs already
     judged are never bought again (A58); verdicts are stored with purpose "history" and the articles' dates.
The window keeps the last 7 days because stories older than that are never regrouped (live does the same).
Positions over time come from the daily fit over all history verdicts (H5), not from this loop.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from pipeline import store
from pipeline.contracts import Article
from pipeline.events.cluster import ClusterConfig, cluster
from pipeline.history import stories as H2
from pipeline.history.fetch import fetch_many
from pipeline.ingest.feeds import utc_now_iso
from pipeline.settings import load_settings

DB = Path("data/history/history.sqlite")
PURPOSE = "history"
CAP = 20                        # articles fetched per outlet per day, at most (≈ 2.3 days of fetching for 2015→2026)
PREFILTER = 0.2                 # H2 grouping threshold for the pre-filter (recall-oriented: ≈ 0.5 of live's pairs)
MAX_GAP_DAYS = 3                # a page dated further from GDELT's date than this is a redirect, not the article

PROGRESS = """CREATE TABLE IF NOT EXISTS history_progress (
    day TEXT PRIMARY KEY, gdelt INTEGER, candidates INTEGER, fetched INTEGER, stories INTEGER, multi INTEGER,
    calls INTEGER, done_at TEXT)"""


def prefilter(rows: list[dict]) -> list[dict]:
    docs = [H2.entities(r["persons"], r["orgs"], r["places"], r["url"]) for r in rows]
    X, keep = H2.vectors(docs)
    lab = H2.cluster(X, np.flatnonzero(keep), PREFILTER)
    groups = defaultdict(list)
    for i, l in lab.items():
        groups[l].append(i)
    shared = {i: len({rows[j]["outlet"] for j in g}) for g in groups.values() for i in g
              if len({rows[j]["outlet"] for j in g}) >= 2}
    by_outlet = defaultdict(list)
    for i, n in shared.items():
        by_outlet[rows[i]["outlet"]].append((n, i))
    return [rows[i] for v in by_outlet.values() for _, i in sorted(v, reverse=True)[:CAP]]


def article_id(url: str, title: str) -> str:
    return hashlib.sha256(f"{url}|{title}".encode()).hexdigest()[:32]


def store_pages(conn, rows: list[dict], pages: dict) -> list[tuple[str, str]]:
    """Insert fetched pages as articles; returns [(article id, text to vectorize)] for the new ones."""
    new, now = [], utc_now_iso()
    for r in rows:
        p = pages.get(r["url"])
        if not p:
            continue
        seen = r["seen"].replace(tzinfo=dt.timezone.utc) if r["seen"].tzinfo is None else r["seen"]
        if p.published:
            pub = dt.datetime.fromisoformat(p.published.replace("Z", "+00:00"))
            if abs((pub - seen).total_seconds()) > MAX_GAP_DAYS * 86400:
                continue
        published = p.published or seen.strftime("%Y-%m-%dT%H:%M:%SZ")
        a = Article(id=article_id(r["url"], p.title), outlet_id=r["outlet"], country=r["country"], url=r["url"],
                    title=p.title, published=published, summary=p.summary, full_text=None, feed_url="gdelt",
                    ingested_at=now, text_source="history-meta")
        if store.insert_article(conn, a):
            new.append((a.id, f"{p.title}\n{p.summary or ''}".strip()))
    conn.commit()
    return new


def embed_missing(conn, day: dt.date, encoder, model_name: str) -> int:
    """Vectorize the day's stored articles that have no vector for this model yet (prefetched days)."""
    rows = conn.execute("""SELECT a.id, a.title, a.summary FROM articles a LEFT JOIN embeddings e
                           ON e.article_id = a.id AND e.model = ? WHERE e.article_id IS NULL
                           AND a.published >= ? AND a.published < ?""",
                        (model_name, f"{day.isoformat()}T00:00:00Z", f"{(day + dt.timedelta(days=1)).isoformat()}T00:00:00Z")).fetchall()
    if rows:
        store.save_embeddings(conn, model_name, [r["id"] for r in rows],
                              encoder.encode([f"{r['title']}\n{r['summary'] or ''}".strip() for r in rows]))
        conn.commit()
    return len(rows)


def add_day(conn, rows: list[dict], encoder, model_name: str) -> int:
    import time
    t0 = time.monotonic()
    pages = fetch_many([r["url"] for r in rows])
    print(f"   fetched {sum(p is not None for p in pages.values()):,}/{len(rows):,} pages in {time.monotonic() - t0:.0f} s", flush=True)
    new = store_pages(conn, rows, pages)
    if new:
        store.save_embeddings(conn, model_name, [i for i, _ in new], encoder.encode([t for _, t in new]))
        conn.commit()
    return len(new)


def regroup(conn, day: dt.date, model_name: str, threshold: float, window_days: int = 7) -> tuple[str, int, int]:
    start = (day - dt.timedelta(days=window_days - 1)).strftime("%Y-%m-%dT00:00:00Z")
    end = (day + dt.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    rows = conn.execute("SELECT id, published FROM articles WHERE published >= ? AND published < ? ORDER BY published",
                        (start, end)).fetchall()
    ids, V = store.load_embeddings(conn, model_name, [r["id"] for r in rows])
    pos = {i: k for k, i in enumerate(ids)}
    rows = [r for r in rows if r["id"] in pos]
    V = V[[pos[r["id"]] for r in rows]]
    hours = np.array([dt.datetime.fromisoformat(r["published"].replace("Z", "+00:00")).timestamp() / 3600 for r in rows])
    labels = cluster(V, hours, ClusterConfig(method="average", threshold=threshold))
    run_id = f"{day.isoformat()}_history_{threshold}"
    store.save_events(conn, run_id, {r["id"]: f"{day.isoformat()}:ev{l}" for r, l in zip(rows, labels)})
    conn.commit()
    sizes = Counter(labels)
    return run_id, len(sizes), sum(n > 1 for n in sizes.values())


def judge_day(conn, day: dt.date, judge, max_calls: int, rng) -> int:
    from pipeline.judge import prompt as P
    from pipeline.measure.run import best_hyper, candidates, fit, record, relevance_so_far
    from pipeline.plan.planner import PlannerConfig, plan_joint_hier
    from pipeline.sources.registry import load_outlets, reference_groups

    settings = load_settings()
    now = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tzinfo=dt.timezone.utc)
    cands, ctx = candidates(conn, settings, relevance_so_far(conn, PURPOSE), PURPOSE, settle_hours=0, now=now)
    if not cands:
        return 0
    groups = reference_groups(load_outlets())
    outlet_ids = sorted({c.outlet_a for c in cands} | {c.outlet_b for c in cands} | {o for g in groups.values() for o in g})
    groups = {c: {o: w for o, w in g.items() if o in outlet_ids} for c, g in groups.items()}
    posts, _ = fit(conn, outlet_ids, PURPOSE)
    hyper = best_hyper(posts)
    by_key = {(c.event_id, frozenset((c.outlet_a, c.outlet_b))): c for c in cands}
    pool, calls = list(cands), 0
    while calls < max_calls and pool:
        reqs = plan_joint_hier(posts, pool, PlannerConfig(mode="variance_per_cost", target_sd=0.0,
                                                          batch_size=min(10, max_calls - calls)), groups=groups, rng=rng)
        if not reqs:
            break
        for r in reqs:
            c = by_key[(r.event_id, frozenset((r.first_outlet, r.second_outlet)))]
            direction = "positive" if rng.random() < 0.5 else "negative"
            if hasattr(judge, "compare_outlets"):                       # simulated: no text, positions drawn per outlet
                v = judge.compare_outlets(r.first_outlet, r.second_outlet, c.discrimination, direction)
            else:
                v = judge.compare(ctx["texts"][(r.event_id, r.first_article_id)],
                                  ctx["texts"][(r.event_id, r.second_article_id)], direction)
            record(conn, PURPOSE, r, r.event_id.rsplit(":", 1)[1], v, direction, event_id=r.event_id,
                   context={"history_day": day.isoformat(), "story_run": ctx["run"], "prompt_version": P.PROMPT_VERSION})
            calls += 1
            pair = frozenset((r.first_article_id, r.second_article_id))
            pool = [p for p in pool if frozenset((p.article_a, p.article_b)) != pair]
        conn.commit()
        posts, _ = fit(conn, outlet_ids, PURPOSE, hyper)
    return calls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-11-01")
    ap.add_argument("--end", default=dt.date.today().isoformat())
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--embed-model", required=True)
    ap.add_argument("--threshold", type=float, required=True, help="story threshold calibrated for --embed-model")
    ap.add_argument("--judge", choices=["simulated", "local"], default="simulated")
    ap.add_argument("--judge-url", help="OpenAI-compatible endpoint of the local model, e.g. http://gaming-pc:8000/v1")
    ap.add_argument("--judge-model", help="model name the local server serves")
    ap.add_argument("--calls-per-day", type=int, default=60)
    ap.add_argument("--prefetched", action="store_true", help="pages were fetched by pipeline.history.prefetch: only vectorize")
    args = ap.parse_args()

    from pipeline.embed.backend import get_encoder
    from pipeline.sources.registry import load_outlets
    conn = store.connect(args.db)
    conn.execute(PROGRESS)
    conn.execute("CREATE TABLE IF NOT EXISTS same_text_pairs (run_id TEXT, a TEXT, b TEXT, jaccard REAL, PRIMARY KEY (run_id, a, b))")
    encoder = get_encoder(args.embed_model)
    if args.judge == "local":
        from openai import OpenAI

        from pipeline.judge.client import OpenAIJudge
        judge = OpenAIJudge(load_settings(), client=OpenAI(base_url=args.judge_url, api_key="local"), model=args.judge_model)
    else:
        from pipeline.judge.client import SimulatedJudge
        judge = SimulatedJudge(sorted(o.id for o in load_outlets()), seed=0)
    rng = np.random.default_rng(0)
    done = {r["day"] for r in conn.execute("SELECT day FROM history_progress")}
    day, end, year_cache = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end), {}
    while day <= end:
        if day.isoformat() in done:
            day += dt.timedelta(days=1)
            continue
        if day.year not in year_cache:
            year_cache.clear()
            year_cache[day.year] = H2.load_year(day.year)
        rows = year_cache[day.year].get(day, [])
        if args.prefetched:
            cand, fetched = [], embed_missing(conn, day, encoder, args.embed_model)
        else:
            cand = prefilter(rows) if rows else []
            fetched = add_day(conn, cand, encoder, args.embed_model) if cand else 0
        run_id, n_stories, multi = regroup(conn, day, args.embed_model, args.threshold)
        calls = judge_day(conn, day, judge, args.calls_per_day, rng)
        conn.execute("INSERT OR REPLACE INTO history_progress VALUES (?,?,?,?,?,?,?,?)",
                     (day.isoformat(), len(rows), len(cand), fetched, n_stories, multi, calls, utc_now_iso()))
        conn.commit()
        print(f"{day}: GDELT {len(rows):,} → candidates {len(cand):,} → fetched {fetched:,} · window stories {n_stories:,}"
              f" ({multi:,} multi) · judged {calls}", flush=True)
        day += dt.timedelta(days=1)


if __name__ == "__main__":
    main()
