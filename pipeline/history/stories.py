"""H2 top stories: for every day since 2015-11-01, the stories most of a country's reference outlets covered (and the
stories most countries covered), from GDELT's entities — the pairs the history judge will compare (A61).

    PYTHONPATH=. uv run python -m pipeline.history.stories [--years 2016] [--threshold T]
    → data/history/stories/year=YYYY.parquet
    PYTHONPATH=. uv run python -m pipeline.history.stories --eval       (accuracy against the live story grouping)

An article = the set of people, organisations and places GDELT found in it (English-normalised, so it works across
languages). Per day: entities in more than MAX_DF of the day's articles are dropped (they link everything: "united
states"), the rest weighted by rarity (idf), and articles grouped by exact average linkage on cosine similarity —
the same method as the live stories (2.3), on a different representation. A story counts when ≥ 2 outlets covered it;
national stories rank by outlets, then articles; global stories by countries. Each kept story lists one article per
outlet: the one closest to the story's centre.

Output schema (one row per chosen article): day, scope (national|global), country (national only), story, rank,
n_outlets, n_countries, outlet, url, seen, centrality.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial.distance import squareform

SRC = Path("data/history/gdelt")
MORE = Path("data/history/gdelt_more")                    # gdelt_raw: outlets the first query left out
OUT = Path("data/history/stories")
THRESHOLD = 0.35          # cosine on idf-weighted entities; set from --eval (precision first)
MAX_DF = 0.02             # an entity in more than 2 % of the day's articles links unrelated stories: dropped
MIN_ENTITIES = 2          # fewer informative entities than this: the article cannot be matched reliably
TOP_NATIONAL, TOP_GLOBAL = 5, 10


SLUG_WORD = re.compile(r"[^\W\d_]{4,}", re.UNICODE)          # letters only, ≥ 4: drops ids, dates, "html"
PATH_NOISE = {"news", "nyheter", "article", "articles", "story", "stories", "html", "index", "www", "amp", "politik",
              "politics", "world", "national", "nation", "local", "inland", "ausland", "noticias", "notizie", "actualite",
              "sport", "sports", "opinion", "video", "live", "uutiset", "nieuws", "wiadomosci", "zpravy", "spravy"}


def entities(persons: str | None, orgs: str | None, places: str | None, url: str | None = None) -> list[str]:
    out = []
    for prefix, field in (("p:", persons), ("o:", orgs), ("l:", places)):
        if field:
            out += [prefix + x.strip().lower() for x in field.split(";") if x.strip()]
    if url:                                                  # most news URLs carry the headline as words
        out += ["w:" + w for w in SLUG_WORD.findall(urlsplit(url).path.lower()) if w not in PATH_NOISE]
    return list(dict.fromkeys(out))


def vectors(docs: list[list[str]]) -> tuple[csr_matrix, np.ndarray]:
    """idf-weighted, L2-normalised entity vectors; `keep` marks articles with enough informative entities."""
    n = len(docs)
    df = Counter(e for d in docs for e in set(d))
    vocab = {e: i for i, (e, c) in enumerate((e, c) for e, c in df.items() if 2 <= c <= max(2, MAX_DF * n))}
    rows, cols, vals = [], [], []
    keep = np.zeros(n, dtype=bool)
    for r, d in enumerate(docs):
        idx = [vocab[e] for e in d if e in vocab]
        if len(idx) < MIN_ENTITIES:
            continue
        w = np.array([math.log(n / df[e]) for e in d if e in vocab])
        w /= np.linalg.norm(w)
        rows += [r] * len(idx); cols += idx; vals += list(w)
        keep[r] = True
    return csr_matrix((vals, (rows, cols)), shape=(n, max(1, len(vocab)))), keep


def cluster(X: csr_matrix, rows: np.ndarray, threshold: float) -> dict[int, int]:
    """Exact average linkage on cosine, cut at `threshold`, for the given rows (same split-by-components argument as
    the old pipeline.events.cluster.average_by_components). Returns {row: label}."""
    if len(rows) < 2:
        return {int(r): i for i, r in enumerate(rows)}
    Y = X[rows]
    S = (Y @ Y.T).tocoo()
    m = (S.row < S.col) & (S.data >= threshold)
    n = len(rows)
    _, comp = connected_components(coo_matrix((np.ones(m.sum()), (S.row[m], S.col[m])), shape=(n, n)), directed=False)
    labels, nxt = {}, 0
    members = defaultdict(list)
    for i, c in enumerate(comp):
        members[c].append(i)
    for idx in members.values():
        if len(idx) == 1:
            labels[int(rows[idx[0]])] = nxt; nxt += 1
            continue
        D = np.clip(1.0 - (Y[idx] @ Y[idx].T).toarray(), 0.0, 2.0)
        np.fill_diagonal(D, 0.0)
        sub = fcluster(linkage(squareform(D, checks=False), method="average"), t=1.0 - threshold, criterion="distance") - 1
        for i, s in zip(idx, sub):
            labels[int(rows[i])] = nxt + int(s)
        nxt += int(sub.max()) + 1
    return labels


def day_stories(day, rows: list[dict], threshold: float) -> list[dict]:
    docs = [entities(r["persons"], r["orgs"], r["places"], r["url"]) for r in rows]
    X, keep = vectors(docs)
    out = []

    def emit(scope, country, groups, rank_key, top):
        ranked = sorted(groups.items(), key=lambda kv: rank_key(kv[1]), reverse=True)[:top]
        for rank, (story, idx) in enumerate(ranked, 1):
            centre = np.asarray(X[idx].mean(axis=0)).ravel()
            cen = X[idx] @ centre
            best = {}
            for i, c in zip(idx, cen):
                o = rows[i]["outlet"]
                if o not in best or c > best[o][1]:
                    best[o] = (i, float(c))
            n_out = len(best)
            n_cty = len({rows[i]["country"] for i in idx})
            for o, (i, c) in best.items():
                out.append({"day": day, "scope": scope, "country": country, "story": f"{scope}:{country or 'all'}:{story}",
                            "rank": rank, "n_outlets": n_out, "n_countries": n_cty, "outlet": o, "url": rows[i]["url"],
                            "seen": rows[i]["seen"], "centrality": c})

    by_country = defaultdict(list)
    for i, r in enumerate(rows):
        if keep[i]:
            by_country[r["country"]].append(i)
    for country, idx in by_country.items():
        lab = cluster(X, np.array(idx), threshold)
        groups = defaultdict(list)
        for i, l in lab.items():
            groups[l].append(i)
        groups = {l: g for l, g in groups.items() if len({rows[i]["outlet"] for i in g}) >= 2}
        emit("national", country, groups, lambda g: (len({rows[i]["outlet"] for i in g}), len(g)), TOP_NATIONAL)
    lab = cluster(X, np.flatnonzero(keep), threshold)
    groups = defaultdict(list)
    for i, l in lab.items():
        groups[l].append(i)
    groups = {l: g for l, g in groups.items() if len({rows[i]["country"] for i in g}) >= 2}
    emit("global", None, groups, lambda g: (len({rows[i]["country"] for i in g}), len(g)), TOP_GLOBAL)
    return out


def load_year(year: int):
    """GDELT's articles of the year by day: the first query's file, and the raw-file months for the outlets it left
    out (gdelt_raw, A75)."""
    import pyarrow.parquet as pq
    cols = ["seen", "url", "persons", "orgs", "places", "outlet", "country"]
    rows = pq.read_table(SRC / f"year={year}.parquet", columns=cols).to_pylist()
    seen = {r["url"] for r in rows}
    for f in sorted(MORE.glob(f"{year}-*.parquet")):
        for r in pq.read_table(f, columns=cols).to_pylist():
            if r["url"] not in seen:
                seen.add(r["url"])
                rows.append(r)
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["seen"].date()].append(r)
    return by_day


def load_month(year: int, month: int):
    """load_year for one month only (the backfill: a year of rows held as dicts took ≈ 5.5 GB per process)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    cols = ["seen", "url", "persons", "orgs", "places", "outlet", "country"]
    lo = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
    hi = dt.datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=dt.timezone.utc)
    import pyarrow.compute as pc
    f = pq.ParquetFile(SRC / f"year={year}.parquet")
    rows = []
    for g in range(f.metadata.num_row_groups):              # the year's rows are not in time order: every group spans
        t = f.read_row_group(g, columns=cols)               # the year, so read one at a time and keep the month's
        t = t.filter(pc.and_(pc.greater_equal(t["seen"], pa.scalar(lo, t["seen"].type)),
                             pc.less(t["seen"], pa.scalar(hi, t["seen"].type))))
        rows += t.to_pylist()
        del t
    seen = {r["url"] for r in rows}
    more = MORE / f"{year}-{month:02d}.parquet"
    if more.exists():
        for r in pq.read_table(more, columns=cols).to_pylist():
            if r["url"] not in seen:
                seen.add(r["url"])
                rows.append(r)
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["seen"].date()].append(r)
    return by_day


def run(years: list[int], threshold: float):
    import pyarrow as pa
    import pyarrow.parquet as pq
    OUT.mkdir(parents=True, exist_ok=True)
    for y in years:
        by_day = load_year(y)
        out = []
        for day in sorted(by_day):
            out += day_stories(day, by_day[day], threshold)
        pq.write_table(pa.Table.from_pylist(out), OUT / f"year={y}.parquet")
        nat = [r for r in out if r["scope"] == "national"]
        days = len(by_day)
        print(f"{y}: {days} days · national stories {len({r['story'] + str(r['day']) for r in nat}):,} "
              f"({len(nat):,} articles) · global {len({r['story'] + str(r['day']) for r in out if r['scope'] == 'global'}):,}",
              flush=True)


def evaluate(thresholds: list[float]):
    """Pairwise agreement with the live story grouping (embeddings, threshold validated in E7b) on articles both
    sources have, Oct 2026: of the article pairs H2 puts in one story (same day, same country), how many does live
    also put together (precision), and of live's same-day same-country pairs, how many H2 finds (recall)."""
    import sqlite3
    from urllib.parse import urlsplit

    def norm(u):
        p = urlsplit(u.strip())
        return p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")

    db = sqlite3.connect("data/prod_snapshot.sqlite")
    run_id = db.execute("SELECT MAX(run_id) FROM event_members").fetchone()[0]
    live = {norm(u): ev for u, ev in db.execute("""SELECT a.url, m.event_id FROM event_members m
            JOIN articles a ON a.id = m.article_id WHERE m.run_id = ?""", (run_id,))}
    by_day = load_year(2026)
    days = [d for d in sorted(by_day) if d >= dt.date(2026, 10, 1)]
    print(f"live run {run_id}; days {days[0]}–{days[-1]}")
    print(f"{'threshold':>9} {'precision':>9} {'recall':>7} {'pairs':>7}")
    for t in thresholds:
        tp = fp = fn = 0
        for d in days:
            rows = by_day[d]
            docs = [entities(r["persons"], r["orgs"], r["places"], r["url"]) for r in rows]
            X, keep = vectors(docs)
            for country in {r["country"] for r in rows}:
                idx = np.array([i for i, r in enumerate(rows) if keep[i] and r["country"] == country and norm(r["url"]) in live])
                if len(idx) < 2:
                    continue
                lab = cluster(X, idx, t)
                for a in range(len(idx)):
                    for b in range(a + 1, len(idx)):
                        i, j = idx[a], idx[b]
                        if rows[i]["outlet"] == rows[j]["outlet"]:
                            continue                       # pairs that matter: across outlets
                        same_h2 = lab[i] == lab[j]
                        same_live = live[norm(rows[i]["url"])] == live[norm(rows[j]["url"])]
                        tp += same_h2 and same_live; fp += same_h2 and not same_live; fn += same_live and not same_h2
        p = tp / (tp + fp) if tp + fp else float("nan")
        r = tp / (tp + fn) if tp + fn else float("nan")
        print(f"{t:>9.2f} {p:>9.2f} {r:>7.2f} {tp + fp:>7}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="*", default=list(range(2015, dt.date.today().year + 1)))
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--eval", action="store_true")
    args = ap.parse_args()
    if args.eval:
        return evaluate([0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6])
    run(args.years, args.threshold)


if __name__ == "__main__":
    main()
