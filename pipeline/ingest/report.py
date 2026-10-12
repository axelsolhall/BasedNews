"""M2 report: coverage table + publication-date figure, for review and for the README.

    uv run python -m pipeline.ingest.report [--samples 2] [--readme]

Writes reports/m2_coverage.png and reports/m2_ingest.md. Both hold counts only, no article text (A10);
--samples prints a few text snippets to the terminal for spot-checking extraction quality;
--readme refreshes the country table in README.md between the m2 markers.

Two kinds of data, told apart by provenance:
  history  archive.org backfill (1.3 / M2b), ~4 articles per outlet per snapshot date, same dates for every outlet
  live     this pipeline's RSS ingest (1.2 / M2), from LIVE_SINCE on
(The pre-pipeline scraper's Nordic-only rows are retired to `articles_legacy`, A37.)
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import statistics
from collections import Counter, defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LogNorm  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

from pipeline import store  # noqa: E402
from pipeline.ingest.eval import LIVE_SINCE, MIN_ANY_TEXT_PCT, MIN_DATED_PCT, MIN_RECENT_72H  # noqa: E402
from pipeline.ingest.run import MIN_TEXT_CHARS  # noqa: E402
from pipeline.sources.registry import load_outlets  # noqa: E402

HISTORY_YEARS = 10            # same horizon as the backfill (--years 10)
RECENT_DAYS = 14              # = M4 embed window (--days 14)
SUSPECT_WORDS = ("cookie", "javascript", "consent", "samtyck", "einwillig", "abonn", "subscribe")

C_FULL, C_SUMMARY, C_TITLE = "#1b7f79", "#e0a43a", "#cfcfcf"


def era(text_source: str | None, ingested_at: str | None) -> str:
    if (text_source or "").startswith("wayback"):
        return "history"
    return "live" if (ingested_at or "") >= LIVE_SINCE else "legacy"


def parse_day(published: str | None) -> dt.date | None:
    if not published or len(published) < 20 or published[4] != "-" or not published.endswith("Z"):
        return None
    try:
        return dt.date.fromisoformat(published[:10])
    except ValueError:
        return None


def collect(conn, today: dt.date):
    outlets = load_outlets()
    first_year = today.year - HISTORY_YEARS
    rows = conn.execute("""SELECT outlet_id, title, published, ingested_at, text_source,
                                  LENGTH(full_text) flen, summary IS NOT NULL has_sum,
                                  lower(substr(full_text, 1, 300)) head
                           FROM articles WHERE canonical_id IS NULL AND kind IS NULL""").fetchall()
    # M3 copies count once (A34); rundowns and site pages are not articles (A38)
    attempted = defaultdict(set)   # outlet -> years the backfill has tried (any status)
    for r in conn.execute("SELECT outlet_id, substr(date, 1, 4) y FROM backfill_jobs"):
        attempted[r["outlet_id"]].add(int(r["y"]))

    now = dt.datetime.now(dt.UTC)
    cut72 = (now - dt.timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
    by = defaultdict(list)
    for r in rows:
        by[r["outlet_id"]].append(r)

    stats = {}
    for o in outlets:
        rs = by.get(o.id, [])
        n = len(rs)
        days = [parse_day(r["published"]) for r in rs]
        full = [r for r in rs if (r["flen"] or 0) >= MIN_TEXT_CHARS]
        summ = sum(1 for r in rs if (r["flen"] or 0) < MIN_TEXT_CHARS and r["has_sum"])
        dated = sum(d is not None for d in days)
        recent72 = sum(1 for r, d in zip(rs, days) if d and r["published"] >= cut72)
        recent14 = sum(1 for d in days if d and (today - d).days < RECENT_DAYS and d <= today)
        hist_years = {d.year for d in days if d and first_year <= d.year < today.year}
        eras = Counter(era(r["text_source"], r["ingested_at"]) for r in rs)
        live = [(r, d) for r, d in zip(rs, days) if era(r["text_source"], r["ingested_at"]) == "live"]
        live_any = 100 * sum(1 for r, _ in live if (r["flen"] or 0) >= MIN_TEXT_CHARS or r["has_sum"]) / len(live) if live else 0.0
        live_dated = 100 * sum(1 for _, d in live if d is not None) / len(live) if live else 0.0
        pct = (lambda k: 100 * k / n) if n else (lambda k: 0.0)
        s = dict(
            outlet=o, n=n, days=days, eras=eras, recent72=recent72, recent14=recent14,
            hist_years=len(hist_years), pending_years=sorted(set(range(first_year, today.year)) - attempted[o.id]),
            full_pct=pct(len(full)), summary_pct=pct(summ), title_pct=pct(n - len(full) - summ),
            any_pct=pct(len(full) + summ), dated_pct=pct(dated),
            dup_pct=100 * (1 - len({(r["title"] or "").lower() for r in rs}) / n) if n else 0.0,
            suspect_pct=100 * sum(any(w in (r["head"] or "") for w in SUSPECT_WORDS) for r in full) / len(full)
            if full else 0.0,
            median_chars=int(statistics.median(r["flen"] for r in full)) if full else 0,
            undated=n - dated,
            future=sum(1 for d in days if d and d > today),
            live_any_pct=live_any, live_dated_pct=live_dated,
        )
        s["ok"] = (recent72 >= MIN_RECENT_72H and live_any >= MIN_ANY_TEXT_PCT and live_dated >= MIN_DATED_PCT)
        stats[o.id] = s
    return outlets, stats


# ---------------------------------------------------------------- figure

def figure(outlets, stats, today: dt.date, path: str):
    countries = list(dict.fromkeys(o.country for o in outlets))
    layout = []                                   # (kind, payload) top to bottom
    for c in countries:
        layout.append(("country", c))
        group = sorted((o for o in outlets if o.country == c),
                       key=lambda o: (o.tier != "reference", -(o.dnr_reach_pct or 0)))
        layout += [("outlet", o) for o in group]
    nrows = len(layout)

    first = dt.date(today.year - HISTORY_YEARS, 1, 1)
    months = [(y, m) for y in range(first.year, today.year + 1) for m in range(1, 13) if (y, m) <= (today.year, today.month)]
    midx = {ym: i for i, ym in enumerate(months)}
    recent = [today - dt.timedelta(days=k) for k in range(RECENT_DAYS - 1, -1, -1)]
    didx = {d: i for i, d in enumerate(recent)}

    A = np.zeros((nrows, len(months)))
    B = np.zeros((nrows, len(recent)))
    for i, (kind, o) in enumerate(layout):
        if kind != "outlet":
            continue
        for d in stats[o.id]["days"]:
            if d is None:
                continue
            if (d.year, d.month) in midx:
                A[i, midx[(d.year, d.month)]] += 1
            if d in didx:
                B[i, didx[d]] += 1
    vmax = max(A.max(), B.max(), 10)
    norm = LogNorm(vmin=1, vmax=vmax)
    cmap = plt.get_cmap("YlGnBu").copy()
    cmap.set_bad((0, 0, 0, 0))

    fig = plt.figure(figsize=(16, 0.165 * nrows + 2.4))
    gs = fig.add_gridspec(1, 3, width_ratios=[6.2, 1.5, 1.6], wspace=0.04,
                          left=0.13, right=0.97, top=1 - 1.7 / (0.165 * nrows + 2.4), bottom=1.0 / (0.165 * nrows + 2.4))
    axA, axB, axC = (fig.add_subplot(gs[0, k]) for k in range(3))
    country_rows = [i for i, (k, _) in enumerate(layout) if k == "country"]

    for ax, M in ((axA, A), (axB, B)):
        Mm = np.ma.masked_where(M == 0, M)
        for i in country_rows:
            Mm[i, :] = np.ma.masked
        ax.set_facecolor("#f3f3f3")
        im = ax.imshow(Mm, aspect="auto", cmap=cmap, norm=norm, interpolation="nearest", zorder=1)
        for i in country_rows:
            ax.axhspan(i - 0.5, i + 0.5, color="white", zorder=2)
        ax.set_ylim(nrows - 0.5, -0.5)

    # hatch outlet-years the backfill has not attempted yet: "pending", as opposed to "tried, found nothing"
    for i, (kind, o) in enumerate(layout):
        if kind != "outlet":
            continue
        for y in stats[o.id]["pending_years"]:
            x0 = midx[(y, 1)] - 0.5
            axA.add_patch(Rectangle((x0, i - 0.5), 12, 1, fill=False, hatch="////", edgecolor="#b9b9b9", lw=0, zorder=0.5))

    # y labels: outlet names, reference tier marked; country headings in the spacer rows
    labels = []
    for kind, p in layout:
        labels.append(p if kind == "country" else ("● " if p.tier == "reference" else "") + p.name)
    axA.set_yticks(range(nrows))
    axA.set_yticklabels(labels, fontsize=7.2)
    for i, (kind, p) in enumerate(layout):
        t = axA.get_yticklabels()[i]
        if kind == "country":
            t.set_fontweight("bold"); t.set_fontsize(8.2)
        elif p.tier != "reference":
            t.set_color("#8a8a8a")
        if kind == "outlet" and not stats[p.id]["ok"]:
            t.set_color("#c0392b")
    axA.tick_params(axis="y", length=0)

    years = sorted({y for y, _ in months})
    axA.set_xticks([midx[(y, 1)] - 0.5 for y in years])
    axA.set_xticklabels([str(y) for y in years], fontsize=8, ha="left")
    axA.set_xlim(-0.5, len(months) - 0.5)
    axA.set_title("Articles per month, by publication date", fontsize=9.5, loc="left", pad=36)

    def era_label(x0, x1, text):
        y = 1.012
        axA.plot([x0, x1], [y, y], transform=axA.get_xaxis_transform(), color="#888", lw=1, clip_on=False)
        axA.text((x0 + x1) / 2, y + 0.004, text, transform=axA.get_xaxis_transform(), ha="center", va="bottom",
                 fontsize=7, color="#555", linespacing=1.0)
    live = (int(LIVE_SINCE[:4]), int(LIVE_SINCE[5:7]))
    era_label(-0.5, midx.get(live, len(months)) - 1.5,
              "archive.org sample: ≈ 4 articles per outlet per snapshot, same dates for every outlet (backfill)")
    if live in midx:
        era_label(midx[live] - 0.5, len(months) - 0.5, "live\nRSS")
    for x in range(len(years)):
        axA.axvline(midx[(years[x], 1)] - 0.5, color="white", lw=0.8, zorder=2.5)

    axB.set_yticks([])
    tick = list(range(0, RECENT_DAYS, 3))
    axB.set_xticks(tick)
    axB.set_xticklabels([recent[k].strftime("%d %b") for k in tick], fontsize=7, rotation=0)
    axB.set_title(f"Per day, last {RECENT_DAYS} days", fontsize=9.5, loc="left")

    # text availability: what the judge will be able to read
    for i, (kind, o) in enumerate(layout):
        if kind != "outlet":
            continue
        s = stats[o.id]
        left = 0
        for w, c in ((s["full_pct"], C_FULL), (s["summary_pct"], C_SUMMARY), (s["title_pct"], C_TITLE)):
            axC.barh(i, w, left=left, color=c, height=0.78, lw=0)
            left += w
        axC.text(101.5, i, f"{s['n']:,}", va="center", fontsize=6.6, color="#444")
    axC.set_ylim(nrows - 0.5, -0.5)
    axC.set_xlim(0, 100)
    axC.set_yticks([])
    axC.set_xticks([0, 50, 100])
    axC.set_xticklabels(["0", "50", "100 %"], fontsize=7)
    axC.set_title("Text available    (n)", fontsize=9.5, loc="left")
    for sp in ("top", "right", "left"):
        axC.spines[sp].set_visible(False)

    total = sum(s["n"] for s in stats.values())
    ok = sum(s["ok"] for s in stats.values())
    fig.text(0.13, 1 - 0.38 / (0.165 * nrows + 2.4), "BasedNews · M2 ingest coverage", fontsize=14, fontweight="bold")
    fig.text(0.13, 1 - 0.70 / (0.165 * nrows + 2.4),
             f"{total:,} articles · {len(outlets)} outlets · {len(set(o.country for o in outlets))} countries · "
             f"{ok}/{len(outlets)} outlets pass M2 checks · generated {today.isoformat()}",
             fontsize=9, color="#444")

    # legend row: colour scale, cell/bar keys, name colours
    H = 0.165 * nrows + 2.4
    cax = fig.add_axes([0.13, 0.42 / H, 0.15, 0.13 / H])
    cb = fig.colorbar(im, cax=cax, orientation="horizontal")
    cb.ax.tick_params(labelsize=7)
    cb.set_label("articles per cell (log)", fontsize=7.5)
    handles = [Patch(facecolor="#f3f3f3", edgecolor="#cfcfcf", lw=0.5, label="no articles"),
               Patch(facecolor="#f3f3f3", edgecolor="#9a9a9a", hatch="////", lw=0.5, label="backfill not attempted yet"),
               Patch(color=C_FULL, label="full text"), Patch(color=C_SUMMARY, label="summary only"),
               Patch(color=C_TITLE, label="title only")]
    fig.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.31, 0.30 / H), ncol=5, fontsize=7.5,
               frameon=False, handlelength=1.4, columnspacing=1.6)
    x = 0.31
    for txt, c in (("● reference outlet (DNR 2026 top 5)", "#222"), ("grey name: extra outlet", "#8a8a8a"),
                   ("red name: fails M2 checks", "#c0392b")):
        fig.text(x, 0.12 / H, txt, fontsize=7.5, color=c)
        x += 0.02 + 0.0042 * len(txt)

    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------- tables

def country_table(outlets, stats) -> list[str]:
    out = ["| Country | Outlets (ref / all) | Articles | Last 72 h | Last 14 d | History years (of 10, mean) "
           "| Full text | Summary only | Dated | Pass |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    tot = Counter()
    for c in dict.fromkeys(o.country for o in outlets):
        ss = [stats[o.id] for o in outlets if o.country == c]
        n = sum(s["n"] for s in ss)
        w = lambda k: sum(s[k] * s["n"] for s in ss) / n if n else 0.0   # article-weighted share
        ref = sum(s["outlet"].tier == "reference" for s in ss)
        row = dict(n=n, r72=sum(s["recent72"] for s in ss), r14=sum(s["recent14"] for s in ss),
                   ok=sum(s["ok"] for s in ss), k=len(ss))
        tot.update(row)
        out.append(f"| {c} | {ref} / {len(ss)} | {n:,} | {row['r72']:,} | {row['r14']:,} | "
                   f"{statistics.mean(s['hist_years'] for s in ss):.1f} | {w('full_pct'):.0f} % | {w('summary_pct'):.0f} % | "
                   f"{w('dated_pct'):.0f} % | {row['ok']}/{row['k']} |")
    allss = list(stats.values())
    w = lambda k: sum(s[k] * s["n"] for s in allss) / tot["n"]
    out.append(f"| **All** | {sum(o.tier == 'reference' for o in outlets)} / {len(outlets)} | **{tot['n']:,}** | "
               f"{tot['r72']:,} | {tot['r14']:,} | {statistics.mean(s['hist_years'] for s in allss):.1f} | "
               f"{w('full_pct'):.0f} % | {w('summary_pct'):.0f} % | {w('dated_pct'):.0f} % | {tot['ok']}/{tot['k']} |")
    return out


def outlet_table(outlets, stats) -> list[str]:
    out = ["| Outlet | Country | Tier | DNR reach | Articles | history / live | 72 h | History yrs | "
           "Full text | Summary | Dated | Dup titles | Boilerplate suspect | Median chars | Status |",
           "|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for o in outlets:
        s = stats[o.id]
        e = s["eras"]
        reasons = []
        if s["recent72"] < MIN_RECENT_72H:
            reasons.append(f"<{MIN_RECENT_72H} in 72 h")
        if s["live_any_pct"] < MIN_ANY_TEXT_PCT:
            reasons.append(f"text {s['live_any_pct']:.0f} %")
        if s["live_dated_pct"] < MIN_DATED_PCT:
            reasons.append(f"dates {s['live_dated_pct']:.0f} %")
        status = "OK" if s["ok"] else "CHECK: " + ", ".join(reasons)
        out.append(f"| {o.name} | {o.country} | {o.tier} | {o.dnr_reach_pct or '–'}{' %' if o.dnr_reach_pct else ''} | "
                   f"{s['n']:,} | {e['history']} / {e['live']} | {s['recent72']} | {s['hist_years']} | "
                   f"{s['full_pct']:.0f} % | {s['summary_pct']:.0f} % | {s['dated_pct']:.0f} % | {s['dup_pct']:.1f} % | "
                   f"{s['suspect_pct']:.0f} % | {s['median_chars']:,} | {status} |")
    return out


def findings(outlets, stats) -> list[str]:
    ss = list(stats.values())
    out = []
    fails = [s["outlet"].name for s in ss if not s["ok"]]
    out.append(f"- Outlets failing the pass criteria: {len(fails)}" + (f" ({', '.join(fails)})" if fails else ""))
    out.append(f"- Undated or unparseable `published`: {sum(s['undated'] for s in ss):,} articles; "
               f"dated in the future: {sum(s['future'] for s in ss)}")
    pend = sum(len(s["pending_years"]) for s in ss)
    out.append(f"- Backfill: {pend} of {len(ss) * HISTORY_YEARS} outlet-years not attempted yet (hatched in the figure)")
    conn = store.connect()
    copies = conn.execute("SELECT COUNT(*) FROM articles WHERE canonical_id IS NOT NULL").fetchone()[0]
    stripped = conn.execute("SELECT COUNT(*) FROM articles WHERE text_source LIKE '%+container'").fetchone()[0]
    multi = conn.execute("SELECT COUNT(*) FROM (SELECT article_id FROM headlines GROUP BY article_id HAVING COUNT(*) > 1)"
                         ).fetchone()[0]
    kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM articles WHERE kind IS NOT NULL GROUP BY 1").fetchall())
    walls = conn.execute("SELECT COUNT(*) FROM articles WHERE canonical_id IS NULL AND kind IS NULL "
                         "AND (text_source LIKE '%+wall' OR text_source LIKE '%+paywall')").fetchone()[0]
    out.append(f"- M3 (A34): {copies} re-headlined copies merged, {multi} articles carry a headline history; "
               f"{stripped} live-blog/news-list entries keep headline + summary only")
    out.append(f"- M3 (A38): not counted as articles — {kinds.get('headline_only', 0)} headline-only items, "
               f"{kinds.get('rundown', 0)} multi-topic rundowns, {kinds.get('page', 0)} site/hub pages; "
               f"{walls} consent/paywall walls keep headline + summary only")
    sus = sorted(ss, key=lambda s: -s["suspect_pct"])[:3]
    out.append("- Highest boilerplate-suspect share (cookie/consent/subscribe words in the first 300 chars): "
               + ", ".join(f"{s['outlet'].name} {s['suspect_pct']:.0f} %" for s in sus))
    return out


README_BEGIN, README_END = "<!-- m2:begin -->", "<!-- m2:end -->"


def update_readme(outlets, stats, today: dt.date, path: str = "README.md"):
    text = open(path).read()
    if README_BEGIN not in text:
        print(f"no {README_BEGIN} marker in {path}; skipped")
        return
    head, rest = text.split(README_BEGIN, 1)
    tail = rest.split(README_END, 1)[1]
    body = [f"*Snapshot {today.isoformat()}, regenerated by `uv run python -m pipeline.ingest.report --readme`.*", "",
            *country_table(outlets, stats)]
    with open(path, "w") as f:
        f.write(head + README_BEGIN + "\n" + "\n".join(body) + "\n" + README_END + tail)
    print(f"updated {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=0, help="print N random full-text snippets per reference outlet")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--readme", action="store_true", help="refresh the README.md section between the m2 markers")
    args = ap.parse_args()
    today = dt.datetime.now(dt.UTC).date()
    conn = store.connect()
    outlets, stats = collect(conn, today)

    figure(outlets, stats, today, f"{args.out}/m2_coverage.png")
    md = ["# M2 ingest: coverage report", "",
          f"Generated {today.isoformat()} by `uv run python -m pipeline.ingest.report`. Counts only, no article text (A10).",
          "", "![M2 coverage](m2_coverage.png)", "",
          "**Data.** *history* = archive.org backfill (1.3, ~4 articles per outlet per snapshot date, same dates for "
          f"every outlet, A23); *live* = RSS ingest (1.2) since {LIVE_SINCE}.", "",
          f"**Pass criteria per outlet** (health of the ingest that runs from now on, so text and dates are checked on "
          f"live-ingest articles): ≥ {MIN_RECENT_72H} articles published in the last 72 h · "
          f"full text or summary on ≥ {MIN_ANY_TEXT_PCT} % · parseable date on ≥ {MIN_DATED_PCT} %. "
          f"Full text means ≥ {MIN_TEXT_CHARS} extracted characters (A19).", "",
          "## Findings", "", *findings(outlets, stats), "",
          "## By country", "", *country_table(outlets, stats), "",
          "## By outlet", "", *outlet_table(outlets, stats), ""]
    with open(f"{args.out}/m2_ingest.md", "w") as f:
        f.write("\n".join(md))
    print("\n".join(country_table(outlets, stats)))
    print()
    print("\n".join(findings(outlets, stats)))
    print(f"\nwrote {args.out}/m2_coverage.png, {args.out}/m2_ingest.md")
    if args.readme:
        update_readme(outlets, stats, today)

    if args.samples:
        rng = random.Random(0)
        for o in outlets:
            if o.tier != "reference":
                continue
            rs = conn.execute("SELECT title, substr(full_text, 1, 160) t FROM articles WHERE outlet_id = ? "
                              "AND LENGTH(full_text) >= ? ORDER BY published DESC LIMIT 50", (o.id, MIN_TEXT_CHARS)).fetchall()
            for r in rng.sample(rs, min(args.samples, len(rs))):
                print(f"\n[{o.id}] {r['title']}\n   {r['t'].strip()}…".replace("\n\n", "\n"))


if __name__ == "__main__":
    main()
