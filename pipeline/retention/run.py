"""A52 retention: keep the hot database small. A dry run unless --apply.

    uv run python -m pipeline.retention.run                  # report what would be archived and deleted
    uv run python -m pipeline.retention.run --apply          # archive to the lake, verify, then delete and VACUUM

0. Trash (A79): an article past the 7-day match window whose best pair is less than 25 % likely to be in the wrong
   order (prior_positions) is deleted everywhere; each outlet keeps at most its 100 most uncertain per day.
1. Lake (ADR-010): monthly Parquet files, `<lake>/<table>/month=YYYY-MM/part.parquet`, for the tables kept forever
   (verdicts, positions, runs, job runs, spend, article metadata, headlines) and the judge call log (A51). A month is
   rewritten when its row count differs from the `lake_files` index; the call log is written once per closed month.
   Article metadata and headlines that leave the database are appended first (A80): `moved-<time>.parquet`, indexed
   in `lake_parts`; their `part.parquet` holds only the rows still in the database. Every file is read back and
   checked before anything is deleted. Locally the lake is data/lake; in production the job uploads it to the private
   bucket before the database (`pipeline/job/storage.py`).
2. Delete, only with --apply (outside production a backup copy of the database is made first):
   - text and summary of live articles older than WINDOW_DAYS, marked `pruned_at` (archive-sample articles keep
     theirs, A52);
   - the embeddings of those articles;
   - story runs except the latest KEEP_STORY_RUNS (`event_members`, `same_text_pairs`);
   - call-log request blobs older than WINDOW_DAYS whose month is a verified lake file (the hash stays in the row);
   - cached text vectors not used for WINDOW_DAYS (A56);
   - normalize's undo copies of those articles' text (articles_text_backup, articles_shared_text);
   - the retired pre-pipeline scraper's rows (articles_legacy, A37; D8).
   Before the lake (A80), only with --apply: live articles older than WINDOW_DAYS and never judged leave the database
   for good — metadata and headlines appended to the lake, then every row of theirs deleted (ARTICLE_ROWS, article_keep).
3. VACUUM, so the file actually shrinks.
Eval (`verify`, also the job gate): database size, integrity, and a refit from the kept verdicts compared with the
latest stored positions.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import sqlite3
import zlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline import store
from pipeline.ingest.feeds import utc_now_iso

# A52: text and vectors are kept exactly as long as a step can still read them — normalize and embed look back
# RECENT_DAYS (14), stories 7, the judge only the newest story run — and deleted after (the maintainer, 2026-10-18:
# "the data that is older than what can be matched should be gone"); verdicts and metadata go to the lake for good
from pipeline.job.steps import RECENT_DAYS  # noqa: E402

WINDOW_DAYS = int(RECENT_DAYS)
KEEP_STORY_RUNS = 2         # the latest run feeds the planner; one more as a fallback
MATCH_DAYS = 7              # stories group only the last 7 days (steps.py): an older article can never be matched again
SWAP_MIN = 0.25             # A79: below this chance of its best pair being in the wrong order, an article is trash
KEEP_PER_OUTLET_DAY = 100   # A79: and each outlet keeps at most its 100 most uncertain articles per day
PAGE_SIZE = 16384           # A84: seven 2 KB vector rows a page instead of one
LAKE = Path("data/lake")
ARTICLE_META = "id, outlet_id, country, url, title, published, ingested_at, text_source, canonical_id, kind, genre, pruned_at"
# table → (SELECT …, month expression); judgments take the month of their call
LAKE_TABLES = {
    "judge_calls": ("SELECT * FROM judge_calls", "substr(at, 1, 7)"),
    "judgments": ("SELECT j.*, substr(c.at, 1, 7) AS _month FROM judgments j JOIN judge_calls c USING (call_id)", "_month"),
    "positions": ("SELECT * FROM positions", "substr(fitted_at, 1, 7)"),
    "api_usage": ("SELECT * FROM api_usage", "substr(at, 1, 7)"),
    "runs": ("SELECT * FROM runs", "substr(started_at, 1, 7)"),
    "job_runs": ("SELECT * FROM job_runs", "substr(started_at, 1, 7)"),
    "articles_meta": (f"SELECT {ARTICLE_META} FROM articles", "substr(coalesce(ingested_at, published), 1, 7)"),
    "headlines": ("SELECT * FROM headlines", "substr(first_seen, 1, 7)"),
    "judge_call_log": ("SELECT * FROM judge_call_log", "substr(started_at, 1, 7)"),
}
# A80: articles leave the database. Their metadata and headlines are appended once, as `moved-<time>.parquet` (the
# `_move` temp table), before the rows go; `part.parquet` of these two tables then holds only the rows still in the
# database (a month with none left is rewritten empty), so the two never overlap and the lake is their union.
SNAPSHOT_TABLES = ("articles_meta", "headlines")
MOVE_TABLES = {
    "articles_meta": (f"SELECT {ARTICLE_META} FROM articles WHERE id IN (SELECT id FROM _move)",
                      "substr(coalesce(ingested_at, published), 1, 7)"),
    "headlines": ("SELECT * FROM headlines WHERE article_id IN (SELECT id FROM _move)", "substr(first_seen, 1, 7)"),
}
ARROW = {"TEXT": pa.string(), "INTEGER": pa.int64(), "REAL": pa.float64(), "BLOB": pa.binary()}


def lake_dir() -> Path:
    return Path(os.environ.get("BASEDNEWS_LAKE") or LAKE)


def cutoff(now: dt.datetime | None = None, days: int = WINDOW_DAYS) -> str:
    return ((now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _schema(conn, table: str, columns: list[str]) -> pa.Schema:
    """Arrow types from the declared SQLite types, so every month of a table has the same schema (Athena)."""
    src = {"articles_meta": "articles", "judgments": "judgments"}.get(table, table)
    declared = {r["name"]: (r["type"] or "TEXT").upper() for r in conn.execute(f"PRAGMA table_info({src})")}
    return pa.schema([(c, ARROW.get(declared.get(c, "TEXT"), pa.string())) for c in columns])


def _month_rows(conn, table: str) -> dict[str, int]:
    q, month = LAKE_TABLES[table]
    return {r[0]: r[1] for r in conn.execute(f"SELECT {month} AS m, COUNT(*) FROM ({q}) GROUP BY m") if r[0]}


def lake_plan(conn, now: dt.datetime | None = None) -> list[tuple[str, str, int]]:
    """(table, month, rows) to (re)write: the month's row count differs from the index; call log: closed months once."""
    this_month = (now or dt.datetime.now(dt.UTC)).strftime("%Y-%m")
    index = {(r["table_name"], r["month"]): r["rows"] for r in conn.execute("SELECT * FROM lake_files")}
    plan = []
    for table in LAKE_TABLES:
        for month, n in sorted(_month_rows(conn, table).items()):
            if table == "judge_call_log":
                if month < this_month and (table, month) not in index:
                    plan.append((table, month, n))
            elif index.get((table, month)) != n:
                plan.append((table, month, n))
    for table in SNAPSHOT_TABLES:                          # A80: every row of the month moved out → an empty file
        hot = _month_rows(conn, table)
        plan += [(t, m, 0) for (t, m), n in sorted(index.items()) if t == table and n and m not in hot]
    return plan


def write_month(conn, table: str, month: str, root: Path) -> tuple[int, str]:
    q, mexpr = LAKE_TABLES[table]
    cur = conn.execute(f"SELECT * FROM ({q}) WHERE {mexpr} = ?", (month,))
    cols = [d[0] for d in cur.description if d[0] != "_month"]
    rows = [dict(r) for r in cur.fetchall()]
    t = pa.Table.from_pylist([{c: r[c] for c in cols} for r in rows], schema=_schema(conn, table, cols))
    path = root / table / f"month={month}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    pq.write_table(t, tmp, compression="zstd")
    os.replace(tmp, path)
    back = pq.read_table(path)                           # read back: count, and for the call log every request hash
    if back.num_rows != len(rows):
        raise RuntimeError(f"lake {table}/{month}: wrote {len(rows)} rows, read back {back.num_rows}")
    if table == "judge_call_log":
        for blob, sha in zip(back.column("request_z").to_pylist(), back.column("request_sha256").to_pylist()):
            if blob is not None and hashlib.sha256(zlib.decompress(blob)).hexdigest() != sha:
                raise RuntimeError(f"lake {table}/{month}: a request does not match its hash")
            if blob is None:
                raise RuntimeError(f"lake {table}/{month}: a request blob is already gone; refusing to index the month")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    conn.execute("INSERT OR REPLACE INTO lake_files VALUES (?,?,?,?,?)", (table, month, len(rows), digest, utc_now_iso()))
    return len(rows), digest


def deletions(conn, cut: str) -> dict[str, tuple[str, tuple]]:
    """name → (SQL, params). The same WHERE clauses serve the dry run (counted) and --apply (executed)."""
    old_live = ("FROM articles WHERE ingested_at < ? AND published < ? "
                "AND (text_source IS NULL OR text_source NOT LIKE 'wayback-%')")
    keep_runs = f"SELECT run_id FROM (SELECT DISTINCT run_id FROM event_members ORDER BY run_id DESC LIMIT {KEEP_STORY_RUNS})"
    has_stp = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'same_text_pairs'").fetchone() is not None
    months = [r[0] for r in conn.execute("SELECT month FROM lake_files WHERE table_name = 'judge_call_log'")]
    ops = {
        "article_text": (f"UPDATE articles SET full_text = NULL, summary = NULL, pruned_at = ? "
                         f"WHERE id IN (SELECT id {old_live} AND pruned_at IS NULL)", ("{now}", cut, cut)),
        "embeddings": (f"DELETE FROM embeddings WHERE article_id IN (SELECT id {old_live})", (cut, cut)),
        "story_runs": (f"DELETE FROM event_members WHERE run_id NOT IN ({keep_runs})", ()),
        "call_log_requests": ("UPDATE judge_call_log SET request_z = NULL WHERE request_z IS NOT NULL AND started_at < ? "
                              f"AND substr(started_at, 1, 7) IN ({','.join('?' * len(months)) or 'NULL'})", (cut, *months)),
    }
    if has_stp:
        ops["same_text_pairs"] = (f"DELETE FROM same_text_pairs WHERE run_id NOT IN ({keep_runs})", ())
    ops["vector_cache"] = ("DELETE FROM text_vectors WHERE used_at < ?", (cut,))      # A56: re-bought if needed again
    # normalize's undo copies (the text before cleaning, stripped boilerplate) go with their article's text: undoing a
    # cleaning needs the text, and keeping them would hold publishers' text past the window (A10)
    for t, col in (("articles_text_backup", "article_id"), ("articles_shared_text", "article_id")):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone():
            ops[t] = (f"DELETE FROM {t} WHERE {col} IN (SELECT id {old_live})", (cut, cut))
    # the retired pre-pipeline scraper (A37): replaced by the GDELT history, of no use to any step (D8, 2026-10-18)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'articles_legacy'").fetchone():
        ops["legacy_articles"] = ("DELETE FROM articles_legacy WHERE 1", ())
    return ops


def swap_values(conn, ids: list[str], outlet: dict[str, str]) -> dict[str, float]:
    """article → its best pair's chance of being in the wrong order (A79): over the other outlets in its story (in the
    newest stored story run that has it) and both axes, Φ(−|μ_A − μ_B| / √(σ_A² + σ_B²)) from prior_positions — the
    planner's own measure. 0 when it shares no story with another outlet; an outlet without a prior counts as 0.5."""
    from scipy.special import ndtr
    prior: dict[tuple[str, str], tuple[float, float]] = {
        (r["outlet_id"], r["axis"]): (r["mean"], r["sd"]) for r in conn.execute("SELECT * FROM prior_positions")}
    axes = sorted({ax for _, ax in prior})
    want = set(ids)
    newest: dict[str, tuple[str, str]] = {}                   # article → (run, event) of the newest run that has it
    members: dict[tuple[str, str], set[str]] = {}             # (run, event) → its outlets
    for run, ev, o, a in conn.execute("SELECT e.run_id, e.event_id, a.outlet_id, e.article_id FROM event_members e "
                                      "JOIN articles a ON a.id = e.article_id"):
        members.setdefault((run, ev), set()).add(o)
        if a in want and (a not in newest or run > newest[a][0]):
            newest[a] = (run, ev)
    pair: dict[tuple[str, str], float] = {}

    def p_inv(a: str, b: str) -> float:
        k = (min(a, b), max(a, b))
        if k not in pair:
            best = 0.0
            for ax in axes:
                if (a, ax) not in prior or (b, ax) not in prior:
                    best = max(best, 0.5)
                    continue
                (ma, sa), (mb, sb) = prior[(a, ax)], prior[(b, ax)]
                best = max(best, float(ndtr(-abs(ma - mb) / max((sa * sa + sb * sb) ** 0.5, 1e-9))))
            pair[k] = best
        return pair[k]

    return {a: max((p_inv(outlet[a], o) for o in members.get(newest.get(a), set()) - {outlet[a]}), default=0.0)
            for a in ids}


def trash(conn, now: dt.datetime | None = None) -> tuple[list[str], list[tuple[str, float]]]:
    """A79 (the maintainer, 2026-10-30): an article that has left the window in which it can still be matched (stories
    group the last MATCH_DAYS) is trash if its swap value is below SWAP_MIN; of the rest, each outlet keeps its
    KEEP_PER_OUTLET_DAY most uncertain per day (by publish day, counting those kept before). Judged and archive-sample
    articles are never trash. No prior_positions: nothing is decided. Returns (trash ids, [(kept id, swap)]); each
    article is decided once (kept ones are remembered in article_keep)."""
    if not conn.execute("SELECT 1 FROM prior_positions LIMIT 1").fetchone():
        return [], []
    cut = cutoff(now, MATCH_DAYS)
    rows = conn.execute(
        "SELECT id, outlet_id, substr(coalesce(published, ingested_at), 1, 10) AS day FROM articles "
        "WHERE ingested_at < ? AND published < ? AND (text_source IS NULL OR text_source NOT LIKE 'wayback-%') "
        "AND id NOT IN (SELECT article_id FROM article_keep) "
        "AND id NOT IN (SELECT first_article FROM judge_calls WHERE first_article IS NOT NULL "
        "               UNION SELECT second_article FROM judge_calls WHERE second_article IS NOT NULL)",
        (cut, cut)).fetchall()
    outlet = {r["id"]: r["outlet_id"] for r in rows}
    swap = swap_values(conn, list(outlet), outlet)
    used = {(o, d): n for o, d, n in conn.execute(
        "SELECT a.outlet_id, substr(coalesce(a.published, a.ingested_at), 1, 10), COUNT(*) "
        "FROM article_keep k JOIN articles a ON a.id = k.article_id GROUP BY 1, 2")}
    gone, kept = [], []
    for r in sorted(rows, key=lambda r: -swap[r["id"]]):
        k = (r["outlet_id"], r["day"])
        if swap[r["id"]] >= SWAP_MIN and used.get(k, 0) < KEEP_PER_OUTLET_DAY:
            used[k] = used.get(k, 0) + 1
            kept.append((r["id"], swap[r["id"]]))
        else:
            gone.append(r["id"])
    return gone, kept


# where an article leaves rows, by table and column — a trashed article is removed from all of them (A79: nothing kept;
# the lake's month files are rewritten without it, as their row counts change)
ARTICLE_ROWS = (("embeddings", "article_id"), ("event_members", "article_id"), ("headlines", "article_id"),
                ("articles_text_backup", "article_id"), ("articles_shared_text", "article_id"),
                ("same_text_pairs", "a"), ("same_text_pairs", "b"), ("articles", "id"))


def delete_trash(conn, ids: list[str], kept: list[tuple[str, float]], now: str) -> dict[str, int]:
    """Delete the trashed articles everywhere and remember the kept ones; rows deleted per table."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _trash (id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM _trash")
    conn.executemany("INSERT INTO _trash VALUES (?)", [(i,) for i in ids])
    n: dict[str, int] = {}
    for table, col in ARTICLE_ROWS:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone():
            n[table] = n.get(table, 0) + conn.execute(f"DELETE FROM {table} WHERE {col} IN (SELECT id FROM _trash)").rowcount
    conn.executemany("INSERT OR IGNORE INTO article_keep VALUES (?,?,?)", [(i, s, now) for i, s in kept])
    conn.commit()
    return n


def movable(conn, cut: str) -> list[str]:
    """A80: live articles past the window (their text is pruned there, so no step reads them again) that no verdict
    names; judged ones stay, as the fit's `since` looks up its articles' publish dates."""
    return [r[0] for r in conn.execute(
        "SELECT id FROM articles WHERE ingested_at < ? AND published < ? "
        "AND (text_source IS NULL OR text_source NOT LIKE 'wayback-%') "
        "AND id NOT IN (SELECT first_article FROM judge_calls WHERE first_article IS NOT NULL "
        "               UNION SELECT second_article FROM judge_calls WHERE second_article IS NOT NULL)", (cut, cut))]


def move_out(conn, ids: list[str], root: Path, now: str) -> dict[str, int]:
    """Append the articles' metadata and headlines to the lake (read back), then delete every row of theirs."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _move (id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM _move")
    conn.executemany("INSERT INTO _move VALUES (?)", [(i,) for i in ids])
    stamp = now.replace("-", "").replace(":", "")
    for table, (q, mexpr) in MOVE_TABLES.items():
        cur = conn.execute(f"SELECT *, {mexpr} AS _month FROM ({q})")
        cols = [d[0] for d in cur.description if d[0] != "_month"]
        months: dict[str, list[dict]] = {}
        for r in cur.fetchall():
            months.setdefault(r["_month"], []).append({c: r[c] for c in cols})
        for month, rows in sorted(months.items()):
            path = root / table / f"month={month}" / f"moved-{stamp}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(rows, schema=_schema(conn, table, cols)), path, compression="zstd")
            if pq.read_table(path).num_rows != len(rows):
                raise RuntimeError(f"lake {path}: wrote {len(rows)} rows, read back another count")
            conn.execute("INSERT OR REPLACE INTO lake_parts VALUES (?,?,?,?,?,?)",
                         (path.relative_to(root).as_posix(), table, month, len(rows),
                          hashlib.sha256(path.read_bytes()).hexdigest(), now))
    n: dict[str, int] = {}
    for table, col in (*ARTICLE_ROWS, ("article_keep", "article_id")):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone():
            n[table] = n.get(table, 0) + conn.execute(f"DELETE FROM {table} WHERE {col} IN (SELECT id FROM _move)").rowcount
    conn.commit()
    return n


def _count(conn, sql: str, params: tuple) -> tuple[int, int]:
    """Rows an UPDATE/DELETE would touch and the bytes it frees (approximate: text and blob lengths)."""
    params = tuple(p for p in params if p != "{now}")
    where = sql.split(" WHERE ", 1)[1]
    table = sql.split(" FROM ", 1)[1].split()[0] if sql.startswith("DELETE") else sql.split()[1]
    size = {"articles": "COALESCE(LENGTH(full_text), 0) + COALESCE(LENGTH(summary), 0)", "embeddings": "LENGTH(vector)",
            "event_members": "60", "same_text_pairs": "60", "judge_call_log": "COALESCE(LENGTH(request_z), 0)",
            "text_vectors": "LENGTH(vector)", "articles_text_backup": "COALESCE(LENGTH(original), 0)",
            "articles_shared_text": "COALESCE(LENGTH(full_text), 0)",
            "articles_legacy": "COALESCE(LENGTH(full_text), 0) + COALESCE(LENGTH(summary), 0) + 200"}[table]
    n, b = conn.execute(f"SELECT COUNT(*), COALESCE(SUM({size}), 0) FROM {table} WHERE {where}", params).fetchone()
    return n, b


def db_bytes(conn) -> int:
    return conn.execute("PRAGMA page_count").fetchone()[0] * conn.execute("PRAGMA page_size").fetchone()[0]


def verify(conn) -> dict:
    """A52's eval: size, integrity, and whether a refit from the kept verdicts reproduces the stored positions."""
    from pipeline.judge import prompt as P
    out = {"db_bytes": db_bytes(conn), "integrity": conn.execute("PRAGMA quick_check").fetchone()[0],
           "refit_max_abs_diff": None, "refit_note": ""}
    latest = conn.execute("SELECT MAX(fitted_at) FROM positions WHERE prompt_version = ?", (P.PROMPT_VERSION,)).fetchone()[0]
    if not latest:
        out["refit_note"] = "no stored positions for the current prompt"
        return out
    newer = conn.execute("SELECT COUNT(*) FROM judge_calls WHERE purpose = 'measure' AND prompt_version = ? AND at > ?",
                         (P.PROMPT_VERSION, latest)).fetchone()[0]
    if newer:
        out["refit_note"] = f"{newer} verdicts newer than the stored fit {latest}: not comparable"
        return out
    stored = {(r["outlet_id"], r["axis"]): r["mean"] for r in
              conn.execute("SELECT outlet_id, axis, mean FROM positions WHERE fitted_at = ? AND prompt_version = ?",
                           (latest, P.PROMPT_VERSION))}
    from pipeline.measure.run import fit
    posts, _ = fit(conn, sorted({o for o, _ in stored}))
    diff = 0.0
    for ax, h in posts.items():
        op = h.outlet_posterior()
        for k, o in enumerate(op.outlets):
            diff = max(diff, abs(float(op.mean[k]) - stored[(o, ax)]))
    out["refit_max_abs_diff"], out["refit_note"] = diff, f"against the fit of {latest}"
    return out


def backup(path: Path) -> Path:
    dest = path.parent / "backups" / f"{path.stem}-{utc_now_iso().replace(':', '')}.sqlite"
    dest.parent.mkdir(parents=True, exist_ok=True)
    src, dst = sqlite3.connect(path), sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close(), dst.close()
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="archive, verify, delete and VACUUM (default: report only)")
    ap.add_argument("--days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--no-backup", action="store_true", help="skip the local backup copy (production: S3 versioning)")
    args = ap.parse_args()
    conn = store.connect()
    cut, now = cutoff(days=args.days), utc_now_iso()
    size0 = db_bytes(conn)
    print(f"retention (A52): window {args.days} days (before {cut}) · database {size0 / 1e6:.1f} MB"
          f"{'' if args.apply else ' · DRY RUN, nothing is changed'}")

    # A79 first, so trash never reaches the lake (a month already written is rewritten without it: its count changes)
    gone, kept = trash(conn)
    if not conn.execute("SELECT 1 FROM prior_positions LIMIT 1").fetchone():
        print("trash (A79): no prior_positions — nothing decided (python -m pipeline.history.prior --to <db>)")
    else:
        print(f"trash (A79): past the {MATCH_DAYS}-day match window, swap < {SWAP_MIN}, or over {KEEP_PER_OUTLET_DAY} "
              f"per outlet and day: {len(gone):,} articles trashed, {len(kept):,} kept")
        if args.apply and (gone or kept):
            n = delete_trash(conn, gone, kept, now)
            print("  deleted: " + " · ".join(f"{t} {k:,}" for t, k in n.items()))

    # A80 before the lake plan, so this run's part.parquet files already leave the moved rows out
    root = lake_dir()
    ids = movable(conn, cut)
    print(f"move out (A80): {len(ids):,} articles past the {args.days}-day window and never judged → lake, then deleted")
    if args.apply and ids:
        n = move_out(conn, ids, root, now)
        print("  deleted: " + " · ".join(f"{t} {k:,}" for t, k in n.items()))

    plan = lake_plan(conn)
    per_table: dict[str, list] = {}
    for t, m, n in plan:
        per_table.setdefault(t, []).append((m, n))
    print(f"lake {root}: {len(plan)} month files to write" + "".join(
        f"\n  {t:<18} {len(ms):>3} months ({ms[0][0]} … {ms[-1][0]}) · {sum(n for _, n in ms):>8,} rows"
        for t, ms in per_table.items()))
    if args.apply:
        for t, m, _ in plan:
            write_month(conn, t, m, root)
        conn.commit()

    ops = deletions(conn, cut)
    freed = 0
    for name, (sql, params) in ops.items():
        n, b = _count(conn, sql, params)
        freed += b
        print(f"  {name:<18} {n:>8,} rows · ~{b / 1e6:.1f} MB")

    if not args.apply:
        print(f"would free ≈ {freed / 1e6:.1f} MB before VACUUM. Run with --apply to archive and delete.")
        return
    if not args.no_backup and not os.environ.get("BASEDNEWS_STATE"):
        print(f"backup: {backup(store.default_db())}")
    for name, (sql, params) in ops.items():
        conn.execute(sql, tuple(now if p == "{now}" else p for p in params))
    conn.commit()
    # A84: 16 KB pages — a 2 KB vector row fills a 4 KB page alone; a page size changes only by VACUUM outside WAL
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute(f"PRAGMA page_size={PAGE_SIZE}")
    conn.execute("VACUUM")
    conn.execute("PRAGMA journal_mode=WAL")
    v = verify(conn)
    print(f"done: database {size0 / 1e6:.1f} → {v['db_bytes'] / 1e6:.1f} MB · integrity {v['integrity']} · "
          f"refit vs stored positions: {v['refit_max_abs_diff']} ({v['refit_note']})")


if __name__ == "__main__":
    main()
