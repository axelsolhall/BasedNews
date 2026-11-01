"""H4 replay: the live pipeline run over the past, on a 7-day window that moves forward one day at a time (A61).

    PYTHONPATH=. uv run python -m pipeline.history.replay --start 2016-03-01 --end 2016-03-14 \
        --embed-model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 --threshold 0.55 --judge simulated
    ... --prefetched --count-only            # free dry run: group stories, count the judgeable pairs per day
    PYTHONPATH=. uv run python -m pipeline.history.replay summary [--fraction 0.05]   # pairs per outlet and quarter
    ... --prefetched --judge local --judge-fraction 0.05     # a fixed share: 5 % of each day's new pairs
    ... --prefetched --judge local --target-sd 0.3 --calls-per-day 2000  # A68: until every outlet is that certain
    PYTHONPATH=. uv run python -m pipeline.history.replay recompute   # A74: the judged days' positions, from the verdicts

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
import os
import sqlite3
import threading
import time
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
    new_pairs INTEGER, by_country TEXT)"""

PROGRESS = """CREATE TABLE IF NOT EXISTS history_progress (
    day TEXT PRIMARY KEY, gdelt INTEGER, candidates INTEGER, fetched INTEGER, stories INTEGER, multi INTEGER,
    calls INTEGER, done_at TEXT)"""


def prepare(conn):
    """The replay's own tables (the history database has live's schema plus these), and the indexes its queries need
    on judge_calls (by purpose; by the day judged for, which starts the event id)."""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_judge_calls_purpose ON judge_calls(purpose, prompt_version)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_judge_calls_day ON judge_calls(purpose, substr(event_id, 1, 10))")
    conn.execute(PROGRESS)
    conn.execute(PAIR_COUNTS)
    if "new_pairs" not in {r[1] for r in conn.execute("PRAGMA table_info(history_pair_counts)")}:
        conn.execute("ALTER TABLE history_pair_counts ADD COLUMN new_pairs INTEGER")     # tables from before 2026-10-18
    if "by_country" not in {r[1] for r in conn.execute("PRAGMA table_info(history_pair_counts)")}:
        conn.execute("ALTER TABLE history_pair_counts ADD COLUMN by_country TEXT")       # tables from before 2026-10-21
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


def store_pages(conn, rows: list[dict], pages: dict, source: str = "gdelt",
                junk: set | None = None) -> list[tuple[str, str]]:
    """Insert fetched pages as articles; returns [(article id, text to vectorize)] for the new ones. source: where the
    page came from, kept in feed_url ("gdelt": the publisher; the backfill's archive copies say so, A75). A page that
    is not an article (a bot wall, a section page: quality.kept, A76; junk: the titles known to be such) is not
    stored."""
    from pipeline.history import quality
    new, now = [], utc_now_iso()
    ok = quality.kept([(r["url"], r["outlet"], pages.get(r["url"])) for r in rows], junk)
    for r in rows:
        p = pages.get(r["url"])
        if not p or r["url"] not in ok:
            continue
        seen = r["seen"].replace(tzinfo=dt.timezone.utc) if r["seen"].tzinfo is None else r["seen"]
        if p.published:
            pub = dt.datetime.fromisoformat(p.published.replace("Z", "+00:00"))
            if abs((pub - seen).total_seconds()) > MAX_GAP_DAYS * 86400:
                continue
        published = p.published or seen.strftime("%Y-%m-%dT%H:%M:%SZ")
        a = Article(id=article_id(r["url"], p.title), outlet_id=r["outlet"], country=r["country"], url=r["url"],
                    title=p.title, published=published, summary=p.summary, full_text=None, feed_url=source,
                    ingested_at=now, text_source="history-meta")
        if store.insert_article(conn, a):
            new.append((a.id, f"{p.title}\n{p.summary or ''}".strip()))
    conn.commit()
    return new


def embed_missing(conn, day: dt.date, encoder, model_name: str) -> int:
    """Vectorize the day's stored articles that have no vector for this model yet (prefetched days)."""
    rows = conn.execute("""SELECT a.id, a.title, a.summary FROM articles a LEFT JOIN embeddings e
                           ON e.article_id = a.id AND e.model = ? WHERE e.article_id IS NULL
                           AND a.published >= ? AND a.published < ? AND a.kind IS NULL""",
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


_my_runs: set[str] = set()       # the story run this process saved last (regroup drops it when it saves the next)


def regroup(conn, day: dt.date, model_name: str, threshold: float, window_days: int = 7,
            vectors=None, keep: bool = False) -> tuple[str, int, int]:
    """The day's story run over the window [day − 6, day]. Vectors from the database, or from a VectorFile
    (pipeline.history.vectors) when the history's vectors live outside it. keep: leave this process's earlier runs
    (grouping ahead of the judge, which deletes each run once its day is judged: --ahead / --grouped)."""
    start = (day - dt.timedelta(days=window_days - 1)).strftime("%Y-%m-%dT00:00:00Z")
    end = (day + dt.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    rows = conn.execute("SELECT id, published FROM articles WHERE published >= ? AND published < ? "
                        "AND kind IS NULL ORDER BY published", (start, end)).fetchall()        # A76: not tagged junk
    ids, V = (vectors.rows_for(conn, [r["id"] for r in rows]) if vectors is not None
              else store.load_embeddings(conn, model_name, [r["id"] for r in rows]))
    pos = {i: k for k, i in enumerate(ids)}
    rows = [r for r in rows if r["id"] in pos]
    V = V[[pos[r["id"]] for r in rows]]
    hours = np.array([dt.datetime.fromisoformat(r["published"].replace("Z", "+00:00")).timestamp() / 3600 for r in rows])
    labels = cluster(V, hours, ClusterConfig(method="average", threshold=threshold))
    run_id = f"{day.isoformat()}_history_{threshold}"
    # only the day's run is kept: nothing reads an old one (verdicts carry their story and articles), and keeping every
    # day's ≈ 12,000 memberships would add ≈ 46 M rows over the eleven years. Only this process's own earlier run is
    # dropped: dry runs over different years run side by side, and each would delete the run the other is counting.
    for old in (set() if keep else _my_runs - {run_id}):
        conn.execute("DELETE FROM event_members WHERE run_id = ?", (old,))
    conn.execute("DELETE FROM event_members WHERE run_id = ?", (run_id,))          # a day grouped again: no stale rows
    store.save_events(conn, run_id, {r["id"]: f"{day.isoformat()}:ev{l}" for r, l in zip(rows, labels)})
    conn.commit()
    if not keep:
        _my_runs.clear()
        _my_runs.add(run_id)
    sizes = Counter(labels)
    return run_id, len(sizes), sum(n > 1 for n in sizes.values())


def wait_judged(conn, day: dt.date, first: dt.date, ahead: int) -> None:
    """Grouping ahead (--ahead): wait until the judge is at most `ahead` days behind `day`, so the story runs kept for
    it stay few (≈ 12,000 memberships a day)."""
    said = False
    while True:
        r = conn.execute("SELECT max(day) FROM history_progress").fetchone()[0]
        behind = (day - (dt.date.fromisoformat(r) if r else first - dt.timedelta(days=1))).days - 1
        if behind <= ahead:
            return
        if not said:
            print(f"{day}: waiting for the judge ({behind} days behind)", flush=True)
            said = True
        time.sleep(20)


def wait_grouped(conn, day: dt.date, since: str) -> tuple[str | None, int, int]:
    """Judging behind a grouping process (--grouped): wait for the day's story run, grouped and counted at or after
    `since` (the vectors it must include), and return it as regroup does."""
    said = False
    while True:
        r = conn.execute("SELECT story_run, counted_at FROM history_pair_counts WHERE day = ?", (day.isoformat(),)).fetchone()
        if r and r["counted_at"] >= since:
            run = r["story_run"]
            if run is None:
                return None, 0, 0
            n, multi = conn.execute("SELECT count(*), coalesce(sum(k > 1), 0) FROM (SELECT count(*) k FROM event_members "
                                    "WHERE run_id = ? GROUP BY event_id)", (run,)).fetchone()
            return run, n, multi
        if not said:
            print(f"{day}: waiting for its stories", flush=True)
            said = True
        conn.commit()                                   # end the read, so the next one sees the grouping's writes
        time.sleep(20)


def prepare_day(db, day: dt.date, since: str, vectors=None):
    """The next day's candidates, loaded while today is judged (its own connection, beside the judge): its story run
    (from the grouping process, --grouped) and every pair of it — only what today's verdicts change is left out (the
    pairs judged by then and the answer shares: finish_candidates adds them when the day starts). ≈ 7 s of a day that
    the GPU otherwise waited for (2026-10-24). With them, what only the articles decide (the maintainer, 2026-10-24,
    "option a"): their publication times (the day's budget counts the new pairs) and each pair's mean vector (the
    answer-share model, A70). Returns (run, stories, multi, (candidates, context) or None)."""
    from pipeline.measure.run import candidates
    from pipeline.plan import angle as AN
    conn = store.connect(db)
    try:
        run, n, multi = wait_grouped(conn, day, since)
        if run is None:
            return run, n, multi, None
        now = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tzinfo=dt.timezone.utc)
        cands, ctx = candidates(conn, load_settings(), {"econ": 1.0, "social": 1.0}, PURPOSE, settle_hours=0, now=now,
                                run=run, exclude_judged=False)
        ids, pub = sorted({a for c in cands for a in (c.article_a, c.article_b)}), {}
        for k in range(0, len(ids), 900):
            chunk = ids[k:k + 900]
            pub.update(conn.execute(f"SELECT id, published FROM articles WHERE id IN ({','.join('?' * len(chunk))})",
                                    chunk).fetchall())
        ctx["_pub"] = pub
        if vectors is not None:
            ctx["_pairs"] = AN.pair_vectors([(c.article_a, c.article_b) for c in cands],
                                            lambda i: vectors.rows_for(conn, i))
        return run, n, multi, (cands, ctx)
    finally:
        conn.close()


class _SoFar:
    """judged_pairs and relevance_so_far for the history run, kept as running totals instead of re-read from every call
    each day (≈ 11–17 s a day once two years were judged, 2026-10-25): the calls recorded since the last look (by
    rowid) are added. Same sets and shares as measure.run's functions."""

    def __init__(self):
        self.rowid, self.pairs, self.n, self.ans, self.conn = 0, set(), Counter(), Counter(), None

    def update(self, conn):
        from pipeline.judge import prompt as P
        last = self.rowid
        for rid, a, b in conn.execute("SELECT rowid, first_article, second_article FROM judge_calls WHERE purpose = ? "
                                      "AND prompt_version = ? AND rowid > ? ORDER BY rowid", (PURPOSE, P.PROMPT_VERSION, last)):
            self.pairs.add(frozenset((a, b)))
            self.rowid = rid
        for ax, n, ans in conn.execute("SELECT j.axis, count(*), sum(j.verdict != 'na') FROM judgments j JOIN judge_calls c "
                                       "ON c.call_id = j.call_id WHERE c.purpose = ? AND c.rowid > ? AND c.rowid <= ? "
                                       "GROUP BY j.axis", (PURPOSE, last, self.rowid)):
            self.n[ax] += n
            self.ans[ax] += ans or 0

    def relevance(self) -> dict:
        from pipeline.measure.run import PRIOR_RELEVANCE
        return {ax: (self.ans[ax] + 10 * PRIOR_RELEVANCE[ax]) / (self.n[ax] + 10) for ax in ("econ", "social")}


_so_far: _SoFar | None = None


def finish_candidates(conn, prepared):
    """prepare_day's candidates as day_candidates gives them: without the pairs judged so far, with the answer shares
    so far (same pairs, same order)."""
    from dataclasses import replace
    global _so_far
    cands, ctx = prepared
    if _so_far is None or _so_far.conn is not conn:                     # one per connection (the run has one)
        _so_far = _SoFar()
        _so_far.conn = conn
    _so_far.update(conn)
    done, rel = _so_far.pairs, _so_far.relevance()
    r = (rel["econ"], rel["social"])
    keep = [k for k, c in enumerate(cands) if frozenset((c.article_a, c.article_b)) not in done]
    if "_pairs" in ctx:                                      # the pairs' vectors, cut to the kept candidates
        X, ok = ctx["_pairs"]
        row, kk = np.cumsum(ok) - 1, np.array(keep, dtype=int)
        ctx = {**ctx, "_pairs": (X[row[kk][ok[kk]]] if len(kk) else X[:0], ok[kk])}
    return [replace(cands[k], relevance=r) for k in keep], ctx


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


def new_pairs(conn, cands, day: dt.date, pub: dict | None = None) -> list:
    """The pool's pairs that are new today: their later article was published on `day`. A pair stays in the pool for
    the whole 7-day window, so a budget on the pool would judge a pair up to seven times over (5 % a day ≈ 30 % of
    pairs); a budget on the new pairs judges that share of all pairs over the run, while the planner still picks from
    the whole pool."""
    ids = [] if pub is not None else list({a for c in cands for a in (c.article_a, c.article_b)})
    pub = dict(pub or {})
    for k in range(0, len(ids), 900):
        chunk = ids[k:k + 900]
        pub.update(conn.execute(f"SELECT id, published FROM articles WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall())
    today = day.isoformat()
    return [c for c in cands if max(pub[c.article_a], pub[c.article_b])[:10] == today]


def count_day(conn, day: dt.date, run: str | None = None) -> int:
    """The free dry run: the pairs new today, per outlet and per country (a pair counts once for each country it
    touches: two outlets of one country are one pair for it — the site's weekly chart, A77), and the whole pool (no
    judge, nothing marked done)."""
    import json
    from pipeline.sources.registry import load_outlets
    cands, ctx = day_candidates(conn, day, run)
    new = new_pairs(conn, cands, day)
    by_outlet = Counter(o for c in new for o in (c.outlet_a, c.outlet_b))
    country = {o.id: o.country for o in load_outlets()}
    by_country = Counter(k for c in new for k in {country.get(c.outlet_a), country.get(c.outlet_b)} - {None})
    conn.execute("INSERT OR REPLACE INTO history_pair_counts VALUES (?,?,?,?,?,?,?,?,?)",
                 (day.isoformat(), ctx.get("run"), len(cands), len(by_outlet),
                  len({a for c in new for a in (c.article_a, c.article_b)}), json.dumps(by_outlet), utc_now_iso(), len(new),
                  json.dumps(by_country)))
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
    # a pair judged on day j has its later article in [j − 6, j]: only calls judged since `since` can count, so the rest
    # are skipped before the joins (same counts; ≈ 11 s → 1 s a day once months were judged, 2026-10-24)
    rows = conn.execute("""SELECT c.first_outlet, c.second_outlet FROM judge_calls c
                           JOIN articles a ON a.id = c.first_article JOIN articles b ON b.id = c.second_article
                           WHERE c.purpose = ? AND substr(c.event_id, 1, 10) >= ?
                           AND max(a.published, b.published) >= ? AND max(a.published, b.published) < ?""",
                        (PURPOSE, since, since, until)).fetchall()
    return Counter(o for r in rows for o in (r[0], r[1]))


CALL_TIMEOUT = 60                                       # seconds for one judge request
WATCHDOG = 300                                          # no day finished this long: every thread's stack to the log
UNREACHABLE = ("Connection", "Connect", "Timeout")     # error types that say the server was not reached
UNREACHABLE_WAIT = 30                                   # seconds before asking an unreachable server again
WAITING = threading.Event()                             # said once that the server is unreachable


def unreachable(e) -> bool:
    """A call whose every attempt failed to reach the server (the PC off or asleep, 2026-10-23: 52,244 calls recorded as
    failed and 98 days judged without verdicts): not the call's fault, so it is asked again, never recorded."""
    errs = (getattr(e, "log", None) or {}).get("errors") or []
    return bool(errs) and all(any(k in (x.get("type") or "") for k in UNREACHABLE) for x in errs)


BLOCK_MAX = 100                                         # the most picks from one scoring (the maintainer, 2026-10-24)


class Feed:
    """How many picks the planner takes from one scoring of all candidates (the maintainer, 2026-10-24: rescore as
    often as the GPU allows — it must stay busy — and at least every BLOCK_MAX picks). The GPU is busy while `parallel`
    calls wait at it; the picks taken now must cover what it answers while the next scoring runs:
    k = parallel + rate × (time per scoring) − calls waiting, between 1 and BLOCK_MAX."""

    def __init__(self, parallel: int, most: int = BLOCK_MAX, queue=None):
        self.parallel, self.most, self.queue = parallel, most, queue       # queue(): the server's own count, or None
        self.lock, self.waiting, self.done = threading.Lock(), 0, 0
        self.t0, self.last, self.scorings, self.sizes = time.monotonic(), None, 0, []
        self.picked, self.marks = 0, []                 # picks so far; where each scoring's block starts

    def sent(self):
        with self.lock:
            self.waiting += 1

    def answered(self):
        with self.lock:
            self.waiting -= 1
            self.done += 1

    def __call__(self) -> int:
        now = time.monotonic()
        per_scoring = (now - self.last) if self.last is not None else 0.0
        self.last = now
        held = self.queue() if self.queue is not None else None            # what the GPU really has (2026-10-25:
        with self.lock:                                                    # calls handed to the sending threads sat
            rate = self.done / max(now - self.t0, 1e-3)                    # on the Mac while the planner held it)
            k = int(np.ceil(self.parallel + rate * per_scoring - (self.waiting if held is None else held)))
        k = max(1, min(self.most, k))
        self.marks.append(self.picked)
        self.scorings += 1
        self.sizes.append(k)
        return k


SERVER_QUEUE = None                                     # set by main for the local judge: server_queue(...)


def server_queue(base_url: str):
    """A reader of the judge server's own queue — requests running plus waiting, from vLLM's /metrics — for Feed, or
    None when the server cannot be read (Feed then counts the calls it sent)."""
    import re
    import urllib.request
    url = re.sub(r"/v1/?$", "", base_url.rstrip("/")) + "/metrics"
    pat = re.compile(r"^vllm:num_requests_(?:running|waiting)\{[^}]*\} ([0-9.]+)", re.M)

    def read() -> float | None:
        try:
            text = urllib.request.urlopen(url, timeout=0.5).read().decode()
        except Exception:                                   # noqa: BLE001 — unreadable: fall back, never stop
            return None
        got = pat.findall(text)
        return sum(float(v) for v in got) if got else None
    return read


TIMES: dict[str, float] = {}     # the last judged day's seconds per phase (the day's log line: is the GPU kept busy?)


def judge_day(conn, day: dt.date, judge, max_calls: int | None, rng, fraction: float | None = None, hyper: dict | None = None,
              parallel: int = PARALLEL, target_sd: float | None = None, window_days: int = WINDOW_DAYS,
              run: str | None = None, min_per_week: int = MIN_PER_WEEK, angle=None,
              posts: dict | None = None, prepared=None, pool=None, carry: list | None = None,
              keep_last: bool = False) -> tuple[int, dict | None]:
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

    from pipeline.judge import prompt as P
    from pipeline.judge.client import JudgeCallFailed
    from pipeline.measure.run import best_hyper, fit
    from pipeline.plan.planner import PlannerConfig, iter_plan_independent
    from pipeline.sources.registry import load_outlets, reference_groups

    t0 = time.monotonic()
    cands, ctx = finish_candidates(conn, prepared) if prepared is not None else day_candidates(conn, day, run)
    step = {"candidates": time.monotonic() - t0}
    if angle is not None:
        cands = angle(cands, ctx["_pairs"]) if "_pairs" in ctx else angle(cands)
    # the maintainer, 2026-10-24: the previous day's last block of calls ran while this day was prepared (keep_last);
    # its pairs are not offered again, and its verdicts are recorded now — after the positions this day is planned
    # from, so they first move the positions tonight and so the plan of the day after (n → n + 2)
    pending = {frozenset((it[0].first_article_id, it[0].second_article_id)) for it, _, _ in (carry or [])}
    if pending:
        cands = [c for c in cands if frozenset((c.article_a, c.article_b)) not in pending]
    step["answer shares"] = time.monotonic() - t0 - sum(step.values())
    max_calls = budget(len(new_pairs(conn, cands, day, ctx.get("_pub"))) if fraction is not None else len(cands),
                       max_calls, fraction)
    step["budget"] = time.monotonic() - t0 - sum(step.values())
    for item, fut, context in (carry or []):
        _record(conn, *fut.result(), context)
    if carry:
        conn.commit()
        carry.clear()
    step["carried recorded"] = time.monotonic() - t0 - sum(step.values())
    have = week_counts(conn, day)
    step["week counts"] = time.monotonic() - t0 - sum(step.values())
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
    feed = None if hasattr(judge, "compare_outlets") else Feed(parallel, queue=SERVER_QUEUE)   # simulator: a scoring per pick
    # the maintainer, 2026-10-25: each outlet's own score and uncertainty; a comparison sharpens only its two outlets
    picks = iter_plan_independent(posts, cands, cfg, rng=rng, quota=quota)

    def ask(item):
        try:
            return _ask(item)
        finally:
            if feed is not None:
                feed.answered()

    def _ask(item):
        r, c, direction = item
        while True:
            try:
                if hasattr(judge, "compare_outlets"):                   # simulated: no text, positions drawn per outlet
                    return item, judge.compare_outlets(r.first_outlet, r.second_outlet, c.discrimination, direction,
                                                       story=c.event_id), None
                return item, judge.compare(ctx["texts"][(r.event_id, r.first_article_id)],
                                           ctx["texts"][(r.event_id, r.second_article_id)], direction), None
            except JudgeCallFailed as e:                                # one bad call never stops eleven years —
                if not unreachable(e):                                  # but a server that does not answer is not a
                    return item, None, e                                # bad call: wait for it and ask again
                if not WAITING.is_set():
                    WAITING.set()
                    print(f"{day}: the judge server does not answer ({e.log['errors'][-1]['type']}): waiting", flush=True)
                time.sleep(UNREACHABLE_WAIT)

    workers = 1 if hasattr(judge, "compare_outlets") else parallel     # the simulator's random draws are not shared
    own = pool is None
    pool = pool or ThreadPoolExecutor(workers)
    try:
        # each pick is asked as soon as it is planned: the GPU judges the first while the planner picks the rest
        # (the GPU waited for the whole day's plan, ≈ 60 % of the time, 2026-10-23). Same picks, same order of draws,
        # recorded in pick order
        items, asked = [], []
        step["set-up"] = time.monotonic() - t0 - sum(step.values())
        for r in picks:
            item = (r, by_key[(r.event_id, frozenset((r.first_outlet, r.second_outlet)))],
                    "positive" if rng.random() < 0.5 else "negative")      # drawn in order: reproducible
            items.append(item)
            if feed is not None:
                feed.sent()
                feed.picked += 1
            asked.append(pool.submit(ask, item))
            if len(asked) == 1:
                step["first pick"] = time.monotonic() - t0 - sum(step.values())
        t1 = time.monotonic()
        context = {"history_day": day.isoformat(), "story_run": ctx["run"], "prompt_version": P.PROMPT_VERSION}
        # keep_last: the calls still out when the day's plan is done run on while the next day is prepared and
        # planned (the maintainer, 2026-10-24: the day's last batch); the others are recorded now, in pick order
        out = set()                                                         # only the day's last batch carries
        if keep_last and carry is not None and feed is not None:          # over (the maintainer, 2026-10-25): wait
            for f in asked:                                                # until no more than one GPU batch is out
                if sum(not g.done() for g in asked) <= parallel:
                    break
                f.result()
            out = {k for k, f in enumerate(asked) if not f.done()}
        if len(out) == len(items):
            out = set()                                                     # nothing answered yet: wait for the day
        for k, f in enumerate(asked):
            if k not in out:
                _record(conn, *f.result(), context)
        if carry is not None:
            carry.extend((items[k], asked[k], context) for k in sorted(out))
        if WAITING.is_set():
            WAITING.clear()
            print(f"{day}: the judge server answers again", flush=True)
    finally:
        if own:
            pool.shutdown(wait=True)
    conn.commit()
    TIMES.update(plan=t1 - t0, judge=time.monotonic() - t1)
    if feed is not None and feed.scorings:
        TIMES["scorings"] = feed.scorings
    if carry:
        TIMES["carried"] = len(carry)
    slow = {k: v for k, v in step.items() if v >= 0.5}
    if slow:
        print(f"  {day} to its first call: " + ", ".join(f"{k} {v:.1f} s" for k, v in slow.items()), flush=True)
    return len(items), hyper


def _record(conn, item, v, err, context) -> None:
    """One call's outcome: its verdict, or its failure (A51)."""
    from pipeline.contracts import ComparisonRequest
    from pipeline.measure.run import record, record_failure
    r, c, direction = item
    if v is None:
        record_failure(conn, PURPOSE, ComparisonRequest(r.event_id, "both", r.first_outlet, r.second_outlet,
                                                        r.first_article_id, r.second_article_id), err, context)
    else:
        record(conn, PURPOSE, r, r.event_id.rsplit(":", 1)[1], v, direction, event_id=r.event_id, context=context)


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
    rowid = advance(conn, states, rowid, dt.date.fromisoformat(last or day), grow_days=0)   # catch-up: no new day
    conn.commit()                        # never hold the write lock while waiting: the grouping process writes the
    return last or day, rowid, states    # stories this run waits for (they waited on each other, 2026-10-25)


_worth = None                            # the run's centre weights, a running sum (positions.CentreWorth)
INDEPENDENT = True                       # the maintainer, 2026-10-25: each outlet its own score and uncertainty;
#                                          a verdict moves only its two outlets (positions.update_independent)


def advance(conn, states: dict, last_rowid: int, day: dt.date, grow_days: int = 1) -> int:
    """The verdicts recorded since last_rowid move the positions; articles out of the next window are dropped; the
    day's positions are written (committed with the day's progress)."""
    from pipeline.measure.run import A_FULL, A_SUMMARY
    rows, last_rowid = HP.new_verdicts(conn, PURPOSE, last_rowid, A_FULL, A_SUMMARY)
    for ax, st in states.items():
        if INDEPENDENT:                                                  # each outlet's own score (2026-10-25):
            HP.grow(st, grow_days)                                       # a day's growth, then its verdicts
            HP.update_independent(st, rows[ax])
        else:
            HP.update(st, rows[ax])
            HP.drop_before(st, HP.window_start(day))
    from pipeline.sources.registry import load_outlets, reference_groups
    global _worth
    if _worth is None:
        _worth = HP.CentreWorth(PURPOSE)
    HP.record_day(conn, day.isoformat(), states,                         # A08: centres weighted by comparisons
                  _worth.weights(conn, day.isoformat(), reference_groups(load_outlets())))
    return last_rowid


def spreads(conn, states: dict, day: dt.date, before: str | None = None) -> None:
    """σ_a (how much an outlet's articles differ) re-estimated over the last HYPER_WINDOW_DAYS of verdicts only — a
    bounded fit; it sets the prior of articles not seen yet. Kept as it was when that span has no verdicts. before:
    only verdicts recorded before that time (a recompute sees what the run saw that day)."""
    from pipeline.measure.run import best_hyper, fit
    outlets = next(iter(states.values())).outlets
    since = (day - dt.timedelta(days=HYPER_WINDOW_DAYS)).isoformat()
    posts, n = fit(conn, outlets, PURPOSE, None, since=since, before=before)
    if n:
        for ax, (_, sigma_a) in best_hyper(posts).items():
            states[ax].sigma_a = sigma_a


def _learn_angle(db, vectors, embed_model: str):
    """The answer model (A70) from the verdicts so far, on its own connection (it runs beside the judge)."""
    from pipeline.plan import angle as AN
    c = store.connect(db)
    try:
        vec = ((lambda ids: vectors.rows_for(c, ids)) if vectors is not None
               else (lambda ids: store.load_embeddings(c, embed_model, ids)))
        return AN.learn(c, PURPOSE, vec)
    finally:
        c.close()


def trim(conn, path: Path) -> dict:
    """Make the database match the saved state exactly, so a stopped run resumes after its last finished day: every
    history judge call recorded after the state's last verdict (its last_rowid) — the unfinished day's, and the
    previous day's last batch that was recorded while it ran — is deleted with its verdicts and log; its pairs are
    free to be picked again. Days after the state's day are not marked done. Returns what was deleted."""
    got = HP.load(path)
    if got is None:
        raise SystemExit(f"{path} is missing: nothing to trim against")
    day, last_rowid, _ = got
    done = conn.execute("SELECT max(day) FROM history_progress").fetchone()[0]
    if done is not None and done > day:
        raise SystemExit(f"history_progress reaches {done} but the state only {day}: not trimming")
    ids = [r[0] for r in conn.execute("SELECT call_id FROM judge_calls WHERE purpose = ? AND rowid > ?", (PURPOSE, last_rowid))]
    n = {"calls": len(ids), "verdicts": 0, "logs": 0}
    for k in range(0, len(ids), 500):
        chunk = ids[k:k + 500]
        q = ",".join("?" * len(chunk))
        n["verdicts"] += conn.execute(f"DELETE FROM judgments WHERE call_id IN ({q})", chunk).rowcount
        n["logs"] += conn.execute(f"DELETE FROM judge_call_log WHERE call_id IN ({q})", chunk).rowcount
    conn.execute("DELETE FROM judge_calls WHERE purpose = ? AND rowid > ?", (PURPOSE, last_rowid))
    n["failed logs"] = conn.execute("DELETE FROM judge_call_log WHERE status = 'failed' AND json_extract(context, '$.history_day') > ?",
                                    (day,)).rowcount
    conn.commit()
    return {"state day": day, **n}


def rebuild(conn, path: Path) -> int:
    """The positions of every day already judged, rebuilt from the saved verdicts with the independent model
    (positions.update_independent, the maintainer 2026-10-25): day by day as the run made them — each day the verdicts
    recorded by its end — with the country centres weighted by decayed comparisons (A08). Writes every day's
    positions and the state the judge goes on from; the previous state file is kept beside it (.joint.npz). No
    judging, no grouping. Returns the days rebuilt."""
    import math
    from pipeline.measure.run import A_FULL, A_SUMMARY
    from pipeline.rate.model import BTConfig
    from pipeline.sources.registry import load_outlets, reference_groups
    from pipeline.judge import prompt as P
    days = conn.execute("SELECT day, done_at FROM history_progress ORDER BY day").fetchall()
    outlets = sorted(o.id for o in load_outlets())
    groups = reference_groups(load_outlets())
    states = {ax: HP.start(outlets, ax, BTConfig().prior_sd, SIGMA_A_START) for ax in P.AXES}
    per_day = defaultdict(lambda: defaultdict(int))                     # judged-for day → outlet → comparisons
    calls = f"(SELECT * FROM judge_calls c WHERE c.purpose = ? AND {HP.not_foreign_call('c')})"   # A81
    for o, d, k in conn.execute(f"""SELECT o, d, count(*) FROM (SELECT first_outlet AS o, substr(event_id, 1, 10) AS d
                                   FROM {calls} UNION ALL SELECT second_outlet,
                                   substr(event_id, 1, 10) FROM {calls}) GROUP BY o, d""",      # A81: no foreign editions
                                (PURPOSE, PURPOSE)):
        per_day[d][o] += k
    worth, last, rowid = defaultdict(float), None, 0
    for r in days:
        day = dt.date.fromisoformat(r["day"])
        rows, rowid = HP.new_verdicts(conn, PURPOSE, rowid, A_FULL, A_SUMMARY, until_at=r["done_at"])
        for ax, st in states.items():                                    # a day's growth (as the run: from the
            HP.grow(st, 1 if last is None else (day - last).days)        # first day), then its verdicts
            HP.update_independent(st, rows[ax])
        if last is not None:                                             # A08 centre weights, kept as a running sum
            f = 0.5 ** ((day - last).days / HP.HALF_LIFE_DAYS)
            for o in worth:
                worth[o] *= f
        for d in [x for x in per_day if (last is None or x > last.isoformat()) and x <= r["day"]]:
            for o, k in per_day[d].items():
                worth[o] += k * 0.5 ** ((day - dt.date.fromisoformat(d)).days / HP.HALF_LIFE_DAYS)
        last = day
        weights = {c: {o: (math.log(worth[o]) if worth[o] > 1.0 else 0.0) for o in g} for c, g in groups.items()}
        HP.record_day(conn, r["day"], states, weights)
    conn.commit()
    if days:
        if path.exists():
            os.replace(path, path.with_suffix(".joint.npz"))
        HP.save(path, days[-1]["day"], rowid, states)
    return len(days)


def recompute(conn, path: Path) -> int:
    """The positions of every day already judged, recomputed from the saved verdicts with today's update rule (A74):
    day by day as the run made them — σ_a from the verdicts recorded before that day, then that day's verdicts — and
    written as the run writes them. No judging, no grouping. Returns the days recomputed."""
    from pipeline.measure.run import A_FULL, A_SUMMARY
    from pipeline.rate.model import BTConfig
    from pipeline.sources.registry import load_outlets, reference_groups
    from pipeline.judge import prompt as P
    days = conn.execute("SELECT day, done_at FROM history_progress ORDER BY day").fetchall()
    outlets = sorted(o.id for o in load_outlets())
    groups = reference_groups(load_outlets())
    states = {ax: HP.start(outlets, ax, BTConfig().prior_sd, SIGMA_A_START) for ax in P.AXES}
    rowid, hyper_day, before = 0, None, None
    for r in days:
        day = dt.date.fromisoformat(r["day"])
        if hyper_day is None or (day - hyper_day).days >= HYPER_EVERY_DAYS:
            if before is not None:
                spreads(conn, states, day, before=before)
            hyper_day = day
        rows, rowid = HP.new_verdicts(conn, PURPOSE, rowid, A_FULL, A_SUMMARY, until_at=r["done_at"])
        for ax, st in states.items():
            HP.update(st, rows[ax])
            HP.drop_before(st, HP.window_start(day))
        HP.record_day(conn, r["day"], states, HP.centre_weights(conn, r["day"], groups, PURPOSE))
        before = r["done_at"]
    conn.commit()
    if days:
        HP.save(path, days[-1]["day"], rowid, states)
    return len(days)


def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "recompute":
        ap = argparse.ArgumentParser(prog="replay recompute")
        ap.add_argument("recompute")
        ap.add_argument("--db", default=str(DB))
        a = ap.parse_args()
        conn = prepare(store.connect(a.db))
        print(f"{recompute(conn, Path(a.db).with_name('positions.npz')):,} days of positions recomputed")
        return
    if len(sys.argv) > 1 and sys.argv[1] == "trim":
        ap = argparse.ArgumentParser(prog="replay trim")
        ap.add_argument("trim")
        ap.add_argument("--db", default=str(DB))
        a = ap.parse_args()
        conn = prepare(store.connect(a.db))
        print("trimmed to the last finished day:", trim(conn, Path(a.db).with_name("positions.npz")))
        return
    if len(sys.argv) > 1 and sys.argv[1] == "rebuild":
        ap = argparse.ArgumentParser(prog="replay rebuild")
        ap.add_argument("rebuild")
        ap.add_argument("--db", default=str(DB))
        a = ap.parse_args()
        conn = prepare(store.connect(a.db))
        t = time.monotonic()
        print(f"{rebuild(conn, Path(a.db).with_name('positions.npz')):,} days of positions rebuilt (each outlet its own "
              f"score) in {time.monotonic() - t:.0f} s")
        return
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
    ap.add_argument("--recount", action="store_true", help="with --count-only: count days again that are counted already "
                    "(a year's first days, counted before the year before had vectors: their window was short)")
    ap.add_argument("--ahead", type=int, help="with --count-only: group for a judge (--grouped), keeping each day's run, "
                    "at most this many days ahead of it")
    ap.add_argument("--stride", type=int, default=1, help="with --count-only: group every N-th day only (several grouping "
                    "processes side by side, one --offset each)")
    ap.add_argument("--offset", type=int, default=0, help="with --stride: which of the N days this process groups")
    ap.add_argument("--grouped", metavar="SINCE", help="judge on the story runs of a grouping process (--count-only --ahead) "
                    "counted at or after SINCE (ISO time: after the last vectors were imported), instead of grouping here")
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
        # a call takes seconds: one whose answer never comes back is given up after CALL_TIMEOUT and asked again (the
        # client's 10 minutes, with retries, hung the run for 40 minutes on 2026-10-25)
        import pipeline.judge.client as JC
        JC.HTTP_TIMEOUT = CALL_TIMEOUT
        judge = candidate_judge(local_client(timeout=CALL_TIMEOUT), CANDIDATES[args.judge_candidate])
        global SERVER_QUEUE
        from pipeline.settings import _dotenv
        SERVER_QUEUE = server_queue({**_dotenv(), **os.environ}["LOCAL_LLM_BASE_URL"])
        print(f"GPU queue read from the server: {SERVER_QUEUE()}", flush=True)
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
        done = set() if args.recount else {r["day"] for r in conn.execute("SELECT day FROM history_pair_counts")}
    day, end, year_cache = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end), {}
    if not args.count_only:
        state_path = Path(args.db).with_name("positions.npz")
        last_day, last_rowid, states = open_positions(conn, state_path)
        hyper_day = None
        from concurrent.futures import ThreadPoolExecutor
        ahead, ahead_days = ThreadPoolExecutor(1), {}                       # --grouped: the next day's candidates
        local = args.judge == "local"
        import faulthandler
        import sys as _sys
        _beat = {"t": time.monotonic()}

        def _watch():                                                       # a hang shows where it is (2026-10-25)
            while True:
                time.sleep(30)
                if time.monotonic() - _beat["t"] > WATCHDOG:
                    print(f"  no day finished in {WATCHDOG // 60} min — every thread's stack:", flush=True)
                    faulthandler.dump_traceback(file=_sys.stdout, all_threads=True)
                    _sys.stdout.flush()
                    _beat["t"] = time.monotonic()
        threading.Thread(target=_watch, daemon=True).start()
        learner, angle_next = ThreadPoolExecutor(1), None                    # the answer model, learned beside the judge
        calls_pool, carry = (ThreadPoolExecutor(PARALLEL), []) if local else (None, None)   # calls outlive their day
    first_day = day
    while day <= end:
        if day.isoformat() in done or (args.count_only and (day - first_day).days % args.stride != args.offset):
            day += dt.timedelta(days=1)                                     # --stride: another grouping process's day
            continue
        if args.prefetched and args.count_only:            # GDELT's rows only pick what to fetch: ≈ 6 GB a year
            rows = []
        else:
            if day.year not in year_cache:
                year_cache.clear()
                year_cache[day.year] = H2.load_year(day.year)
            rows = year_cache[day.year].get(day, [])
        if args.prefetched:
            cand, fetched = [], (0 if vectors is not None else embed_missing(conn, day, encoder, args.embed_model))
        else:
            cand = prefilter(rows) if rows else []
            fetched = add_day(conn, cand, encoder, args.embed_model) if cand else 0
        if args.count_only:                              # the backfill writes alongside: a day the database was
            for attempt in range(100):                   # locked is grouped and counted again, 3 s later
                try:
                    if args.ahead is not None:
                        wait_judged(conn, day, dt.date.fromisoformat(args.start), args.ahead)
                    run_id, n_stories, multi = regroup(conn, day, args.embed_model, args.threshold, vectors=vectors,
                                                       keep=args.ahead is not None)
                    n_new = count_day(conn, day, run_id)
                    break
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e) or attempt == 99:
                        raise
                    conn.rollback()
                    print(f"{day}: database locked, again in 3 s", flush=True)
                    time.sleep(3)
            print(f"{day}: window stories {n_stories:,} ({multi:,} multi) · new judgeable pairs {n_new:,}", flush=True)
            day += dt.timedelta(days=1)
            continue
        prepared = None
        if args.grouped:                                                    # a grouping process runs ahead (--ahead)
            fut = ahead_days.pop(day, None) or ahead.submit(prepare_day, args.db, day, args.grouped, vectors)
            t_wait = time.monotonic()
            run_id, n_stories, multi, prepared = fut.result()
            between = {"waited for the day": time.monotonic() - t_wait}
            if day < end:                                                   # tomorrow's candidates load while today is judged
                ahead_days[day + dt.timedelta(days=1)] = ahead.submit(prepare_day, args.db, day + dt.timedelta(days=1),
                                                                      args.grouped, vectors)
        else:
            run_id, n_stories, multi = regroup(conn, day, args.embed_model, args.threshold, vectors=vectors)
        if last_day is not None and day.isoformat() <= last_day:
            raise SystemExit(f"{day}: the positions are already at {last_day} and only move forward (A74)")
        if not args.grouped:
            between = {}
        if not INDEPENDENT and (hyper_day is None or (day - hyper_day).days >= HYPER_EVERY_DAYS):
            t_b = time.monotonic()
            spreads(conn, states, day)                                      # σ_a from the last month's verdicts
            hyper_day, between["spreads"] = day, time.monotonic() - t_b
        if not args.no_angle:                                               # A70, relearned weekly
            if angle_next is not None and angle_next.done():                # the new model, once it is ready
                angle_model, angle_next = angle_next.result(), None
            if angle_day is None:                                           # the run's first day: learn it now
                t_b = time.monotonic()
                angle_model, angle_day = AN.learn(conn, PURPOSE, vec_of), day     # None until MIN_LABELS verdicts
                between["answer model"] = time.monotonic() - t_b
            elif (day - angle_day).days >= ANGLE_EVERY_DAYS and angle_next is None:
                # in the background while the GPU judges (the maintainer, 2026-10-25): from the verdicts so far, used
                # from the day it is ready on (the next) — the GPU waited ≈ 15 s for it every 7 days
                angle_next, angle_day = learner.submit(_learn_angle, args.db, vectors, args.embed_model), day
        calls = 0 if run_id is None else judge_day(
            conn, day, judge, args.calls_per_day, rng, args.judge_fraction, target_sd=args.target_sd, run=run_id,
            min_per_week=args.min_per_week,
            angle=None if angle_model is None else (lambda cs, pre=None: AN.apply(cs, angle_model, vec_of, pre)),
            posts={ax: st.posterior() for ax, st in states.items()}, prepared=prepared,
            pool=calls_pool, carry=carry, keep_last=local and day < end)[0]
        t_pos = time.monotonic()
        last_rowid = advance(conn, states, last_rowid, day)                 # today's verdicts move the positions
        conn.execute("INSERT OR REPLACE INTO history_progress VALUES (?,?,?,?,?,?,?,?)",
                     (day.isoformat(), len(rows), len(cand), fetched, n_stories, multi, calls, utc_now_iso()))
        conn.commit()
        HP.save(state_path, day.isoformat(), last_rowid, states)
        TIMES["positions"] = time.monotonic() - t_pos
        if args.judge == "local":
            _beat["t"] = time.monotonic()
        last_day = day.isoformat()
        if args.grouped and run_id is not None:                             # judged: the grouping's run is not read again
            conn.execute("DELETE FROM event_members WHERE run_id = ?", (run_id,))
            conn.commit()
        print(f"{day}: GDELT {len(rows):,} → candidates {len(cand):,} → fetched {fetched:,} · window stories {n_stories:,}"
              f" ({multi:,} multi) · judged {calls} · " + ", ".join(f"{k} {v:.1f} s" if isinstance(v, float) else f"{k} {v}"
                                                                    for k, v in TIMES.items()), flush=True)
        if any(v >= 1.0 for v in between.values()):                       # what kept the GPU waiting between days
            print(f"  {day} before judging: " + ", ".join(f"{k} {v:.1f} s" for k, v in between.items() if v >= 1.0), flush=True)
        TIMES.clear()
        day += dt.timedelta(days=1)


if __name__ == "__main__":
    main()
