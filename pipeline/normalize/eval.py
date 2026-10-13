"""M3 eval: are shared-text groups split the right way, and what headline history do articles carry?

    uv run python -m pipeline.normalize.eval [--samples 3]

Checks with labels we get for free (no hand-labelling):
  live-blog URLs (pinnedEntry, /direkt/, /live, /seneste/…) must come out as containers           target 100 %
  copies with an identical feed summary must come out as one renamed article                      target 100 %
Writes reports/m3_normalize.png: the similarity distributions the thresholds in rules.py cut through.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from pipeline import store  # noqa: E402
from pipeline.normalize.rules import NormalizeConfig  # noqa: E402

LIVE_URL_MARKERS = ("pinnedEntry", "/direkt", "-direkt", "/live", "liveblog", "/seneste/", "siste-nytt")


def load_groups(conn) -> list[dict]:
    out = []
    for r in conn.execute("SELECT run_id, outlet_id, kind, n_members, n_titles, payload FROM normalize_groups ORDER BY run_id"):
        g = json.loads(r["payload"])
        g.update(run_id=r["run_id"], outlet_id=r["outlet_id"], kind=r["kind"], n_titles=r["n_titles"])
        out.append(g)
    return out


def is_live_url(g) -> bool:
    return any(m in x["url"] for x in g["members"] for m in LIVE_URL_MARKERS)


def has_duplicate_summary(g) -> bool:
    S = np.array(g["summary_sim"])
    idx = [k for k, x in enumerate(g["members"]) if x["has_summary"]]
    return any(S[i, j] > 0.999 for a, i in enumerate(idx) for j in idx[a + 1:])


def figure(groups, cfg: NormalizeConfig, path: str):
    owners, others, ren_pairs, con_pairs = [], [], [], []
    for g in groups:
        S = np.array(g["summary_sim"])
        idx = [k for k, x in enumerate(g["members"]) if x["has_summary"]]
        pairs = [S[i, j] for a, i in enumerate(idx) for j in idx[a + 1:]]
        (ren_pairs if g["kind"] == "renamed" else con_pairs).extend(pairs)
        if g["kind"] == "container":
            for x in g["members"]:
                (owners if x["canonical"] in g["keeps_text"] else others).append(x["text_sim"])
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.2))
    bins = np.linspace(-0.2, 1.0, 37)
    a1.hist(others, bins=bins, color="#cfcfcf", label=f"entries that lose the text (n={len(others)})")
    a1.hist(owners, bins=bins, color="#1b7f79", label=f"entry that keeps it (n={len(owners)})")
    a1.axvline(cfg.owns_text, color="#c0392b", ls="--", lw=1)
    a1.text(cfg.owns_text + 0.01, a1.get_ylim()[1] * 0.92, f"owns_text {cfg.owns_text}\n+ margin {cfg.margin}",
            fontsize=8, color="#c0392b", va="top")
    a1.set_title("Containers: headline ↔ page-text similarity", fontsize=10, loc="left")
    a1.set_xlabel("cosine similarity (the embedding model of each normalize run)")
    a1.legend(fontsize=8, frameon=False)
    a2.hist(con_pairs, bins=bins, color="#e0a43a", label=f"pairs in containers (n={len(con_pairs)})")
    a2.hist(ren_pairs, bins=bins, color="#1b7f79", label=f"pairs in renamed articles (n={len(ren_pairs)})")
    a2.axvline(cfg.same_summary, color="#c0392b", ls="--", lw=1)
    a2.text(cfg.same_summary + 0.01, a2.get_ylim()[1] * 0.92, f"same_summary {cfg.same_summary}", fontsize=8,
            color="#c0392b", va="top")
    a2.set_title("Feed-summary similarity between copies", fontsize=10, loc="left")
    a2.set_xlabel("cosine similarity")
    a2.legend(fontsize=8, frameon=False)
    for a in (a1, a2):
        a.spines[["top", "right"]].set_visible(False)
    fig.suptitle("BasedNews · M3 normalize: one text, several headlines (A34)", x=0.01, ha="left", fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=0, help="print N random decisions per kind")
    args = ap.parse_args()
    conn = store.connect()
    groups = load_groups(conn)
    if not groups:
        print("no decisions logged yet: run `uv run --group ml python -m pipeline.normalize.run`"); return
    from pipeline.embed.profiles import profile
    p = profile()
    cfg = NormalizeConfig(same_summary=p["same_summary"], same_title=p["same_title"], owns_text=p["owns_text"],
                          margin=p["owns_margin"])

    kinds = Counter(("renamed" if g["kind"] == "renamed" else
                     "container, one entry keeps the text" if g["keeps_text"] else "container, nobody keeps it")
                    for g in groups)
    print(f"{len(groups)} shared-text groups: {dict(kinds)}")
    live = [g for g in groups if is_live_url(g)]
    dup = [g for g in groups if has_duplicate_summary(g)]
    ok_live = sum(g["kind"] == "container" for g in live)
    ok_dup = sum(g["kind"] == "renamed" for g in dup)
    print(f"live-blog URL groups classified as container: {ok_live}/{len(live)}"
          f" ({100 * ok_live / max(len(live), 1):.0f} %, target 100 %)")
    print(f"identical-summary groups classified as renamed: {ok_dup}/{len(dup)}"
          f" ({100 * ok_dup / max(len(dup), 1):.0f} %, target 100 %)")

    hist = store.headline_history(conn)
    n_versions = Counter(len(v) for v in hist.values())
    hows = Counter(h.how for v in hist.values() if len(v) > 1 for h in v)
    multi = sum(c for k, c in n_versions.items() if k > 1)
    print(f"canonical articles with headline history: {multi} of {len(hist):,} "
          f"(versions per article: {dict(sorted(n_versions.items()))}; sources: {dict(hows)})")

    bp = conn.execute("SELECT outlet_id, field, paragraph, n_articles, edge_share, title_sim FROM boilerplate "
                      "ORDER BY n_articles DESC").fetchall()
    if bp:
        before = conn.execute("SELECT COALESCE(SUM(LENGTH(original)), 0) FROM articles_text_backup").fetchone()[0]
        after = conn.execute("""SELECT COALESCE(SUM(LENGTH(CASE b.field WHEN 'full_text' THEN a.full_text ELSE a.summary END)), 0)
                                FROM articles_text_backup b JOIN articles a ON a.id = b.article_id""").fetchone()[0]
        edited = conn.execute("SELECT COUNT(DISTINCT article_id) FROM articles_text_backup").fetchone()[0]
        print(f"boilerplate (A40): {len(bp)} paragraphs in {len({r['outlet_id'] for r in bp})} outlets; "
              f"{edited} articles edited, {before - after:,} characters removed. Most repeated:")
        for r in bp[:12]:
            where = "anywhere" if (r["edge_share"] or 0) <= 0.5 else f"edge {r['edge_share']:.0%}"
            print(f"   {r['n_articles']:>4}× {r['outlet_id']:<12} {r['field']:<9} {where:<9} {r['paragraph'][:80]}")
    figure(groups, cfg, "reports/m3_normalize.png")
    print("wrote reports/m3_normalize.png")

    if args.samples:
        rng = random.Random(0)
        for kind in ("renamed", "container"):
            pool = [g for g in groups if g["kind"] == kind]
            for g in rng.sample(pool, min(args.samples, len(pool))):
                print(f"\n[{kind}] {g['outlet_id']}  best {g['best_text_sim']:.2f} / runner-up {g['runner_up_text_sim']:.2f}")
                for x in sorted(g["members"], key=lambda x: -x["text_sim"]):
                    tag = "keeps text" if x["canonical"] in g["keeps_text"] else "loses text"
                    if x["canonical"] != x["id"]:
                        tag = "merged as headline version"
                    print(f"   {x['text_sim']:5.2f}  {tag:<26} {x['title'][:70]}")


if __name__ == "__main__":
    main()
