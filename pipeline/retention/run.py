"""A52 retention: keep the hot database small. A dry run unless --apply.

    uv run python -m pipeline.retention.run                  # report what would be archived and deleted
    uv run python -m pipeline.retention.run --apply          # archive to the lake, verify, then delete and VACUUM

1. Lake (ADR-010): monthly Parquet files, `<lake>/<table>/month=YYYY-MM/part.parquet`, for the tables kept forever
   (verdicts, positions, runs, job runs, spend, article metadata, headlines) and the judge call log (A51). A month is
   rewritten when its row count differs from the `lake_files` index; the call log is written once per closed month.
   Every file is read back and checked before anything is deleted. Locally the lake is data/lake; in production the
   job uploads it to the private bucket before the database (`pipeline/job/storage.py`).
2. Delete, only with --apply (outside production a backup copy of the database is made first):
   - text and summary of live articles older than WINDOW_DAYS, marked `pruned_at` (archive-sample articles keep
     theirs, A52);
   - the embeddings of those articles;
   - story runs except the latest KEEP_STORY_RUNS (`event_members`, `same_text_pairs`);
   - call-log request blobs older than WINDOW_DAYS whose month is a verified lake file (the hash stays in the row);
   - cached text vectors not used for WINDOW_DAYS (A56).
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

WINDOW_DAYS = 30            # A52: bulky inputs older than this are deleted (open: window length)
KEEP_STORY_RUNS = 2         # the latest run feeds the planner; one more as a fallback
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
    return ops


def _count(conn, sql: str, params: tuple) -> tuple[int, int]:
    """Rows an UPDATE/DELETE would touch and the bytes it frees (approximate: text and blob lengths)."""
    params = tuple(p for p in params if p != "{now}")
    where = sql.split(" WHERE ", 1)[1]
    table = sql.split(" FROM ", 1)[1].split()[0] if sql.startswith("DELETE") else sql.split()[1]
    size = {"articles": "COALESCE(LENGTH(full_text), 0) + COALESCE(LENGTH(summary), 0)", "embeddings": "LENGTH(vector)",
            "event_members": "60", "same_text_pairs": "60", "judge_call_log": "COALESCE(LENGTH(request_z), 0)",
            "text_vectors": "LENGTH(vector)"}[table]
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

    plan = lake_plan(conn)
    root = lake_dir()
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
    for t in ("articles_legacy", "articles_text_backup", "articles_shared_text"):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone():
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:<18} {n:>8,} rows · not touched (backup/legacy table; the maintainer decides, VERIFY.md)")

    if not args.apply:
        print(f"would free ≈ {freed / 1e6:.1f} MB before VACUUM. Run with --apply to archive and delete.")
        return
    if not args.no_backup and not os.environ.get("BASEDNEWS_STATE"):
        print(f"backup: {backup(store.default_db())}")
    for name, (sql, params) in ops.items():
        conn.execute(sql, tuple(now if p == "{now}" else p for p in params))
    conn.commit()
    conn.execute("VACUUM")
    v = verify(conn)
    print(f"done: database {size0 / 1e6:.1f} → {v['db_bytes'] / 1e6:.1f} MB · integrity {v['integrity']} · "
          f"refit vs stored positions: {v['refit_max_abs_diff']} ({v['refit_note']})")


if __name__ == "__main__":
    main()
