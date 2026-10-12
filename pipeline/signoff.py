"""Stage sign-off: one scorecard per pipeline stage — automatic checks with targets, then the checks only a human can do.

    uv run python -m pipeline.signoff --stage collect      # 1 · Collect  (1.1 Sources M1, 1.2 Ingest M2, 1.3 Backfill M2b)

PASS = meets the target · WARN = below target but explained (see the note) · FAIL = below target, unexplained.
The human checks print real articles with their URLs: open each one and compare. Tick the result in VERIFY.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
from collections import Counter

from pipeline import store
from pipeline.ingest.eval import LIVE_SINCE
from pipeline.ingest.report import HISTORY_YEARS, SUSPECT_WORDS, collect
from pipeline.sources.registry import load_outlets

KNOWN_ISSUES = {   # outlet -> why it fails, with the BACKLOG / assumption that covers it
    "leparisien": "blocks article pages and its feed has no summaries → nothing judgeable (extra tier, A33/A38)",
}


class Card:
    def __init__(self):
        self.rows = []

    def check(self, step: str, what: str, value: float, target: float, shown: str, note: str = "", higher=True):
        ok = value >= target if higher else value <= target
        status = "PASS" if ok else ("WARN" if note else "FAIL")
        self.rows.append((step, what, shown, f"{'≥' if higher else '≤'} {target:g}", status, note))

    def print(self, title: str):
        print(f"\n{title}\n" + "─" * len(title))
        for step, what, shown, target, status, note in self.rows:
            mark = {"PASS": "✓", "WARN": "!", "FAIL": "✗"}[status]
            print(f" {mark} {status:<4}  {step:<5} {what:<52} {shown:>14}   target {target:<8}" + (f"\n{'':>14}↳ {note}" if note else ""))
        n = Counter(r[4] for r in self.rows)
        print(f"\n {n['PASS']} pass · {n['WARN']} explained warnings · {n['FAIL']} unexplained failures")


def collect_stage(conn, samples: int, seed: int):
    outlets = load_outlets()
    ref = [o for o in outlets if o.tier == "reference"]
    health = store.latest_health(conn)
    today = dt.datetime.now(dt.UTC).date()
    _, stats = collect(conn, today)
    card = Card()

    # 1.1 Sources (M1)
    per_country = Counter(o.country for o in ref)
    card.check("1.1", "reference set: 5 DNR-2026 outlets in each of 10 countries", min(per_country.values()) * (len(per_country) == 10),
               5, f"{len(ref)} in {len(per_country)}")
    usable = [o for o in ref if o.id in health and health[o.id].feeds_ok > 0]
    card.check("1.1", "reference outlets with a working feed", len(usable) / len(ref) * 100, 100, f"{len(usable)}/{len(ref)}")
    decided = [o for o in outlets if o.id in health and health[o.id].robots_allows_articles is not None]
    card.check("1.1", "outlets with a robots.txt decision recorded", len(decided) / len(outlets) * 100, 95,
               f"{len(decided)}/{len(outlets)}")
    tdm = sorted(o.id for o in outlets if o.id in health and not health[o.id].full_text_allowed)
    age = max((health[o.id].checked_at for o in outlets if o.id in health), default="never")
    card.check("1.1", "feed health checked within the last 2 days",
               2 - (today - dt.date.fromisoformat(age[:10])).days if age != "never" else -1, 0, age[:16].replace("T", " "))

    # 1.2 Ingest (M2)
    s_ref = [stats[o.id] for o in ref]
    fresh = sum(s["recent72"] >= 5 for s in s_ref)
    card.check("1.2", "reference outlets with ≥ 5 judgeable articles in 72 h", fresh / len(ref) * 100, 100,
               f"{fresh}/{len(ref)}", "" if fresh == len(ref) else "run the ingest again: freshness decays when it hasn't run")
    extra_bad = [o.id for o in outlets if o.tier != "reference" and stats[o.id]["recent72"] < 5]
    card.check("1.2", "extra outlets with ≥ 5 judgeable articles in 72 h", 100 * (1 - len(extra_bad) / max(1, len(outlets) - len(ref))),
               100, f"{len(outlets) - len(ref) - len(extra_bad)}/{len(outlets) - len(ref)}",
               "; ".join(f"{o}: {KNOWN_ISSUES[o]}" for o in extra_bad)
               if extra_bad and all(o in KNOWN_ISSUES for o in extra_bad) else "")
    live = conn.execute("""SELECT COUNT(*) n, SUM(published LIKE '____-__-__T%Z') d FROM articles
                           WHERE canonical_id IS NULL AND text_source NOT LIKE 'wayback-%' AND ingested_at >= ?""",
                        (LIVE_SINCE,)).fetchone()
    card.check("1.2", "live articles with a parseable publication date", 100 * live["d"] / live["n"], 95,
               f"{100 * live['d'] / live['n']:.1f} %")
    dup = conn.execute("""SELECT COUNT(*) - COUNT(DISTINCT outlet_id || '|' || lower(title)) FROM articles
                          WHERE canonical_id IS NULL AND text_source NOT LIKE 'wayback-%'""").fetchone()[0]
    n_live_all = conn.execute("SELECT COUNT(*) FROM articles WHERE canonical_id IS NULL AND text_source NOT LIKE 'wayback-%'").fetchone()[0]
    card.check("1.2", "repeated headline within an outlet (possible duplicates)", 100 * dup / n_live_all, 2,
               f"{100 * dup / n_live_all:.1f} %", higher=False)
    heads = [r[0] or "" for r in conn.execute("SELECT lower(substr(full_text, 1, 300)) FROM articles "
                                               "WHERE canonical_id IS NULL AND full_text IS NOT NULL")]
    sus = sum(any(w in h for w in SUSPECT_WORDS) for h in heads)
    card.check("1.2", "full texts starting like a cookie/consent/subscribe wall", 100 * sus / len(heads), 5,
               f"{100 * sus / len(heads):.1f} %", higher=False)

    # 1.3 Backfill (M2b)
    jobs = Counter(r[0] for r in conn.execute("SELECT status FROM backfill_jobs"))
    tried = sum(jobs.values())
    card.check("1.3", "attempted snapshots that yielded articles", 100 * jobs["ok"] / max(tried, 1), 80,
               f"{jobs['ok']}/{tried}")
    wb = conn.execute("""SELECT published, feed_url FROM articles WHERE text_source LIKE 'wayback-%'
                         AND published LIKE '____-__-__T%Z' AND feed_url LIKE 'wayback:%@%'""").fetchall()
    near = 0
    for r in wb:
        snap = r["feed_url"].split("@")[-1][:8]
        d = abs((dt.date.fromisoformat(r["published"][:10]) - dt.date(int(snap[:4]), int(snap[4:6]), int(snap[6:8]))).days)
        near += d <= 60
    card.check("1.3", "archived articles dated within 60 days of their snapshot", 100 * near / max(len(wb), 1), 90,
               f"{near}/{len(wb)}")
    attempted_years = conn.execute("SELECT COUNT(DISTINCT outlet_id || substr(date, 1, 4)) FROM backfill_jobs").fetchone()[0]
    card.check("1.3", "outlet-years attempted so far (backfill in progress)",
               100 * attempted_years / (len(outlets) * HISTORY_YEARS), 100, f"{attempted_years}/{len(outlets) * HISTORY_YEARS}", "the overnight backfill is still working through the grid")
    kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM articles WHERE kind IS NOT NULL GROUP BY 1").fetchall())
    card.print("1 · Collect — automatic checks")
    print(f"\n   info: excluded as non-articles (A38): {kinds.get('headline_only', 0)} headline-only (video clips, blocked "
          f"outlets), {kinds.get('page', 0)} site/hub pages, {kinds.get('rundown', 0)} multi-topic rundowns")
    print(f"\n   info: {len(tdm)} outlets are headline + summary only by robots.txt / TDM policy (M1): {', '.join(tdm)}."
          f"\n         Outlets that block bots at fetch time (A33: lemonde, dailymail, liberation) show up as orange in the report.")

    # human checks
    rng = random.Random(seed)
    print("\n1 · Collect — checks only you can do (≈ 10 min)\n" + "─" * 47)
    print(" A. Open reports/m2_coverage.png (regenerate: uv run python -m pipeline.ingest.report).")
    print("    Every red outlet name should match an explanation above or in BACKLOG.md.")
    rows = conn.execute("""SELECT outlet_id, title, published, url, text_source, substr(coalesce(full_text, summary, ''), 1, 160) t
                           FROM articles WHERE canonical_id IS NULL AND text_source NOT LIKE 'wayback-%' AND ingested_at >= ?
                           AND outlet_id IN (""" + ",".join("?" * len(ref)) + ")", (LIVE_SINCE, *[o.id for o in ref])).fetchall()
    print(f"\n B. {samples} random live articles. Open each URL and compare: same headline? same date (UTC)? does the stored")
    print("    text start where the article starts — not a menu, cookie banner or a different story?")
    for k, r in enumerate(rng.sample(rows, min(samples, len(rows))), 1):
        print(f"\n   B{k}. [{r['outlet_id']}] {r['title']}\n       published {r['published']} · stored as {r['text_source']}"
              f"\n       {r['url']}\n       stored text: {r['t'].strip()}…")
    wrows = conn.execute("""SELECT outlet_id, title, published, url, feed_url, substr(coalesce(full_text, summary, ''), 1, 160) t
                            FROM articles WHERE text_source LIKE 'wayback-%' AND full_text IS NOT NULL""").fetchall()
    print("\n C. 3 random archived articles. Open the archive link: is it the same article, from around that date?")
    for k, r in enumerate(rng.sample(wrows, min(3, len(wrows))), 1):
        snap = r["feed_url"].split("@")[-1]
        print(f"\n   C{k}. [{r['outlet_id']}] {r['title']}\n       published {r['published']} · found on snapshot {snap}"
              f"\n       https://web.archive.org/web/{snap[:8]}/{r['url']}\n       stored text: {r['t'].strip()}…")
    print("\n D. Policy spot-check: open https://www.lefigaro.fr/.well-known/tdmrep.json (or its HTTP headers) — Le Figaro")
    print("    should declare a TDM reservation, which is why it is summary-only. Then open https://www.svt.se/robots.txt —")
    print("    SVT should allow article pages, which is why we read its full text.")
    print("\nWhen A–D look right, tick '1 · Collect' in VERIFY.md (or tell Claude what was wrong, with the B/C number).")


STAGES = {"collect": collect_stage}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=sorted(STAGES), default="collect")
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0, help="change to draw different sample articles")
    args = ap.parse_args()
    STAGES[args.stage](store.connect(), args.samples, args.seed)


if __name__ == "__main__":
    main()
