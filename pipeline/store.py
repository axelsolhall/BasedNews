"""Storage for module outputs (SQLite at data/basednews.sqlite, or the path in BASEDNEWS_DB).

A18: SQLite behind these functions; Postgres later if needed. Keep SQL here, not in modules. In production the file is
downloaded from S3 before a job step and uploaded after it succeeds (ADR-010, `pipeline/job/storage.py`).
"""
from __future__ import annotations

import json
import os
import sqlite3

import numpy as np
from dataclasses import asdict
from pathlib import Path

from pipeline.contracts import Article, FeedHealth, HeadlineVersion

DEFAULT_DB = Path("data/basednews.sqlite")


def default_db() -> Path:
    """The database every module opens: BASEDNEWS_DB if set (job runner, containers, tests), else DEFAULT_DB."""
    return Path(os.environ.get("BASEDNEWS_DB") or DEFAULT_DB)

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY, outlet_id TEXT NOT NULL, country TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
    title TEXT, published TEXT, summary TEXT, full_text TEXT, feed_url TEXT, ingested_at TEXT,
    text_source TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_outlet ON articles(outlet_id);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published);
CREATE TABLE IF NOT EXISTS feed_health (
    outlet_id TEXT NOT NULL, checked_at TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (outlet_id, checked_at)
);
CREATE TABLE IF NOT EXISTS embeddings (
    article_id TEXT NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL, vector BLOB NOT NULL,
    PRIMARY KEY (article_id, model)
);
CREATE TABLE IF NOT EXISTS event_members (
    run_id TEXT NOT NULL, event_id TEXT NOT NULL, article_id TEXT NOT NULL,
    PRIMARY KEY (run_id, article_id)
);
CREATE TABLE IF NOT EXISTS runs (
    module TEXT NOT NULL, started_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_usage (
    at TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, kind TEXT NOT NULL,
    input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL
);
CREATE TABLE IF NOT EXISTS judge_calls (
    call_id TEXT PRIMARY KEY, at TEXT NOT NULL, purpose TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
    direction TEXT NOT NULL, event_id TEXT, first_article TEXT, second_article TEXT, first_outlet TEXT,
    second_outlet TEXT, text_kind TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, raw TEXT
);
CREATE TABLE IF NOT EXISTS judgments (
    call_id TEXT NOT NULL, axis TEXT NOT NULL, verdict TEXT NOT NULL, first_wins INTEGER, reason TEXT,
    PRIMARY KEY (call_id, axis)
);
CREATE TABLE IF NOT EXISTS judge_call_log (
    call_id TEXT PRIMARY KEY, status TEXT NOT NULL, run_id TEXT, started_at TEXT, finished_at TEXT, latency_ms INTEGER,
    attempts INTEGER, errors TEXT, request_sha256 TEXT, request_z BLOB, response_json TEXT, response_model TEXT,
    response_id TEXT, system_fingerprint TEXT, finish_reason TEXT, cached_tokens INTEGER, reasoning_tokens INTEGER,
    context TEXT, code TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    fitted_at TEXT NOT NULL, prompt_version TEXT NOT NULL, outlet_id TEXT NOT NULL, axis TEXT NOT NULL,
    mean REAL, sd REAL, n_comparisons INTEGER, PRIMARY KEY (fitted_at, outlet_id, axis)
);
CREATE TABLE IF NOT EXISTS headlines (
    article_id TEXT NOT NULL, title TEXT NOT NULL, first_seen TEXT, url TEXT, how TEXT NOT NULL,
    PRIMARY KEY (article_id, title)
);
CREATE TABLE IF NOT EXISTS text_vectors (
    model TEXT NOT NULL, text_sha1 TEXT NOT NULL, vector BLOB NOT NULL, used_at TEXT NOT NULL,
    PRIMARY KEY (model, text_sha1)
);
CREATE TABLE IF NOT EXISTS lake_files (
    table_name TEXT NOT NULL, month TEXT NOT NULL, rows INTEGER NOT NULL, sha256 TEXT NOT NULL, written_at TEXT NOT NULL,
    PRIMARY KEY (table_name, month)
);
CREATE TABLE IF NOT EXISTS job_runs (
    run_id TEXT NOT NULL, step TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    duration_s REAL, commit_sha TEXT, args TEXT, counts_before TEXT, counts_after TEXT, gate TEXT, error TEXT,
    traceback TEXT, host TEXT, PRIMARY KEY (run_id, step)
);
"""

# Columns added after the first schema (A34): ALTER TABLE on first connect, so old databases keep working.
MIGRATIONS = (("articles", "canonical_id", "TEXT"),        # NULL = canonical; else the article this row is a copy of
              ("embeddings", "input_hash", "TEXT"),        # hash of the text M4 embedded; changes trigger a re-embed
              ("articles", "kind", "TEXT"),                # NULL = article; 'rundown' | 'page' | 'headline_only' (A38)
              ("articles", "genre", "TEXT"),               # 'news' | 'opinion' (2.1, A43)
              ("articles", "pruned_at", "TEXT"))           # text and summary deleted by retention at (A52)


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = path or default_db()
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=120)          # wait up to 2 min for a concurrent writer
    conn.execute("PRAGMA journal_mode=WAL")             # readers never block; live ingest + backfill can run together
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        if col not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            conn.commit()
    return conn


# ---- articles --------------------------------------------------------------------
def article_exists(conn, url: str) -> bool:
    return conn.execute("SELECT 1 FROM articles WHERE url = ? LIMIT 1", (url,)).fetchone() is not None


def insert_article(conn, a: Article) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO articles (id, outlet_id, country, url, title, published, summary, full_text, "
        "feed_url, ingested_at, text_source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (a.id, a.outlet_id, a.country, a.url, a.title, a.published, a.summary, a.full_text,
         a.feed_url, a.ingested_at, a.text_source),
    )
    if cur.rowcount == 1:
        add_headline(conn, HeadlineVersion(a.id, a.title, a.published or a.ingested_at, a.url, "original"))
    return cur.rowcount == 1


# ---- headline history (A34) ----------------------------------------------------------
def add_headline(conn, h: HeadlineVersion) -> bool:
    cur = conn.execute("INSERT OR IGNORE INTO headlines VALUES (?,?,?,?,?)",
                       (h.article_id, h.title, h.first_seen, h.url, h.how))
    return cur.rowcount == 1


def seed_headlines(conn) -> int:
    """Give every article that predates the headlines table its own title as version 1 (idempotent)."""
    cur = conn.execute("""INSERT OR IGNORE INTO headlines
        SELECT id, title, coalesce(published, ingested_at), url, 'original' FROM articles WHERE title IS NOT NULL""")
    return cur.rowcount


def headline_history(conn, article_ids: list[str] | None = None) -> dict[str, list[HeadlineVersion]]:
    """Versions per canonical article, oldest first (ISO strings sort chronologically; unparsed dates sort last)."""
    if article_ids is None:
        rows = conn.execute("SELECT * FROM headlines ORDER BY article_id, first_seen, title").fetchall()
    else:                                                   # only the asked articles: the table grows with every run
        ids, rows = list(dict.fromkeys(article_ids)), []
        for k in range(0, len(ids), 900):                   # SQLite's parameter limit
            chunk = ids[k:k + 900]
            rows += conn.execute(f"SELECT * FROM headlines WHERE article_id IN ({','.join('?' * len(chunk))}) "
                                 "ORDER BY article_id, first_seen, title", chunk).fetchall()
    out: dict[str, list[HeadlineVersion]] = {}
    for r in rows:
        out.setdefault(r["article_id"], []).append(
            HeadlineVersion(r["article_id"], r["title"], r["first_seen"], r["url"], r["how"]))
    return out


# ---- feed health -----------------------------------------------------------------
def save_health(conn, h: FeedHealth) -> None:
    conn.execute("INSERT OR REPLACE INTO feed_health VALUES (?,?,?)",
                 (h.outlet_id, h.checked_at, json.dumps(asdict(h))))


def latest_health(conn) -> dict[str, FeedHealth]:
    rows = conn.execute(
        "SELECT payload FROM feed_health f WHERE checked_at = "
        "(SELECT MAX(checked_at) FROM feed_health g WHERE g.outlet_id = f.outlet_id)"
    ).fetchall()
    out = {}
    for r in rows:
        d = json.loads(r["payload"])
        d["errors"] = tuple(d.get("errors", ()))
        out[d["outlet_id"]] = FeedHealth(**d)
    return out


def log_run(conn, module: str, started_at: str, payload: dict) -> None:
    conn.execute("INSERT INTO runs VALUES (?,?,?)", (module, started_at, json.dumps(payload)))


# ---- job runs (5.1, ADR-010): one row per job step, written by pipeline.job ------------------------
JOB_RUN_JSON = ("args", "counts_before", "counts_after", "gate")


def save_job_run(conn, rec: dict) -> None:
    row = {k: (json.dumps(rec.get(k)) if k in JOB_RUN_JSON else rec.get(k)) for k in
           ("run_id", "step", "status", "started_at", "finished_at", "duration_s", "commit_sha", "args", "counts_before",
            "counts_after", "gate", "error", "traceback", "host")}
    conn.execute(f"INSERT OR REPLACE INTO job_runs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                 tuple(row.values()))


def recent_job_runs(conn, n: int = 20) -> list[dict]:
    rows = conn.execute("SELECT * FROM job_runs ORDER BY started_at DESC, rowid DESC LIMIT ?", (n,)).fetchall()
    return [{k: (json.loads(r[k]) if k in JOB_RUN_JSON and r[k] else r[k]) for k in r.keys()} for r in rows]


# ---- embeddings (M4) ---------------------------------------------------------------
def save_embeddings(conn, model: str, ids: list[str], vecs: np.ndarray, input_hashes: list[str] | None = None) -> None:
    hashes = input_hashes or [None] * len(ids)
    conn.executemany("INSERT OR REPLACE INTO embeddings (article_id, model, dim, vector, input_hash) VALUES (?,?,?,?,?)",
                     [(i, model, int(v.shape[0]), v.astype(np.float32).tobytes(), h) for i, v, h in zip(ids, vecs, hashes)])


def load_embeddings(conn, model: str, article_ids: list[str] | None = None) -> tuple[list[str], np.ndarray]:
    q = "SELECT article_id, dim, vector FROM embeddings WHERE model = ?"
    rows = conn.execute(q, (model,)).fetchall()
    if article_ids is not None:
        keep = set(article_ids)
        rows = [r for r in rows if r["article_id"] in keep]
    if not rows:
        return [], np.zeros((0, 0), dtype=np.float32)
    return [r["article_id"] for r in rows], np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])


# ---- events (M5) -------------------------------------------------------------------
def save_events(conn, run_id: str, membership: dict[str, str]) -> None:
    conn.executemany("INSERT OR REPLACE INTO event_members VALUES (?,?,?)",
                     [(run_id, e, a) for a, e in membership.items()])


def latest_event_run(conn) -> str | None:
    r = conn.execute("SELECT run_id FROM event_members ORDER BY run_id DESC LIMIT 1").fetchone()
    return r["run_id"] if r else None


# ---- AI spend (A48) ----------------------------------------------------------------
def log_usage(conn, provider: str, model: str, kind: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
    from pipeline.ingest.feeds import utc_now_iso
    conn.execute("INSERT INTO api_usage VALUES (?,?,?,?,?,?,?)",
                 (utc_now_iso(), provider, model, kind, int(input_tokens), int(output_tokens), float(cost_usd)))


def spent_this_month(conn) -> float:
    from pipeline.ingest.feeds import utc_now_iso
    month = utc_now_iso()[:7]
    return float(conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM api_usage WHERE substr(at, 1, 7) = ?",
                              (month,)).fetchone()[0])
