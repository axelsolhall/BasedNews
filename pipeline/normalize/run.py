"""M3 run: merge renamed copies into one article with a headline history; strip container-page texts (A34);
mark items that are not one article (rundowns, site pages) and strip consent/paywall walls (A38).

    uv run --group ml python -m pipeline.normalize.run [--days 14] [--dry-run]

Input: articles (M2). Output, all in the store:
  articles.canonical_id    set on copies; downstream modules read canonical articles only
  articles.kind            'rundown' | 'page' for non-articles (2.2 and 2.3 skip them); NULL = article
  headlines                every headline a canonical article has carried (M2 adds same-URL edits, M3 merged copies)
  articles_shared_text     backup of texts removed from container entries (reversible)
  normalize_groups         one row per decision, for pipeline.normalize.eval
Runs after M2 and before M4. Idempotent: stripped texts leave their group, merged copies are re-merged identically.
Reads only articles published in the last `--days` (the embed step's window: nothing downstream reads older ones), plus
older copies sharing a text with them, so a run costs the same on day 300 as on day 30.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict

import numpy as np

from pipeline import store
from pipeline.embed.backend import get_encoder
from pipeline.embed.profiles import profile
from pipeline.embed.run import MODEL
from pipeline.ingest.feeds import utc_now_iso
from pipeline.ingest.run import MIN_TEXT_CHARS
from pipeline.normalize.boilerplate import find_boilerplate, strip_boilerplate
from pipeline.normalize.kinds import WALL_LEAD_CHARS, genre_of, has_wall_phrase, is_wall, kind_of, strip_site_suffix
from pipeline.normalize.rules import Member, NormalizeConfig, decide

TEXT_LEAD_CHARS = 400      # MiniLM reads ~128 tokens; the lead is what a container page shows on top
GROUPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles_shared_text (
    article_id TEXT PRIMARY KEY, full_text TEXT, text_source TEXT, cleaned_at TEXT);
CREATE TABLE IF NOT EXISTS boilerplate (
    outlet_id TEXT, field TEXT, paragraph TEXT, n_articles INTEGER, edge_share REAL, title_sim REAL, found_at TEXT,
    PRIMARY KEY (outlet_id, field, paragraph));
CREATE TABLE IF NOT EXISTS articles_text_backup (
    article_id TEXT, field TEXT, original TEXT, saved_at TEXT, PRIMARY KEY (article_id, field));
CREATE TABLE IF NOT EXISTS normalize_groups (
    run_id TEXT, group_key TEXT, outlet_id TEXT, kind TEXT, n_members INTEGER, n_titles INTEGER, payload TEXT,
    PRIMARY KEY (run_id, group_key));
"""


def shared_text_groups(conn, since: str, outlet: str) -> list[list]:
    """One outlet's canonical rows sharing one stored text under at least two different headlines, for texts carried
    by an article published since `since` (older members included). Copies merged by an earlier run are already
    resolved; their headlines live in the canonical's history. The text match runs in SQLite, so old texts are
    compared there and never loaded."""
    groups = defaultdict(list)
    for r in conn.execute("""SELECT id, outlet_id, title, summary, published, ingested_at, url, full_text, text_source
                             FROM articles WHERE full_text IS NOT NULL AND canonical_id IS NULL AND outlet_id = ?
                             AND full_text IN (SELECT full_text FROM articles WHERE full_text IS NOT NULL
                                 AND canonical_id IS NULL AND published >= ? AND outlet_id = ?)""", (outlet, since, outlet)):
        groups[(r["outlet_id"], hashlib.sha1(r["full_text"].encode()).hexdigest())].append(r)
    return [g for g in groups.values() if len({(r["title"] or "").strip().lower() for r in g}) > 1]


def similarities(model, group) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    titles = [r["title"] or "" for r in group]
    summaries = [r["summary"] or "" for r in group]
    E = model.encode(titles + summaries + [group[0]["full_text"][:TEXT_LEAD_CHARS]], normalize_embeddings=True)
    T, S, L = E[:len(group)], E[len(group):2 * len(group)], E[-1]
    return S @ S.T, T @ T.T, T @ L


def apply(conn, group, d, now: str) -> tuple[int, int]:
    """Write one decision. Returns (copies merged, texts stripped)."""
    merged = stripped = 0
    by_id = {r["id"]: r for r in group}
    for mid, cid in d.canonical.items():
        if mid == cid:
            continue
        r = by_id[mid]
        conn.execute("UPDATE articles SET canonical_id = ? WHERE id = ?", (cid, mid))
        conn.execute("""INSERT OR IGNORE INTO headlines SELECT ?, title, first_seen, url, 'merged_copy'
                        FROM headlines WHERE article_id = ?""", (cid, mid))
        conn.execute("INSERT OR IGNORE INTO headlines VALUES (?,?,?,?,'merged_copy')",
                     (cid, r["title"], r["published"] or r["ingested_at"], r["url"]))
        conn.execute("DELETE FROM headlines WHERE article_id = ?", (mid,))
        conn.execute("DELETE FROM embeddings WHERE article_id = ?", (mid,))
        merged += 1
    if d.kind == "container":
        for mid, cid in d.canonical.items():
            if cid in d.keeps_text:
                continue
            conn.execute("INSERT OR REPLACE INTO articles_shared_text SELECT id, full_text, text_source, ? "
                         "FROM articles WHERE id = ?", (now, mid))
            conn.execute("UPDATE articles SET full_text = NULL, text_source = text_source || '+container' WHERE id = ?",
                         (mid,))
            stripped += 1
    return merged, stripped


def decide_outlet(conn, model, outlet: str, since: str, cfg, prof: dict, site_names: list[str], known_db) -> dict:
    """Every decision for one outlet's articles published since `since` (and older copies of their texts); no writes."""
    decided = []
    for g in shared_text_groups(conn, since, outlet):
        S, T, L = similarities(model, g)
        decided.append((g, decide([Member(r["id"], r["published"], bool(r["summary"])) for r in g], S, T, L, cfg), S, T, L))

    # 1a. boilerplate by frequency and position (A40), for texts and summaries; known paragraphs are re-applied to
    #     new articles even once their own count has dropped (they were stripped from the older ones)
    rows = conn.execute("""SELECT id, outlet_id, title, summary, full_text FROM articles
                           WHERE canonical_id IS NULL AND kind IS NULL AND published >= ? AND outlet_id = ?""",
                        (since, outlet)).fetchall()
    title_vec = dict(zip([r["id"] for r in rows],
                         model.encode([r["title"] or "" for r in rows], normalize_embeddings=True, batch_size=128))) \
        if rows else {}

    def mean_title_sim(ids):
        V = np.array([title_vec[i] for i in ids[:40]])
        iu = np.triu_indices(len(V), 1)
        return float((V @ V.T)[iu].mean())

    new_bp = {field: find_boilerplate([(r["id"], r["outlet_id"], r[field]) for r in rows], mean_title_sim,
                                      same_story_sim=prof["same_story"])
              for field in ("full_text", "summary")}
    known = {field: known_db[(outlet, field)] | {p for (_, p) in new_bp[field]} for field in ("full_text", "summary")}
    bp_changes = []                                       # (article_id, field, old, new)
    for r in rows:
        for field in ("full_text", "summary"):
            old_v = r[field]
            new_v = strip_boilerplate(old_v, known[field])
            if field == "full_text" and new_v and len(new_v) < MIN_TEXT_CHARS:
                new_v = None                              # nothing left but the site (A19)
            if new_v != old_v:
                bp_changes.append((r["id"], field, old_v, new_v))

    # 1c. title site suffixes (A42) and genre (A43)
    title_changes, genres = [], []
    for r in conn.execute("SELECT id, outlet_id, title, url, genre FROM articles WHERE canonical_id IS NULL "
                          "AND published >= ? AND outlet_id = ?", (since, outlet)):
        t = strip_site_suffix(r["title"], site_names)
        if t != r["title"]:
            title_changes.append((r["id"], r["title"], t))
        g = genre_of(t, r["url"])
        if g != r["genre"]:
            genres.append((g, r["id"]))

    # 1b. non-articles and walls (A38): rules on titles, then the model only for texts with a wall phrase
    kinds = [(kind_of(r["title"], r["summary"], r["url"], r["full_text"]), r["id"]) for r in
             conn.execute("""SELECT id, title, summary, url, full_text FROM articles
                             WHERE canonical_id IS NULL AND kind IS NULL AND pruned_at IS NULL AND published >= ?
                             AND outlet_id = ?""", (since, outlet))]                    # A52: no text ≠ no article
    kinds = [(k, i) for k, i in kinds if k]
    cand = [r for r in conn.execute("""SELECT id, title, full_text FROM articles
                                       WHERE canonical_id IS NULL AND kind IS NULL AND full_text IS NOT NULL
                                       AND published >= ? AND outlet_id = ?""", (since, outlet))
            if has_wall_phrase(r["full_text"])]
    walls = []
    if cand:
        T = model.encode([r["title"] or "" for r in cand], normalize_embeddings=True)
        L = model.encode([r["full_text"][:WALL_LEAD_CHARS] for r in cand], normalize_embeddings=True)
        walls = [r["id"] for r, s_ in zip(cand, (T * L).sum(1)) if is_wall(r["full_text"], float(s_), prof["wall_max"])]
    return {"decided": decided, "new_bp": new_bp, "bp_changes": bp_changes, "title_changes": title_changes,
            "genres": genres, "kinds": kinds, "walls": walls}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="only articles published in the last N days (and their copies)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = store.connect()
    conn.executescript(GROUPS_SCHEMA)
    seeded = 0 if args.dry_run else store.seed_headlines(conn)
    conn.commit()
    prof = profile(MODEL)                                  # thresholds belong to the embedding model (A47)
    cfg = NormalizeConfig(same_summary=prof["same_summary"], same_title=prof["same_title"],
                          owns_text=prof["owns_text"], margin=prof["owns_margin"])
    model = get_encoder(MODEL, conn=conn, cache=True)       # A56: an hourly run pays only for new texts
    run_id, now = utc_now_iso(), utc_now_iso()
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. decide everything first: reads + model only, so no write lock is held while encoding (backfill runs alongside).
    #    Every rule compares an outlet only with itself, so one outlet at a time: memory is one outlet's window, however
    #    many outlets and countries there are (A61: a 14-day window of 41 countries is ≈ 336 k articles).
    from urllib.parse import urlparse
    from pipeline.sources.registry import load_outlets
    alias = json.load(open("data/outlet_aliases.json"))["aliases"]
    site_names = {o.id: [o.name, *alias.get(o.id, []), urlparse(o.homepage).netloc.removeprefix("www.")]
                  for o in load_outlets()}
    known_db = defaultdict(set)
    for r in conn.execute("SELECT outlet_id, field, paragraph FROM boilerplate"):
        known_db[(r["outlet_id"], r["field"])].add(r["paragraph"])
    decided, bp_changes, title_changes, genres, kinds, walls = [], [], [], [], [], []
    new_bp: dict[str, dict] = {"full_text": {}, "summary": {}}
    outlets = [r[0] for r in conn.execute("SELECT DISTINCT outlet_id FROM articles WHERE published >= ? ORDER BY 1", (since,))]
    for outlet in outlets:
        d = decide_outlet(conn, model, outlet, since, cfg, prof, site_names.get(outlet, []), known_db)
        decided += d["decided"]; bp_changes += d["bp_changes"]; title_changes += d["title_changes"]
        genres += d["genres"]; kinds += d["kinds"]; walls += d["walls"]
        for field in new_bp:
            new_bp[field].update(d["new_bp"][field])

    # 2. then write in one short transaction
    kinds_n, versions, merged, stripped = Counter(), Counter(), 0, 0
    for g, d, S, T, L in decided:
        n_titles = len({(r["title"] or "").strip().lower() for r in g})
        kinds_n[d.kind if d.kind == "renamed" or d.keeps_text else "container_no_owner"] += 1
        if d.kind == "renamed":
            versions[n_titles] += 1
        if args.dry_run:
            continue
        payload = {"members": [{"id": r["id"], "title": r["title"], "url": r["url"], "published": r["published"],
                                "has_summary": bool(r["summary"]), "text_sim": round(float(L[k]), 3),
                                "canonical": d.canonical[r["id"]]} for k, r in enumerate(g)],
                   "keeps_text": sorted(d.keeps_text), "best_text_sim": round(d.best_text_sim, 3),
                   "runner_up_text_sim": round(d.runner_up_text_sim, 3),
                   "summary_sim": np.round(S, 3).tolist(), "title_sim": np.round(T, 3).tolist()}
        conn.execute("INSERT OR REPLACE INTO normalize_groups VALUES (?,?,?,?,?,?,?)",
                     (run_id, g[0]["id"], g[0]["outlet_id"], d.kind, len(g), n_titles, json.dumps(payload)))
        m, s_ = apply(conn, g, d, now)
        merged += m; stripped += s_
    if not args.dry_run:
        conn.executemany("INSERT OR REPLACE INTO boilerplate VALUES (?,?,?,?,?,?,?)",
                         [(o, field, p, v["n"], v["edge"], v["sim"], now) for field, found in new_bp.items()
                          for (o, p), v in found.items()])
        conn.executemany("INSERT OR IGNORE INTO articles_text_backup VALUES (?,?,?,?)",
                         [(i, f, old_v, now) for i, f, old_v, _ in bp_changes])
        for i, f, _, new_v in bp_changes:
            conn.execute(f"UPDATE articles SET {f} = ? WHERE id = ?", (new_v, i))
        conn.executemany("UPDATE articles SET kind = ? WHERE id = ?", kinds)
        conn.executemany("INSERT OR IGNORE INTO articles_text_backup VALUES (?, 'title', ?, ?)",
                         [(i, old_t, now) for i, old_t, _ in title_changes])
        for i, old_t, new_t in title_changes:
            conn.execute("UPDATE articles SET title = ? WHERE id = ?", (new_t, i))
            conn.execute("UPDATE OR IGNORE headlines SET title = ? WHERE article_id = ? AND title = ?", (new_t, i, old_t))
            conn.execute("DELETE FROM headlines WHERE article_id = ? AND title = ?", (i, old_t))
        conn.executemany("UPDATE articles SET genre = ? WHERE id = ?", genres)
        conn.executemany("INSERT OR REPLACE INTO articles_shared_text SELECT id, full_text, text_source, ? "
                         "FROM articles WHERE id = ?", [(now, i) for i in walls])
        conn.executemany("UPDATE articles SET full_text = NULL, text_source = text_source || '+wall' WHERE id = ?",
                         [(i,) for i in walls])
        store.log_run(conn, "normalize", run_id, {"groups": dict(kinds_n), "merged": merged, "stripped": stripped,
                                                  "seeded_headlines": seeded, "kinds": dict(Counter(k for k, _ in kinds)),
                                                  "walls": len(walls), "boilerplate_edits": len(bp_changes),
                                                  "titles_unsuffixed": len(title_changes), "genre_set": len(genres)})
        conn.commit()
    print(f"run {run_id}{' (dry run, nothing written)' if args.dry_run else ''}: {sum(kinds_n.values())} shared-text groups "
          f"→ {dict(kinds_n)}; renamed articles by headline count {dict(sorted(versions.items()))}; "
          f"copies merged {merged}, container texts stripped {stripped}; non-articles marked "
          f"{dict(Counter(k for k, _ in kinds))}, walls stripped {len(walls)}; boilerplate paragraphs found "
          f"{sum(len(f) for f in new_bp.values())}, texts/summaries edited {len(bp_changes)}; site suffixes removed "
          f"from {len(title_changes)} titles; genre set on {len(genres)}. "
          f"Run `python -m pipeline.normalize.eval`.")


if __name__ == "__main__":
    main()
