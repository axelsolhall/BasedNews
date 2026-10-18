"""E11: teach the local judge to answer like gpt-5.4-mini (LoRA) — docs/experiments/e11_prereg.json.

    PYTHONPATH=. uv run python -m pipeline.experiments.e11 sample --batch b1 --pairs 3000      # free: choose pairs
    PYTHONPATH=. uv run python -m pipeline.experiments.e11 judge --batch b1 --budget-usd 5     # paid: gpt-5.4-mini v4
    PYTHONPATH=. uv run python -m pipeline.experiments.e11 export --out data/distil/sft         # train/val JSONL
    PYTHONPATH=. uv run python -m pipeline.experiments.e11 status

Training data are gpt-5.4-mini's v4 verdicts on history pairs — the judge's real job: headline + description, 2015
onwards, every outlet. Built to grow in batches without spoiling anything:
  - a batch is a set of whole days (two per month, seeded by the batch's name), never a day an earlier batch used, and
    never October 2026 or later (E9's 943 test calls are from then);
  - within a day, stories are grouped as the replay groups them (MiniLM @ 0.60, the story module) and pairs are chosen
    to spread over outlets (the least-used outlet first); order and question direction are random per pair, seeded;
  - one day in ten is validation (by a hash of day and batch): its verdicts never train, they pick the stopping point;
  - every choice is stored before any call (e11_pairs), and every call is recorded like live's (judge_calls, purpose
    'e11', the A51 call log), so a run that stops resumes and a batch can be audited;
  - E9's 943 calls stay the test: never exported here.
Optional extra training examples: E10's 668 fresh US/UK verdicts (`export --with-e10`), never used to test.
Everything lives in data/distil/distil.sqlite (git-ignored), articles copied in from the history database.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np

from pipeline import store
from pipeline.judge import prompt as P

DB = Path("data/distil/distil.sqlite")
HISTORY = Path("data/history/history.sqlite")
E10_DB = Path("data/experiments/e10.sqlite")
PURPOSE = "e11"
EMBED = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
THRESHOLD = 0.60                    # MiniLM's story threshold (E7), as the dry runs used
FIRST, LAST = dt.date(2015, 11, 1), dt.date(2026, 9, 30)       # E9's test calls are from October 2026
DAYS_PER_MONTH = 2
VAL_EVERY = 10                      # one day in ten is validation

PAIRS = """CREATE TABLE IF NOT EXISTS e11_pairs (
    batch TEXT, split TEXT, day TEXT, story TEXT, first_article TEXT, second_article TEXT, first_outlet TEXT,
    second_outlet TEXT, direction TEXT, chosen_at TEXT, PRIMARY KEY (first_article, second_article))"""


def connect(path: Path = DB):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = store.connect(path)
    conn.execute(PAIRS)
    return conn


def split_of(day: str, batch: str) -> str:
    return "val" if int(hashlib.sha256(f"{batch}:{day}".encode()).hexdigest(), 16) % VAL_EVERY == 0 else "train"


def pick_days(batch: str, used: set[str], first: dt.date = FIRST, last: dt.date = LAST,
              per_month: int = DAYS_PER_MONTH) -> list[dt.date]:
    """per_month days of every month, seeded by the batch name, never a day an earlier batch used."""
    rng = np.random.default_rng(int(hashlib.sha256(batch.encode()).hexdigest()[:12], 16))
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        days = [dt.date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]
        days = [d for d in days if first <= d <= last and d.isoformat() not in used]
        out += sorted(rng.choice(days, size=min(per_month, len(days)), replace=False).tolist())
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def day_pairs(hist, day: dt.date, encoder) -> list[dict]:
    """The day's cross-outlet pairs within stories, one article per outlet per story, as the replay forms them (its
    story module on the day's articles; identical texts skipped, as live skips wire copies)."""
    from pipeline.events.cluster import ClusterConfig, cluster
    rows = hist.execute("""SELECT id, outlet_id, country, url, title, summary, published FROM articles
                           WHERE published >= ? AND published < ? ORDER BY published, id""",
                        (f"{day.isoformat()}T00:00:00Z", f"{(day + dt.timedelta(days=1)).isoformat()}T00:00:00Z")).fetchall()
    rows = [dict(r) for r in rows if (r["summary"] or "").strip()]
    if len(rows) < 2:
        return []
    V = encoder.encode([f"{r['title']}\n{r['summary'] or ''}".strip() for r in rows])
    hours = np.array([dt.datetime.fromisoformat(r["published"].replace("Z", "+00:00")).timestamp() / 3600 for r in rows])
    labels = cluster(np.asarray(V), hours, ClusterConfig(method="average", threshold=THRESHOLD))
    stories: dict[int, dict[str, dict]] = {}
    for r, lab in zip(rows, labels):
        stories.setdefault(int(lab), {}).setdefault(r["outlet_id"], r)
    out = []
    for lab, by_outlet in stories.items():
        arts = list(by_outlet.values())
        for i, a in enumerate(arts):
            for b in arts[i + 1:]:
                if f"{a['title']}\n{a['summary']}".strip().lower() == f"{b['title']}\n{b['summary']}".strip().lower():
                    continue
                out.append({"story": f"{day.isoformat()}:ev{lab}", "a": a, "b": b})
    return out


def choose(pairs: list[dict], k: int, use: Counter, rng) -> list[dict]:
    """k pairs spread over outlets: repeatedly the pair whose busier outlet has been used least (random tie-break)."""
    pool = list(pairs)
    rng.shuffle(pool)
    out = []
    while pool and len(out) < k:
        best = min(range(len(pool)), key=lambda i: (max(use[pool[i]["a"]["outlet_id"]], use[pool[i]["b"]["outlet_id"]]),
                                                    use[pool[i]["a"]["outlet_id"]] + use[pool[i]["b"]["outlet_id"]]))
        p = pool.pop(best)
        use[p["a"]["outlet_id"]] += 1; use[p["b"]["outlet_id"]] += 1
        out.append(p)
    return out


def sample(batch: str, n_pairs: int, conn=None, hist=None, encoder=None, days: list[dt.date] | None = None) -> int:
    from pipeline.ingest.feeds import utc_now_iso
    conn = conn or connect()
    if conn.execute("SELECT 1 FROM e11_pairs WHERE batch = ? LIMIT 1", (batch,)).fetchone():
        raise SystemExit(f"batch {batch} exists: choose a new name for a new batch")
    if hist is None:
        import sqlite3
        hist = sqlite3.connect(f"file:{HISTORY}?mode=ro", uri=True)
        hist.row_factory = sqlite3.Row
    if encoder is None:
        from pipeline.embed.backend import get_encoder
        encoder = get_encoder(EMBED)
    used = {r[0] for r in conn.execute("SELECT DISTINCT day FROM e11_pairs")}
    days = days if days is not None else pick_days(batch, used)
    per_day = int(np.ceil(n_pairs / max(len(days), 1)))
    rng = np.random.default_rng(int(hashlib.sha256(f"{batch}:pairs".encode()).hexdigest()[:12], 16))
    use, n = Counter(), 0
    for day in days:
        got = choose(day_pairs(hist, day, encoder), per_day, use, rng)
        for p in got:
            a, b = (p["b"], p["a"]) if rng.random() < 0.5 else (p["a"], p["b"])
            for art in (a, b):
                conn.execute("INSERT OR IGNORE INTO articles (id, outlet_id, country, url, title, published, summary, "
                             "text_source) VALUES (?,?,?,?,?,?,?,?)", (art["id"], art["outlet_id"], art["country"],
                                                                       art["url"], art["title"], art["published"],
                                                                       art["summary"], "history-meta"))
            conn.execute("INSERT OR IGNORE INTO e11_pairs VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (batch, split_of(day.isoformat(), batch), day.isoformat(), p["story"], a["id"], b["id"],
                          a["outlet_id"], b["outlet_id"], "positive" if rng.random() < 0.5 else "negative", utc_now_iso()))
        conn.commit()
        n += len(got)
        print(f"{day}: {len(got)} pairs ({n:,} so far)", flush=True)
        if n >= n_pairs:
            break
    return n


def judge(batch: str, budget_usd: float, conn=None, judge_=None, parallel: int = 8) -> dict:
    """gpt-5.4-mini v4 on every unjudged pair of the batch, cheapest first never — in the stored order; stops at the
    budget. Recorded as live records its calls (purpose 'e11', call log), so a stopped run resumes."""
    from concurrent.futures import ThreadPoolExecutor

    from pipeline.contracts import ComparisonRequest
    from pipeline.judge.client import JudgeCallFailed, OpenAIJudge
    from pipeline.measure.run import record, record_failure, rendered
    from pipeline.settings import load_settings
    conn = conn or connect()
    todo = conn.execute("""SELECT p.* FROM e11_pairs p WHERE p.batch = ? AND NOT EXISTS (
                             SELECT 1 FROM judge_calls c WHERE c.purpose = ? AND c.first_article = p.first_article
                             AND c.second_article = p.second_article) ORDER BY p.day, p.story, p.first_article""",
                        (batch, PURPOSE)).fetchall()
    arts = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM articles")}
    judge_ = judge_ or OpenAIJudge(load_settings())
    spent, done, failed = 0.0, 0, 0
    print(f"{batch}: {len(todo):,} pairs to judge, budget ${budget_usd:.2f}", flush=True)

    def ask(p):
        a, b = arts[p["first_article"]], arts[p["second_article"]]
        try:
            return p, judge_.compare(rendered(a, {}, "summary"), rendered(b, {}, "summary"), p["direction"]), None
        except JudgeCallFailed as e:
            return p, None, e

    for k in range(0, len(todo), 40):
        if spent >= budget_usd:
            break
        with ThreadPoolExecutor(parallel) as pool:
            for p, v, err in pool.map(ask, todo[k:k + 40]):
                req = ComparisonRequest(p["story"] + ":summary", "both", p["first_outlet"], p["second_outlet"],
                                        p["first_article"], p["second_article"], 0.0)
                ctx = {"batch": batch, "split": p["split"], "day": p["day"], "experiment": "E11"}
                if v is None:
                    record_failure(conn, PURPOSE, req, err, ctx); failed += 1
                else:
                    record(conn, PURPOSE, req, "summary", v, p["direction"], event_id=req.event_id, context=ctx)
                    spent += v.cost_usd; done += 1
        conn.commit()
        print(f"  {done:,} judged, {failed} failed, ${spent:.2f}", flush=True)
    return {"judged": done, "failed": failed, "usd": round(spent, 4), "left": len(todo) - done - failed}


def examples(conn, purpose: str = PURPOSE, kinds=("summary",)) -> list[dict]:
    """One chat example per recorded call: exactly the v4 messages the judge saw and gpt-5.4-mini's JSON answer."""
    from pipeline.measure.run import rendered
    has_pairs = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'e11_pairs'").fetchone()
    join = ("LEFT JOIN e11_pairs p ON p.first_article = c.first_article AND p.second_article = c.second_article"
            if has_pairs else "LEFT JOIN (SELECT NULL split, NULL batch, NULL day, NULL first_article) p ON 0")
    rows = conn.execute(f"""SELECT c.*, p.split, p.batch, p.day FROM judge_calls c {join}
                            WHERE c.purpose = ? AND c.prompt_version = 'v4' ORDER BY c.at, c.call_id""", (purpose,)).fetchall()
    out = []
    for r in rows:
        if r["text_kind"] not in kinds:
            continue
        a = conn.execute("SELECT * FROM articles WHERE id = ?", (r["first_article"],)).fetchone()
        b = conn.execute("SELECT * FROM articles WHERE id = ?", (r["second_article"],)).fetchone()
        hist = store.headline_history(conn, [r["first_article"], r["second_article"]])
        answer = json.dumps({ax: v["verdict"] if v["verdict"] in ("A", "B") else "na"
                             for ax, v in P.parse(r["raw"]).items()}, separators=(",", ":"))
        msgs = P.messages(rendered(dict(a), hist, r["text_kind"]), rendered(dict(b), hist, r["text_kind"]), r["direction"], "v4")
        out.append({"messages": msgs + [{"role": "assistant", "content": answer}], "call_id": r["call_id"],
                    "split": r["split"] or "train", "batch": r["batch"] or purpose, "source": purpose,
                    "day": r["day"] or ""})
    return out


def export(out_dir: Path, with_e10: bool = False, conn=None) -> dict:
    conn = conn or connect()
    rows = examples(conn)
    if with_e10 and E10_DB.exists():
        rows += [{**e, "split": "train"} for e in examples(store.connect(E10_DB), "e10", kinds=("summary", "full"))]
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = Counter(r["split"] for r in rows)
    for split in ("train", "val"):
        with open(out_dir / f"{split}.jsonl", "w") as f:
            for r in rows:
                if r["split"] == split:
                    f.write(json.dumps({"messages": r["messages"], "call_id": r["call_id"], "batch": r["batch"],
                                        "source": r["source"], "day": r["day"]}, ensure_ascii=False) + "\n")
    return dict(counts)


def status(conn=None) -> str:
    conn = conn or connect()
    lines = []
    for b, n, days in conn.execute("SELECT batch, count(*), count(DISTINCT day) FROM e11_pairs GROUP BY 1 ORDER BY 1"):
        j = conn.execute("""SELECT count(*), coalesce(sum(cost_usd), 0) FROM judge_calls c JOIN e11_pairs p
                            ON p.first_article = c.first_article AND p.second_article = c.second_article
                            WHERE c.purpose = ? AND p.batch = ?""", (PURPOSE, b)).fetchone()
        outlets = conn.execute("SELECT count(DISTINCT o) FROM (SELECT first_outlet o FROM e11_pairs WHERE batch = ? "
                               "UNION SELECT second_outlet FROM e11_pairs WHERE batch = ?)", (b, b)).fetchone()[0]
        lines.append(f"{b}: {n:,} pairs over {days} days and {outlets} outlets; judged {j[0]:,} (${j[1]:.2f})")
    return "\n".join(lines) or "no batches yet"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["sample", "judge", "export", "status"])
    ap.add_argument("--batch")
    ap.add_argument("--pairs", type=int, default=3000)
    ap.add_argument("--budget-usd", type=float, default=5.0)
    ap.add_argument("--out", type=Path, default=Path("data/distil/sft"))
    ap.add_argument("--with-e10", action="store_true", help="add E10's fresh US/UK verdicts to training (never a test)")
    args = ap.parse_args()
    if args.what in ("sample", "judge") and not args.batch:
        ap.error(f"{args.what} needs --batch")
    if args.what == "sample":
        print(f"{sample(args.batch, args.pairs):,} pairs chosen for {args.batch}")
    elif args.what == "judge":
        print(judge(args.batch, args.budget_usd))
    elif args.what == "export":
        print(export(args.out, args.with_e10))
    print(status())


if __name__ == "__main__":
    main()
