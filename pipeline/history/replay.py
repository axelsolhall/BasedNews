"""H4 replay: the live pipeline run over the past, on a 7-day window that moves forward one day at a time (A61).

    PYTHONPATH=. uv run python -m pipeline.history.replay --start 2016-03-01 --end 2016-03-14 \
        --embed-model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 --threshold 0.55 --judge simulated
    ... --prefetched --count-only            # free dry run: group stories, count the judgeable pairs per day
    PYTHONPATH=. uv run python -m pipeline.history.replay summary [--fraction 0.05]   # pairs per outlet and quarter
    ... --prefetched --judge local --judge-fraction 0.05     # a fixed share: 5 % of each day's new pairs
    ... --prefetched --judge local --target-sd 0.3 --calls-per-day 2000  # A68: until every outlet is that certain

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
  6. positions (A74): yesterday's positions are the prior, today's verdicts update them; every outlet's position is
     written for the day (history_positions) and is what the next day is planned from (pipeline.history.positions).
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
from pipeline.history import positions as HP
from pipeline.history import stories as H2
from pipeline.history.fetch import fetch_many
from pipeline.ingest.feeds import utc_now_iso
from pipeline.settings import load_settings

DB = Path("data/history/history.sqlite")
PURPOSE = "history"
CAP = 20                        # articles fetched per outlet per day, at most (≈ 2.3 days of fetching for 2015→2026)
PREFILTER = 0.2                 # H2 grouping threshold for the pre-filter (recall-oriented: ≈ 0.5 of live's pairs)
MAX_GAP_DAYS = 3                # a page dated further from GDELT's date than this is a redirect, not the article
# The local judge: one of E9's candidates by name (pipeline.experiments.e9.CANDIDATES — base model, LoRA module, and
# whether both orders are read); --judge-candidate picks it, D1/A65. G4-1/3 = Gemma 4 12B with its LoRA (E13, A73).
HISTORY_JUDGE = "G4-1/3"
LOCAL_PAIRS_PER_HOUR = 51_000   # replay-shaped calls on the 5080, 64 in flight, Qwen3-14B-AWQ thinking off (A65,
                                # measured 2026-10-18: 46,000 at 16 in flight); for the projection only
CALLS_PER_DAY = 60              # the daily calls without --judge-fraction (--calls-per-day)
PARALLEL = 64                   # judge calls in flight (A65: 64 beat 16 by 11 %; each answer is ≈ 14 tokens)
HYPER_EVERY_DAYS = 30           # σ_a is re-estimated once a month (A74) and held in between
HYPER_WINDOW_DAYS = 30          # ... over that month's verdicts only, so the fit stays the same size every month
SIGMA_A_START = 0.5             # σ_a before any verdict (the hierarchical fit's default with no data)
WINDOW_DAYS = 91                # A68: with --target-sd, "certain enough" is judged over the last quarter's verdicts
ANGLE_EVERY_DAYS = 7            # A70: the per-pair angle model is relearned weekly from the history's own verdicts
MIN_PER_WEEK = 5                # A69: every outlet with a pair today gets at least this many comparisons in any 7 days,
                                # served first even when they are not the day's most informative picks — an outlet
                                # known well is never so known that it stops being compared (placeholder; D2)

PAIR_COUNTS = """CREATE TABLE IF NOT EXISTS history_pair_counts (
    day TEXT PRIMARY KEY, story_run TEXT, pairs INTEGER, outlets INTEGER, articles INTEGER, by_outlet TEXT, counted_at TEXT,
    new_pairs INTEGER)"""

PROGRESS = """CREATE TABLE IF NOT EXISTS history_progress (
    day TEXT PRIMARY KEY, gdelt INTEGER, candidates INTEGER, fetched INTEGER, stories INTEGER, multi INTEGER,
    calls INTEGER, done_at TEXT)"""


def prepare(conn):
    """The replay's own tables (the history database has live's schema plus these)."""
    conn.execute(PROGRESS)
    conn.execute(PAIR_COUNTS)
    if "new_pairs" not in {r[1] for r in conn.execute("PRAGMA table_info(history_pair_counts)")}:
        conn.execute("ALTER TABLE history_pair_counts ADD COLUMN new_pairs INTEGER")     # tables from before 2026-10-18
    conn.execute("CREATE TABLE IF NOT EXISTS same_text_pairs (run_id TEXT, a TEXT, b TEXT, jaccard REAL, PRIMARY KEY (run_id, a, b))")
    return conn


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


def regroup(conn, day: dt.date, model_name: str, threshold: float, window_days: int = 7,
            vectors=None) -> tuple[str, int, int]:
    """The day's story run over the window [day − 6, day]. Vectors from the database, or from a VectorFile
    (pipeline.history.vectors) when the history's vectors live outside it."""
    start = (day - dt.timedelta(days=window_days - 1)).strftime("%Y-%m-%dT00:00:00Z")
    end = (day + dt.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    rows = conn.execute("SELECT id, published FROM articles WHERE published >= ? AND published < ? ORDER BY published",
                        (start, end)).fetchall()
    ids, V = (vectors.rows_for(conn, [r["id"] for r in rows]) if vectors is not None
              else store.load_embeddings(conn, model_name, [r["id"] for r in rows]))
    pos = {i: k for k, i in enumerate(ids)}
    rows = [r for r in rows if r["id"] in pos]
    V = V[[pos[r["id"]] for r in rows]]
    hours = np.array([dt.datetime.fromisoformat(r["published"].replace("Z", "+00:00")).timestamp() / 3600 for r in rows])
    labels = cluster(V, hours, ClusterConfig(method="average", threshold=threshold))
    run_id = f"{day.isoformat()}_history_{threshold}"
    # only the day's run is kept: nothing reads an old one (verdicts carry their story and articles), and keeping every
    # day's ≈ 12,000 memberships would add ≈ 46 M rows over the eleven years
    conn.execute("DELETE FROM event_members WHERE run_id GLOB '*_history_*' AND run_id != ?", (run_id,))
    store.save_events(conn, run_id, {r["id"]: f"{day.isoformat()}:ev{l}" for r, l in zip(rows, labels)})
    conn.commit()
    sizes = Counter(labels)
    return run_id, len(sizes), sum(n > 1 for n in sizes.values())


def day_run(conn, day: dt.date) -> str | None:
    """The day's own story run — never simply the newest: after a dry run over all days the newest is the last day's."""
    r = conn.execute("SELECT run_id FROM event_members WHERE run_id GLOB ? ORDER BY run_id DESC LIMIT 1",
                     (f"{day.isoformat()}_history_*",)).fetchone()
    return r["run_id"] if r else None


def day_candidates(conn, day: dt.date, run: str | None = None):
    """Live's judgeable pairs on the day's story run (not judged yet, like with like), with their texts."""
    from pipeline.measure.run import candidates, relevance_so_far
    now = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tzinfo=dt.timezone.utc)
    run = run or day_run(conn, day)
    if run is None:
        return [], {"run": None, "texts": {}}
    return candidates(conn, load_settings(), relevance_so_far(conn, PURPOSE), PURPOSE, settle_hours=0, now=now, run=run)


def new_pairs(conn, cands, day: dt.date) -> list:
    """The pool's pairs that are new today: their later article was published on `day`. A pair stays in the pool for
    the whole 7-day window, so a budget on the pool would judge a pair up to seven times over (5 % a day ≈ 30 % of
    pairs); a budget on the new pairs judges that share of all pairs over the run, while the planner still picks from
    the whole pool."""
    ids = list({a for c in cands for a in (c.article_a, c.article_b)})
    pub: dict[str, str] = {}
    for k in range(0, len(ids), 900):
        chunk = ids[k:k + 900]
        pub.update(conn.execute(f"SELECT id, published FROM articles WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall())
    today = day.isoformat()
    return [c for c in cands if max(pub[c.article_a], pub[c.article_b])[:10] == today]


def count_day(conn, day: dt.date, run: str | None = None) -> int:
    """The free dry run: the pairs new today, per outlet, and the whole pool (no judge, nothing marked done)."""
    import json
    cands, ctx = day_candidates(conn, day, run)
    new = new_pairs(conn, cands, day)
    by_outlet = Counter(o for c in new for o in (c.outlet_a, c.outlet_b))
    conn.execute("INSERT OR REPLACE INTO history_pair_counts VALUES (?,?,?,?,?,?,?,?)",
                 (day.isoformat(), ctx.get("run"), len(cands), len(by_outlet),
                  len({a for c in new for a in (c.article_a, c.article_b)}), json.dumps(by_outlet), utc_now_iso(), len(new)))
    conn.commit()
    return len(new)


def budget(n_candidates: int, max_calls: int | None, fraction: float | None) -> int:
    """Calls for the day: a fraction of its new pairs (rounded up), never more than max_calls when one is set;
    without a fraction, max_calls (CALLS_PER_DAY when not set)."""
    if fraction is None:
        return CALLS_PER_DAY if max_calls is None else max_calls
    n = int(np.ceil(fraction * n_candidates))
    return n if max_calls is None else min(max_calls, n)


def week_counts(conn, day: dt.date) -> Counter:
    """Comparisons per outlet over the 7 days ending on `day` (by the articles' dates: the history's time)."""
    since = (day - dt.timedelta(days=6)).isoformat()
    until = (day + dt.timedelta(days=1)).isoformat()
    rows = conn.execute("""SELECT c.first_outlet, c.second_outlet FROM judge_calls c
                           JOIN articles a ON a.id = c.first_article JOIN articles b ON b.id = c.second_article
                           WHERE c.purpose = ? AND max(a.published, b.published) >= ? AND max(a.published, b.published) < ?""",
                        (PURPOSE, since, until)).fetchall()
    return Counter(o for r in rows for o in (r[0], r[1]))


def judge_day(conn, day: dt.date, judge, max_calls: int | None, rng, fraction: float | None = None, hyper: dict | None = None,
              parallel: int = PARALLEL, target_sd: float | None = None, window_days: int = WINDOW_DAYS,
              run: str | None = None, min_per_week: int = MIN_PER_WEEK, angle=None,
              posts: dict | None = None) -> tuple[int, dict | None]:
    """Plan the day's calls once with live's planner, judge them in parallel, record them. Returns (calls, the spreads
    used). One plan per day: within it the planner already applies each pick's expected effect before the next
    (fantasised rank-1 updates), so refitting mid-day bought little and cost one fit per batch at scale. `hyper` holds
    the spreads (τ, σ_a) fixed; None estimates them over the grid (the caller re-estimates monthly).
    The planner knows every verdict so far. min_per_week (A69): first, each outlet below that many comparisons over the
    last 7 days gets its most informative pairs until it is not (more than max_calls if the floor needs it).
    target_sd (A68): then stop once every outlet and country it can reach today is that certain over the last
    `window_days` — max_calls is then only a ceiling. Without it, `fraction` of the day's new pairs (or max_calls).
    angle (A70): candidates → candidates with their own predicted chance of an answer per axis (pipeline.plan.angle).
    posts (A74): today's positions ({axis: HierPosterior}, pipeline.history.positions) — the replay's own; without
    them the day is planned from a fit over the verdicts so far (the tests' small worlds)."""
    from concurrent.futures import ThreadPoolExecutor

    from pipeline.contracts import ComparisonRequest
    from pipeline.judge import prompt as P
    from pipeline.judge.client import JudgeCallFailed
    from pipeline.measure.run import best_hyper, fit, record, record_failure
    from pipeline.plan.planner import PlannerConfig, plan_joint_hier
    from pipeline.sources.registry import load_outlets, reference_groups

    cands, ctx = day_candidates(conn, day, run)
    if angle is not None:
        cands = angle(cands)
    max_calls = budget(len(new_pairs(conn, cands, day)) if fraction is not None else len(cands), max_calls, fraction)
    have = week_counts(conn, day)
    quota = {o: min_per_week - have.get(o, 0) for o in {c.outlet_a for c in cands} | {c.outlet_b for c in cands}
             if have.get(o, 0) < min_per_week}
    if not cands or (max_calls <= 0 and not quota):
        return 0, hyper
    groups = reference_groups(load_outlets())
    if posts is None:
        outlet_ids = sorted({c.outlet_a for c in cands} | {c.outlet_b for c in cands} | {o for g in groups.values() for o in g})
        since = (day - dt.timedelta(days=window_days - 1)).isoformat() if target_sd else None
        posts, _ = fit(conn, outlet_ids, PURPOSE, hyper, since=since)
        hyper = hyper or best_hyper(posts)
    else:
        outlet_ids = posts[next(iter(posts))].outlets
    groups = {c: {o: w for o, w in g.items() if o in set(outlet_ids)} for c, g in groups.items()}
    by_key = {(c.event_id, frozenset((c.outlet_a, c.outlet_b))): c for c in cands}
    cfg = PlannerConfig(mode="variance_per_cost", target_sd=target_sd or 0.0, batch_size=max_calls)
    reqs = plan_joint_hier(posts, cands, cfg, groups=groups, rng=rng, quota=quota)
    items = [(r, by_key[(r.event_id, frozenset((r.first_outlet, r.second_outlet)))],
              "positive" if rng.random() < 0.5 else "negative") for r in reqs]     # drawn in order: reproducible

    def ask(item):
        r, c, direction = item
        try:
            if hasattr(judge, "compare_outlets"):                       # simulated: no text, positions drawn per outlet
                return item, judge.compare_outlets(r.first_outlet, r.second_outlet, c.discrimination, direction,
                                                   story=c.event_id), None
            return item, judge.compare(ctx["texts"][(r.event_id, r.first_article_id)],
                                       ctx["texts"][(r.event_id, r.second_article_id)], direction), None
        except JudgeCallFailed as e:                                    # one bad call never stops eleven years
            return item, None, e

    workers = 1 if hasattr(judge, "compare_outlets") else parallel     # the simulator's random draws are not shared
    with ThreadPoolExecutor(workers) as pool:
        for (r, c, direction), v, err in pool.map(ask, items):
            context = {"history_day": day.isoformat(), "story_run": ctx["run"], "prompt_version": P.PROMPT_VERSION}
            if v is None:
                record_failure(conn, PURPOSE, ComparisonRequest(r.event_id, "both", r.first_outlet, r.second_outlet,
                                                                r.first_article_id, r.second_article_id), err, context)
            else:
                record(conn, PURPOSE, r, r.event_id.rsplit(":", 1)[1], v, direction, event_id=r.event_id, context=context)
    conn.commit()
    return len(items), hyper


def summary(conn, fraction: float) -> str:
    """Pairs per outlet and quarter from the dry run, and what judging a fraction of them takes on the local GPU."""
    import json
    rows = conn.execute("SELECT day, new_pairs, by_outlet FROM history_pair_counts WHERE new_pairs IS NOT NULL "
                        "ORDER BY day").fetchall()
    if not rows:
        return "no counted days yet: run the replay with --count-only first"
    total = sum(r["new_pairs"] for r in rows)
    per_q: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        q = f"{r['day'][:4]}Q{(int(r['day'][5:7]) - 1) // 3 + 1}"
        for o, n in json.loads(r["by_outlet"]).items():
            per_q[(o, q)] += n
    quarters = sorted({q for _, q in per_q})
    outlets = sorted({o for o, _ in per_q})
    cells = np.array([[per_q.get((o, q), 0) for q in quarters] for o in outlets], dtype=float) * fraction
    calls = int(np.ceil(fraction * total))
    lines = [f"{len(rows):,} days counted ({rows[0]['day']} → {rows[-1]['day']}): {total:,} judgeable pairs "
             f"(each counted on the day its later article appeared)",
             f"judging {fraction:.0%}: ≈ {calls:,} calls ≈ {calls / LOCAL_PAIRS_PER_HOUR:,.1f} GPU hours at "
             f"{LOCAL_PAIRS_PER_HOUR:,} an hour (A65)",
             f"verdicts per outlet and quarter at that fraction (each pair counts for both outlets): median "
             f"{np.median(cells):.0f}, 10th percentile {np.percentile(cells, 10):.0f}, cells with none "
             f"{int((cells == 0).sum())} of {cells.size}"]
    thin = sorted(((cells[k] > 0).sum() / len(quarters), o) for k, o in enumerate(outlets))[:5]
    lines.append("outlets with the fewest quarters covered: " + ", ".join(f"{o} {s:.0%}" for s, o in thin))
    return "\n".join(lines)


def open_positions(conn, path: Path) -> tuple[str | None, int, dict]:
    """The positions carried so far (A74), caught up with any verdict recorded after the last save (a run that stopped
    between its commit and its save); a fresh start when nothing has been judged yet."""
    from pipeline.sources.registry import load_outlets
    from pipeline.rate.model import BTConfig
    from pipeline.judge import prompt as P
    last = conn.execute("SELECT max(day) FROM history_progress").fetchone()[0]
    got = HP.load(path)
    if got is None:
        if last is not None:
            raise SystemExit(f"history_progress reaches {last} but {path} is missing: the positions cannot be rebuilt "
                             "from part of the run (A74)")
        outlets = sorted(o.id for o in load_outlets())
        return None, 0, {ax: HP.start(outlets, ax, BTConfig().prior_sd, SIGMA_A_START) for ax in P.AXES}
    day, rowid, states = got
    rowid = advance(conn, states, rowid, dt.date.fromisoformat(last or day))
    return last or day, rowid, states


def advance(conn, states: dict, last_rowid: int, day: dt.date) -> int:
    """The verdicts recorded since last_rowid move the positions; articles out of the next window are dropped; the
    day's positions are written (committed with the day's progress)."""
    from pipeline.measure.run import A_FULL, A_SUMMARY
    rows, last_rowid = HP.new_verdicts(conn, PURPOSE, last_rowid, A_FULL, A_SUMMARY)
    for ax, st in states.items():
        HP.update(st, rows[ax])
        HP.drop_before(st, HP.window_start(day))
    from pipeline.sources.registry import load_outlets, reference_groups
    HP.record_day(conn, day.isoformat(), states, reference_groups(load_outlets()))
    return last_rowid


def spreads(conn, states: dict, day: dt.date) -> None:
    """σ_a (how much an outlet's articles differ) re-estimated over the last HYPER_WINDOW_DAYS of verdicts only — a
    bounded fit; it sets the prior of articles not seen yet. Kept as it was when that span has no verdicts."""
    from pipeline.measure.run import best_hyper, fit
    outlets = next(iter(states.values())).outlets
    since = (day - dt.timedelta(days=HYPER_WINDOW_DAYS)).isoformat()
    posts, n = fit(conn, outlets, PURPOSE, None, since=since)
    if n:
        for ax, (_, sigma_a) in best_hyper(posts).items():
            states[ax].sigma_a = sigma_a


def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "summary":
        ap = argparse.ArgumentParser(prog="replay summary")
        ap.add_argument("summary")
        ap.add_argument("--db", default=str(DB))
        ap.add_argument("--fraction", type=float, default=0.05)
        a = ap.parse_args()
        print(summary(prepare(store.connect(a.db)), a.fraction))
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-11-01")
    ap.add_argument("--end", default=dt.date.today().isoformat())
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--embed-model", required=True)
    ap.add_argument("--threshold", type=float, required=True, help="story threshold calibrated for --embed-model")
    ap.add_argument("--judge", choices=["simulated", "local"], default="simulated",
                    help="local: the server in .env (LOCAL_LLM_BASE_URL/KEY, A65), as --judge-candidate")
    ap.add_argument("--judge-candidate", default=HISTORY_JUDGE,
                    help="with --judge local: an E9 candidate (C1 base, C4 LoRA, C4-S both orders, …)")
    ap.add_argument("--calls-per-day", type=int, help=f"the most calls a day (default: {CALLS_PER_DAY}, or no cap with "
                    "--judge-fraction — a cap there would cut the share on busy days)")
    ap.add_argument("--judge-fraction", type=float, help="judge this share of each day's pairs (≤ --calls-per-day)")
    ap.add_argument("--target-sd", type=float, help="A68: judge until every outlet's sd is below this "
                                                    "(≤ --calls-per-day); instead of --judge-fraction")
    ap.add_argument("--no-angle", action="store_true", help="A70 off: one average chance of an answer per axis")
    ap.add_argument("--min-per-week", type=int, default=MIN_PER_WEEK,
                    help="A69: comparisons every outlet gets in any 7 days, before the most informative picks")
    ap.add_argument("--count-only", action="store_true", help="group and count pairs only: no judge, nothing marked done")
    ap.add_argument("--prefetched", action="store_true", help="pages were fetched by pipeline.history.prefetch: only vectorize")
    ap.add_argument("--vectors", type=Path, help="vectors computed ahead, outside the database (pipeline.history.vectors)")
    args = ap.parse_args()
    if args.target_sd and args.judge_fraction:
        ap.error("--target-sd or --judge-fraction, not both")

    from pipeline.embed.backend import get_encoder
    from pipeline.sources.registry import load_outlets
    conn = prepare(store.connect(args.db))
    vectors = None
    if args.vectors:
        from pipeline.history.vectors import VectorFile
        vectors, encoder = VectorFile(args.vectors, args.embed_model), None       # nothing to embed on the way
    else:
        encoder = get_encoder(args.embed_model)
    if args.judge == "local":
        from pipeline.experiments.e9 import CANDIDATES
        from pipeline.experiments.e9 import LocalJudge as candidate_judge
        from pipeline.judge.client import local_client
        if args.judge_candidate not in CANDIDATES:
            raise SystemExit(f"--judge-candidate: one of {', '.join(CANDIDATES)}")
        judge = candidate_judge(local_client(), CANDIDATES[args.judge_candidate])
        print(f"judge: {args.judge_candidate} ({CANDIDATES[args.judge_candidate].get('served', 'judge')})", flush=True)
    else:
        from pipeline.judge.client import LIKE_LIVE, SimulatedJudge          # needs what the real judge would
        judge = SimulatedJudge(sorted(o.id for o in load_outlets()), seed=0, **LIKE_LIVE)
    rng = np.random.default_rng(0)
    from pipeline.plan import angle as AN
    vec_of = ((lambda ids: vectors.rows_for(conn, ids)) if vectors is not None
              else (lambda ids: store.load_embeddings(conn, args.embed_model, ids)))
    angle_model, angle_day = None, None
    done = {r["day"] for r in conn.execute("SELECT day FROM history_progress")}
    if args.count_only:
        done = {r["day"] for r in conn.execute("SELECT day FROM history_pair_counts")}
    day, end, year_cache = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end), {}
    if not args.count_only:
        state_path = Path(args.db).with_name("positions.npz")
        last_day, last_rowid, states = open_positions(conn, state_path)
        hyper_day = None
    while day <= end:
        if day.isoformat() in done:
            day += dt.timedelta(days=1)
            continue
        if day.year not in year_cache:
            year_cache.clear()
            year_cache[day.year] = H2.load_year(day.year)
        rows = year_cache[day.year].get(day, [])
        if args.prefetched:
            cand, fetched = [], (0 if vectors is not None else embed_missing(conn, day, encoder, args.embed_model))
        else:
            cand = prefilter(rows) if rows else []
            fetched = add_day(conn, cand, encoder, args.embed_model) if cand else 0
        run_id, n_stories, multi = regroup(conn, day, args.embed_model, args.threshold, vectors=vectors)
        if args.count_only:
            print(f"{day}: window stories {n_stories:,} ({multi:,} multi) · new judgeable pairs "
                  f"{count_day(conn, day, run_id):,}", flush=True)
            day += dt.timedelta(days=1)
            continue
        if last_day is not None and day.isoformat() <= last_day:
            raise SystemExit(f"{day}: the positions are already at {last_day} and only move forward (A74)")
        if hyper_day is None or (day - hyper_day).days >= HYPER_EVERY_DAYS:
            spreads(conn, states, day)                                      # σ_a from the last month's verdicts
            hyper_day = day
        if not args.no_angle and (angle_day is None or (day - angle_day).days >= ANGLE_EVERY_DAYS):
            angle_model, angle_day = AN.learn(conn, PURPOSE, vec_of), day     # None until MIN_LABELS verdicts
        calls, _ = judge_day(conn, day, judge, args.calls_per_day, rng, args.judge_fraction,
                             target_sd=args.target_sd, run=run_id, min_per_week=args.min_per_week,
                             angle=None if angle_model is None else (lambda cs: AN.apply(cs, angle_model, vec_of)),
                             posts={ax: st.posterior() for ax, st in states.items()})
        last_rowid = advance(conn, states, last_rowid, day)                 # today's verdicts move the positions
        conn.execute("INSERT OR REPLACE INTO history_progress VALUES (?,?,?,?,?,?,?,?)",
                     (day.isoformat(), len(rows), len(cand), fetched, n_stories, multi, calls, utc_now_iso()))
        conn.commit()
        HP.save(state_path, day.isoformat(), last_rowid, states)
        last_day = day.isoformat()
        print(f"{day}: GDELT {len(rows):,} → candidates {len(cand):,} → fetched {fetched:,} · window stories {n_stories:,}"
              f" ({multi:,} multi) · judged {calls}", flush=True)
        day += dt.timedelta(days=1)


if __name__ == "__main__":
    main()
