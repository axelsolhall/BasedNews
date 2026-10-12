"""Pipeline review: one self-contained HTML report with aggregate results AND concrete examples for every stage.

    uv run --group ml python -m pipeline.inspect          → reports/pipeline_review.html  (open in a browser)

Internal tool: shows article snippets for inspection only — never publish this file (A10).
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
import random
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

from pipeline import store
from pipeline.backfill.run import JOBS_SCHEMA
from pipeline.sources.registry import load_outlets

OUT = Path("reports/pipeline_review.html")
E = html.escape


def table(headers, rows, cls=""):
    h = "".join(f"<th>{E(str(x))}</th>" for x in headers)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'


def badge(ok, yes="OK", no="CHECK"):
    return f'<span class="b {"ok" if ok else "bad"}">{yes if ok else no}</span>'


def img(path):
    p = Path(path)
    if not p.exists():
        return f"<p class='muted'>missing: {E(str(p))}</p>"
    return f'<img src="data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}">'


def md_table(path):
    p = Path(path)
    if not p.exists():
        return f"<p class='muted'>missing: {E(str(p))} — run the experiment first</p>"
    out, rows, in_tab = [], [], False
    for line in p.read_text().splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            rows.append(cells); in_tab = True
        else:
            if in_tab:
                out.append(table(rows[0], rows[1:])); rows, in_tab = [], False
            if line.startswith("#"):
                out.append(f"<h4>{E(line.lstrip('# '))}</h4>")
            elif line.strip():
                out.append(f"<p class='muted'>{E(line)}</p>")
    if rows:
        out.append(table(rows[0], rows[1:]))
    return "".join(out)


def section(sid, title, verdict, body):
    return f'<section id="{sid}"><h2>{E(title)}</h2><div class="verdict">{verdict}</div>{body}</section>'


def main():
    conn = store.connect()
    conn.execute(JOBS_SCHEMA)
    outlets = load_outlets()
    byid = {o.id: o for o in outlets}
    rng = random.Random(1)
    parts, toc = [], []

    # ---- M1 sources ----------------------------------------------------------------
    health = store.latest_health(conn)
    rows, ok_n = [], 0
    for o in sorted(outlets, key=lambda o: (o.country, o.tier != "reference", -(o.dnr_reach_pct or 0))):
        h = health.get(o.id)
        ok = bool(h and h.feeds_ok > 0 and h.newest_item_age_h is not None and h.newest_item_age_h < 48)
        ok_n += ok
        yn = lambda v: "?" if v is None else ("yes" if v else "no")
        rows.append([E(o.country), f"<b>{E(o.name)}</b>" if o.tier == "reference" else E(o.name), o.tier,
                     f"{o.dnr_reach_pct}%" if o.dnr_reach_pct else "—",
                     f"{h.feeds_ok}/{h.feeds_total}" if h else "—",
                     f"{h.newest_item_age_h:.1f}" if h and h.newest_item_age_h is not None else "—",
                     yn(h.robots_allows_articles) if h else "—", yn(h.tdm_reserved) if h else "—",
                     ("full" if h and h.full_text_allowed else "headline+summary"), badge(ok)])
    parts.append(section("m1", "M1 · Sources — outlet registry & feed health",
        f"{ok_n}/{len(outlets)} outlets usable · {sum(o.tier == 'reference' for o in outlets)} reference (DNR 2026 top-5, A29) · "
        f"{sum(1 for h in health.values() if not h.full_text_allowed)} limited to headline+summary by robots/TDM (A10)",
        table(["country", "outlet", "tier", "DNR reach", "feeds", "newest (h)", "robots", "TDM reserved", "text policy", ""], rows)))

    # ---- M2 ingest -----------------------------------------------------------------
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
    agg = {r["outlet_id"]: r for r in conn.execute("""SELECT outlet_id, COUNT(*) n, SUM(published >= ?) recent,
        AVG(full_text IS NOT NULL) text, AVG(LENGTH(full_text)) chars FROM articles
        WHERE text_source NOT LIKE 'wayback-%' GROUP BY outlet_id""", (since,))}
    total = sum(r["n"] for r in agg.values())
    rows = [[E(byid[k].country) if k in byid else "?", E(k), r["n"], r["recent"] or 0, f"{100 * (r['text'] or 0):.0f}%",
             f"{(r['chars'] or 0):.0f}", badge((r["recent"] or 0) >= 5)] for k, r in sorted(agg.items())]
    ex = conn.execute("""SELECT outlet_id, published, title, url, text_source, substr(coalesce(full_text, summary, ''),1,280) t
        FROM articles WHERE text_source NOT LIKE 'wayback-%' AND published >= ? ORDER BY RANDOM() LIMIT 6""", (since,)).fetchall()
    exh = "".join(f"<div class='ex'><b>[{E(a['outlet_id'])}]</b> {E(a['title'])} <span class='muted'>({E(a['published'] or '')}, "
                  f"{E(a['text_source'])})</span><br><span class='snip'>{E(a['t'])}…</span> <a href='{E(a['url'])}'>source</a></div>" for a in ex)
    parts.append(section("m2", "M2 · Ingest — live articles",
        f"{total:,} live articles from {len(agg)} outlets · dedupe before download · text kept internally only (A10)",
        table(["country", "outlet", "articles", "last 72 h", "full text", "avg chars", ""], rows) + "<h3>Random recent examples</h3>" + exh))

    # ---- M2b backfill ---------------------------------------------------------------
    grid = defaultdict(lambda: defaultdict(int))
    for r in conn.execute("SELECT outlet_id, substr(published,1,4) y, COUNT(*) n FROM articles WHERE text_source LIKE 'wayback-%' GROUP BY 1,2"):
        grid[r["outlet_id"]][r["y"]] = r["n"]
    years = sorted({y for g in grid.values() for y in g})
    rows = [[E(o.id)] + [(f"<span class='heat' style='opacity:{0.25 + 0.75 * min(1, grid[o.id][y] / 4):.2f}'>{grid[o.id][y]}</span>"
                          if grid[o.id][y] else "·") for y in years] for o in outlets]
    jobs = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM backfill_jobs GROUP BY status")}
    hist_ex = conn.execute("SELECT outlet_id, published, title, substr(coalesce(full_text,''),1,220) t FROM articles "
                           "WHERE text_source LIKE 'wayback-%' AND full_text IS NOT NULL ORDER BY RANDOM() LIMIT 5").fetchall()
    exh = "".join(f"<div class='ex'><b>[{E(a['outlet_id'])} · {E(a['published'][:10])}]</b> {E(a['title'])}<br>"
                  f"<span class='snip'>{E(a['t'])}…</span></div>" for a in hist_ex)
    parts.append(section("m2b", "M2b · Backfill — 10-year sparse history (Wayback Machine, A23–A24)",
        f"{sum(sum(g.values()) for g in grid.values()):,} historical articles · jobs: {E(json.dumps(jobs))}",
        table(["outlet"] + years, rows, "grid") + "<h3>Random historical examples</h3>" + exh))

    # ---- M4 / M5 --------------------------------------------------------------------
    try:
        from pipeline.embed.run import MODEL
        ids, V = store.load_embeddings(conn, MODEL)
        m4 = f"{len(ids):,} vectors ({E(MODEL)})"
        ex = ""
        if len(ids) > 50:
            meta = {r["id"]: r for r in conn.execute("SELECT id, country, outlet_id, title FROM articles WHERE id IN (%s)"
                                                     % ",".join("?" * len(ids)), ids)}
            S = V @ V.T
            off = S[np.triu_indices(len(ids), 1)]
            m4 += f" · pairwise cosine median {np.median(off):.2f}, p99 {np.percentile(off, 99):.2f}"
            for i in rng.sample(range(len(ids)), 4):
                a = meta[ids[i]]
                nb = [j for j in np.argsort(-S[i]) if meta[ids[j]]["country"] != a["country"]][:3]
                ex += (f"<div class='ex'><b>[{E(a['country'])}/{E(a['outlet_id'])}]</b> {E(a['title'])}<ul>"
                       + "".join(f"<li>{S[i, j]:.2f} [{E(meta[ids[j]]['country'])}/{E(meta[ids[j]]['outlet_id'])}] {E(meta[ids[j]]['title'])}</li>" for j in nb)
                       + "</ul></div>")
        parts.append(section("m4", "M4 · Embeddings — multilingual vectors (A12)", m4,
                             "<h3>Nearest neighbours from OTHER countries (cross-lingual check)</h3>" + ex))
    except Exception as e:
        parts.append(section("m4", "M4 · Embeddings", f"not available: {E(str(e))}", ""))

    run = store.latest_event_run(conn)
    if run:
        mem = defaultdict(list)
        for r in conn.execute("""SELECT e.event_id, a.outlet_id, a.country, a.title FROM event_members e
                                 JOIN articles a ON a.id = e.article_id WHERE e.run_id = ?""", (run,)):
            mem[r["event_id"]].append(r)
        multi = {k: v for k, v in mem.items() if len({x["outlet_id"] for x in v}) >= 2}
        cross = sorted(((k, v) for k, v in multi.items() if len({x["country"] for x in v}) >= 3),
                       key=lambda kv: -len({x["country"] for x in kv[1]}))
        sizes = sorted((len(v) for v in mem.values()), reverse=True)
        ex = "".join(f"<div class='ex'><b>{len({x['country'] for x in v})} countries, {len({x['outlet_id'] for x in v})} outlets</b><ul>"
                     + "".join(f"<li>[{E(x['country'])}/{E(x['outlet_id'])}] {E(x['title'])}</li>" for x in v[:8]) + "</ul></div>"
                     for _, v in cross[:4])
        labels = Path("data/eval/same_event_pairs.jsonl")
        nlab = len(labels.read_text().splitlines()) if labels.exists() else 0
        parts.append(section("m5", "M5 · Events — same-story clusters (A02, A20)",
            f"run {E(run)} · {len(mem):,} clusters · {len(multi)} with ≥2 outlets · largest {sizes[:5]} (chaining check) · "
            f"labelled pairs for precision/recall: {nlab} {badge(nlab >= 100, 'labelled', 'needs labels')}",
            "<h3>Most international events (examples)</h3>" + ex))

    # ---- M6/M8 experiments, M9 preview ----------------------------------------------
    parts.append(section("e1", "M6 + M8 · E1 — active selection vs random (simulation)",
        "Bayesian Bradley–Terry + variance-targeted planner; equal number of LLM calls",
        md_table("reports/p1_rating_core.md") + img("reports/p1_active_vs_random.png")))
    sens = "".join(f"<h3>Summary sharpness a = {a}</h3>" + md_table(f"reports/e2_info_per_token_a{a}.md") for a in ("0.4", "0.8"))
    parts.append(section("e2", "M6 + M8 · E2 — information per token (simulation)",
        "Elo baseline vs Bayesian BT; random vs cost-blind vs cost-aware selection; equal TOKEN budget",
        md_table("reports/e2_info_per_token_a0.6.md") + img("reports/e2_info_per_token_a0.6.png")
        + "<h3>Sensitivity (does the planner adapt?)</h3>" + sens))
    gif = Path("reports/e3_world.gif")
    gif_html = (f'<img src="data:image/gif;base64,{base64.b64encode(gif.read_bytes()).decode()}">' if gif.exists() else "")
    parts.append(section("e3", "M6 + M8 · E3 — semi-real replay (real events & costs, simulated verdicts)",
        "Joint two-axis planner on real same-event pairs from 10 countries; animated plane random vs active",
        md_table("reports/e3_semireal.md") + gif_html))
    parts.append(section("m9", "M9 · Plane — dots + ovals (simulation preview, A25–A26)",
        "Oval calibration: truth inside ±1 sd oval vs 39 % expected", img("reports/plane_world.png") + img("reports/plane_country.png")))

    # ---- assumptions ------------------------------------------------------------------
    A = Path("ASSUMPTIONS.md").read_text()
    items = re.findall(r"### (A\d+) — ([^\n]+)\n(.*?)(?=\n### |\Z)", A, re.S)
    rows = [[E(i), E(t), E((re.search(r"\*\*Status:\*\* ([^\n]+)", body) or [None, "?"])[1])] for i, t, body in items]
    parts.append(section("assumptions", "Assumptions register", f"{len(items)} assumptions", table(["id", "assumption", "status"], rows)))

    for sid, title in re.findall(r'<section id="([^"]+)"><h2>([^<]+)</h2>', "".join(parts)):
        toc.append(f'<a href="#{sid}">{title.split(" — ")[0]}</a>')
    css = """body{font:14px/1.45 -apple-system,Segoe UI,sans-serif;margin:0;color:#111;background:#f6f7f9}
    header{background:#111827;color:#fff;padding:18px 28px}nav{position:sticky;top:0;background:#fff;padding:8px 28px;border-bottom:1px solid #e5e7eb;z-index:2}
    nav a{margin-right:14px;color:#2563eb;text-decoration:none;font-size:13px}main{padding:8px 28px;max-width:1200px}
    section{background:#fff;border-radius:10px;padding:14px 18px;margin:14px 0;box-shadow:0 1px 2px #0001}
    .verdict{color:#374151;margin:-4px 0 10px}table{border-collapse:collapse;font-size:12px;margin:6px 0}
    th,td{border-bottom:1px solid #eee;padding:3px 8px;text-align:left}th{background:#f3f4f6}
    .b{padding:1px 6px;border-radius:6px;font-size:11px}.ok{background:#dcfce7;color:#166534}.bad{background:#fee2e2;color:#991b1b}
    .ex{border-left:3px solid #c7d2fe;padding:4px 10px;margin:8px 0}.snip{color:#4b5563}.muted{color:#6b7280}
    img{max-width:100%;margin:8px 0}.grid td{text-align:center;padding:2px 4px}.heat{background:#2563eb;color:#fff;border-radius:3px;padding:0 4px}"""
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>BasedNews · pipeline review</title><style>{css}</style></head>"
                   f"<body><header><h1>BasedNews · pipeline review</h1><div>generated {dt.datetime.now():%Y-%m-%d %H:%M} · "
                   f"internal — contains article snippets, do not publish (A10)</div></header><nav>{''.join(toc)}</nav><main>{''.join(parts)}</main></body></html>")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
