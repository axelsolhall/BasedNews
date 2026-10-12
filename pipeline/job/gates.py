"""Gates: after each job step, a few numbers from the module's eval decide whether its output may go on (A53).

A failed gate stops the job. In production the database is then not uploaded, so the next step and the site keep
the last good data, and the step's record says which number was out of range. Thresholds are set from the data on
2026-10-12; each one is a constant here, so a wrong threshold is a one-line change.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

# A53: thresholds (measured values on 2026-10-12 in the comments)
MIN_USABLE_OUTLET_SHARE = 0.80      # sources: feeds OK and newest item < 48 h (72 of 73 = 99 %)
MAX_NEWEST_ARTICLE_AGE_H = 6.0      # ingest: something was published in the last 6 h (≈ 1,600 articles/day)
MIN_ACTIVE_OUTLET_SHARE = 0.75      # ingest: outlets with ≥ 1 live article in 72 h (99 %)
MAX_CANONICAL_DROP = 0.05           # normalize: merging copies may not remove > 5 % of the articles in one run
MIN_EMBED_COVERAGE = 0.98           # embed: story-window articles with a vector for the active model (100 %)
MAX_LARGEST_STORY_SHARE = 0.05      # stories: one cluster holding > 5 % of the window means chaining (0.8 %)
MIN_MULTI_OUTLET_STORIES = 100      # stories: stories with ≥ 2 outlets, the pairs the judge can compare (701)
MAX_FAILED_CALL_SHARE = 0.20        # measure: judge calls that failed after all retries
BUDGET_SLACK_USD = 0.02             # measure: spend may exceed the run budget by one in-flight batch at most
MAX_HOT_DB_BYTES = 1_000_000_000    # retention: the SQLite-in-S3 upgrade trigger (ADR-010)


@dataclass
class Gate:
    ok: bool
    metrics: dict = field(default_factory=dict)
    reason: str = ""

    def as_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "metrics": self.metrics}


def _check(metrics: dict, rules: list[tuple[bool, str]]) -> Gate:
    failed = [msg for ok, msg in rules if not ok]
    return Gate(not failed, metrics, "; ".join(failed))


def _ago(hours: float) -> str:
    return (dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def sources(conn, ctx) -> Gate:
    from pipeline import store
    from pipeline.sources.registry import load_outlets
    health = store.latest_health(conn)
    ids = [o.id for o in load_outlets()]
    usable = [i for i in ids if i in health and health[i].feeds_ok > 0
              and health[i].newest_item_age_h is not None and health[i].newest_item_age_h < 48]
    share = len(usable) / len(ids) if ids else 0.0
    m = {"outlets": len(ids), "usable": len(usable), "usable_share": round(share, 3),
         "text_blocked": sum(1 for i in ids if i in health and not health[i].full_text_allowed)}
    return _check(m, [(share >= MIN_USABLE_OUTLET_SHARE, f"usable outlets {share:.0%} < {MIN_USABLE_OUTLET_SHARE:.0%}")])


def _live(where: str = "") -> str:
    return ("FROM articles WHERE canonical_id IS NULL AND kind IS NULL AND "
            "(text_source IS NULL OR text_source NOT LIKE 'wayback-%')" + (f" AND {where}" if where else ""))


def ingest(conn, ctx) -> Gate:
    from pipeline.sources.registry import load_outlets
    newest = conn.execute(f"SELECT MAX(published) {_live()} AND published <= ?", (_ago(-1),)).fetchone()[0]
    age_h = ((dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(newest.replace("Z", "+00:00"))).total_seconds() / 3600
             if newest else float("inf"))
    active = {r[0] for r in conn.execute(f"SELECT DISTINCT outlet_id {_live('published >= ?')}", (_ago(72),))}
    ids = {o.id for o in load_outlets()}
    share = len(active & ids) / len(ids) if ids else 0.0
    m = {"new_articles": ctx["delta"].get("articles", 0), "headline_versions": ctx["delta"].get("headlines", 0),
         "newest_article_age_h": round(age_h, 2), "active_outlets_72h": len(active & ids), "outlets": len(ids),
         "active_share": round(share, 3)}
    return _check(m, [(age_h <= MAX_NEWEST_ARTICLE_AGE_H, f"newest article {age_h:.1f} h old > {MAX_NEWEST_ARTICLE_AGE_H} h"),
                      (share >= MIN_ACTIVE_OUTLET_SHARE, f"active outlets {share:.0%} < {MIN_ACTIVE_OUTLET_SHARE:.0%}")])


def normalize(conn, ctx) -> Gate:
    before, after = ctx["before"].get("canonical_articles", 0), ctx["after"].get("canonical_articles", 0)
    drop = (before - after) / before if before else 0.0
    m = {"canonical_before": before, "canonical_after": after, "drop_share": round(drop, 4)}
    return _check(m, [(drop <= MAX_CANONICAL_DROP, f"canonical articles fell {drop:.1%} > {MAX_CANONICAL_DROP:.0%}")])


def embed(conn, ctx) -> Gate:
    from pipeline.embed.run import MODEL
    n, have = conn.execute(f"""SELECT COUNT(*), SUM(EXISTS (SELECT 1 FROM embeddings e WHERE e.article_id = a.id
                               AND e.model = ?)) FROM (SELECT id {_live('published >= ?')}) a""",
                           (MODEL, _ago(24 * 7))).fetchone()
    cov = (have or 0) / n if n else 1.0
    m = {"model": MODEL, "window_articles": n, "embedded": have or 0, "coverage": round(cov, 4),
         "new_vectors": ctx["delta"].get("embeddings", 0)}
    return _check(m, [(cov >= MIN_EMBED_COVERAGE, f"embedding coverage {cov:.1%} < {MIN_EMBED_COVERAGE:.0%}")])


def stories(conn, ctx) -> Gate:
    from pipeline import store
    run = store.latest_event_run(conn)
    rows = conn.execute("""SELECT m.event_id, a.outlet_id FROM event_members m JOIN articles a ON a.id = m.article_id
                           WHERE m.run_id = ?""", (run,)).fetchall() if run else []
    size, outlets = {}, {}
    for r in rows:
        size[r[0]] = size.get(r[0], 0) + 1
        outlets.setdefault(r[0], set()).add(r[1])
    largest = max(size.values()) / len(rows) if rows else 0.0
    multi = sum(1 for s in outlets.values() if len(s) >= 2)
    fresh = bool(run) and run[:20] >= ctx["started_at"][:20]
    m = {"run": run, "articles": len(rows), "stories": len(size), "multi_outlet_stories": multi,
         "largest_story_share": round(largest, 4)}
    return _check(m, [(fresh, "no new story run was written"),
                      (largest <= MAX_LARGEST_STORY_SHARE, f"largest story holds {largest:.1%} of the window (chaining?)"),
                      (multi >= MIN_MULTI_OUTLET_STORIES, f"only {multi} stories with ≥ 2 outlets")])


def measure(conn, ctx) -> Gate:
    from pipeline.judge.calllog import coverage
    cov = coverage(conn, ctx["started_at"])
    spent = ctx["delta"].get("spend_usd", 0.0)
    budget = ctx.get("budget_usd")
    attempts = cov["paid_calls"] + cov["failed"]
    fail_share = cov["failed"] / attempts if attempts else 0.0
    new_fit = conn.execute("SELECT COUNT(*) FROM positions WHERE fitted_at >= ?", (ctx["started_at"],)).fetchone()[0]
    m = {**cov, "failed_share": round(fail_share, 3), "spent_usd": round(spent, 4), "budget_usd": budget,
         "positions_written": new_fit}
    rules = [(cov["logged"] == cov["paid_calls"], f"{cov['paid_calls'] - cov['logged']} paid calls without a log row (A51)"),
             (fail_share <= MAX_FAILED_CALL_SHARE, f"{fail_share:.0%} of judge calls failed"),
             (cov["paid_calls"] == 0 or new_fit > 0, "calls were made but no positions were saved")]
    if budget is not None:
        rules.append((spent <= budget + BUDGET_SLACK_USD, f"spent ${spent:.2f} > budget ${budget:.2f}"))
    return _check(m, rules)


def export(conn, ctx) -> Gate:
    from pipeline.export.run import FILE, check, out_dir
    res = check(out_dir() / FILE)
    return Gate(res.pop("ok"), res, res.pop("reason", ""))


def retention(conn, ctx) -> Gate:
    from pipeline.retention.run import verify
    res = verify(conn)
    return _check(res, [(res["db_bytes"] <= MAX_HOT_DB_BYTES, f"hot database {res['db_bytes'] / 1e9:.2f} GB > 1 GB: time for Postgres (ADR-010)"),
                        (res["integrity"] == "ok", f"integrity check: {res['integrity']}"),
                        (res["refit_max_abs_diff"] is None or res["refit_max_abs_diff"] < 1e-6,
                         f"refit from kept verdicts differs from stored positions by {res['refit_max_abs_diff']}")])
