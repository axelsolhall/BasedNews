"""Experiment E3 — semi-real replay: REAL outlets, REAL same-event pairs (national + international), REAL text
availability and token costs. Only the judge's verdicts are simulated, from "true" positions drawn N(0,1) per outlet
on both axes. One judge call returns a winner on BOTH axes (A31).

    uv run python -m pipeline.sim.eval_semireal [--budget 750000] [--seed 0]

Outputs: reports/e3_semireal.md · reports/e3_world.gif · reports/e3_country_<C>.gif ·
         frontend/src/assets/plane-frames.json (animation for the site)
Every visual is watermarked SIMULATED: real outlet names, invented positions.
"""
from __future__ import annotations

import argparse
import json
import warnings
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import norm, spearmanr

from pipeline import store
from pipeline.aggregate.plane import _contrast_stats
from pipeline.judge.render import render_article
from pipeline.plan.planner import Candidate, PlannerConfig, plan_joint
from pipeline.rate.model import BradleyTerryLaplace
from pipeline.sources.registry import load_outlets, reference_groups

PROMPT, A_FULL, A_SUMMARY = 300, 1.0, 0.6   # A30; full texts are never cut (A41), long articles just cost more
Z90 = norm.ppf(0.95)
WATERMARK = "SIMULATED — real outlets, random positions, not real bias"


def load_candidates(conn, outlets_ok):
    run = store.latest_event_run(conn)
    rows = conn.execute("""SELECT e.event_id, a.id, a.outlet_id, a.country, a.title, a.summary, a.full_text, a.published
                           FROM event_members e JOIN articles a ON a.id = e.article_id WHERE e.run_id = ?""", (run,)).fetchall()
    hist = store.headline_history(conn, [r["id"] for r in rows])
    try:                                                     # same text in two outlets = known tie, no call (A44)
        tie = {frozenset((t["a"], t["b"])) for t in conn.execute("SELECT a, b FROM same_text_pairs WHERE run_id = ?", (run,))}
    except Exception:
        tie = set()
    # token cost = what the judge would actually read (render.py, A36), incl. headline history
    tokens = lambda r, body: len(render_article(hist.get(r["id"], []), body)) / 4
    ev = defaultdict(dict)
    for r in rows:
        if r["outlet_id"] in outlets_ok and r["outlet_id"] not in ev[r["event_id"]]:
            ev[r["event_id"]][r["outlet_id"]] = r               # one article per outlet per event
    cands, n_cross = [], 0
    rel_rng = np.random.default_rng(12345)
    for eid, arts in ev.items():
        items = list(arts.values())
        # A32 (simulated topic mix): ~30 % of stories are economic (econ angle 0.9) else 0.2; social angle 0.75 typical
        rel = (0.9 if rel_rng.random() < 0.3 else 0.2, float(rel_rng.uniform(0.5, 0.95)))
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if frozenset((a["id"], b["id"])) in tie:
                    continue
                cross = a["country"] != b["country"]; n_cross += cross
                sa, sb = tokens(a, a["summary"] or "") + 10, tokens(b, b["summary"] or "") + 10
                cands.append(Candidate(eid, a["outlet_id"], b["outlet_id"], cost=PROMPT + sa + sb, discrimination=A_SUMMARY, relevance=rel))
                if a["full_text"] and b["full_text"]:
                    fa, fb = tokens(a, a["full_text"]), tokens(b, b["full_text"])
                    cands.append(Candidate(eid + ":full", a["outlet_id"], b["outlet_id"], cost=PROMPT + fa + fb, discrimination=A_FULL, relevance=rel))
    return run, cands, n_cross, len(ev)


def snapshot(posts, outlets, groups, truth, spent, calls):
    o_pts, c_pts = [], []
    tm = {ax: np.mean([truth[ax][o] for o in posts["econ"].outlets]) for ax in truth}
    for o in posts["econ"].outlets:
        x, sx = _contrast_stats(posts["econ"], {o: 1.0}); y, sy = _contrast_stats(posts["social"], {o: 1.0})
        o_pts.append({"id": o, "label": outlets[o].name, "country": outlets[o].country, "tier": outlets[o].tier,
                      "x": x, "y": y, "sd_x": sx, "sd_y": sy, "tx": truth["econ"][o] - tm["econ"], "ty": truth["social"][o] - tm["social"]})
    for c, w in groups.items():
        x, sx = _contrast_stats(posts["econ"], w); y, sy = _contrast_stats(posts["social"], w)
        c_pts.append({"id": c, "label": c, "x": x, "y": y, "sd_x": sx, "sd_y": sy,
                      "tx": np.mean([truth["econ"][o] for o in w]) - tm["econ"], "ty": np.mean([truth["social"][o] for o in w]) - tm["social"]})
    return {"tokens": int(spent), "calls": calls, "outlets": o_pts, "countries": c_pts}


def run(mode, cands, outlet_ids, outlets, groups, budget, seed, frame_every):
    rng = np.random.default_rng(seed)
    truth = {ax: {o: float(v) for o, v in zip(outlet_ids, np.random.default_rng(seed + i).standard_normal(len(outlet_ids)))}
             for i, ax in enumerate(("econ", "social"))}
    model = BradleyTerryLaplace(outlet_ids)
    posts = {ax: model.fit([], []) for ax in ("econ", "social")}
    data = {ax: ([], [], []) for ax in posts}
    used, spent, frames, metrics, rounds = set(), 0.0, [], [], 0
    look = {}
    for c in cands:
        look[(c.event_id, c.outlet_a, c.outlet_b)] = c; look[(c.event_id, c.outlet_b, c.outlet_a)] = c
    while True:
        pool = [c for c in cands if (c.event_id.split(":")[0], frozenset((c.outlet_a, c.outlet_b))) not in used]
        cfg = PlannerConfig(mode="random" if mode == "random" else "variance_per_cost", target_sd=0.0, batch_size=20)
        reqs = plan_joint(posts, pool, cfg, groups=groups, budget_left=budget - spent, rng=rng)
        if not reqs:
            break
        for r in reqs:
            c = look[(r.event_id, r.first_outlet, r.second_outlet)]
            used.add((r.event_id.split(":")[0], frozenset((r.first_outlet, r.second_outlet))))
            spent += c.cost
            for k, ax in enumerate(posts):                     # one call → a winner on each axis (A31)
                if rng.random() >= c.relevance[k]:
                    continue                                   # A32/A06: no angle on this axis → judge abstains
                d = truth[ax][r.first_outlet] - truth[ax][r.second_outlet] + rng.normal(0, 0.3 * np.sqrt(2))
                win = rng.random() < 1 / (1 + np.exp(-(c.discrimination * d + 0.3)))
                data[ax][0].append((r.first_outlet, r.second_outlet)); data[ax][1].append(bool(win)); data[ax][2].append(c.discrimination)
        posts = {ax: model.fit(*data[ax]) for ax in posts}
        rounds += 1
        n = len(used)                                          # calls bought (an axis may have abstained)
        sd = {ax: np.sqrt(np.diag(posts[ax].cov)[: len(outlet_ids)]) for ax in posts}
        rho = {ax: spearmanr(posts[ax].mean[: len(outlet_ids)], [truth[ax][o] for o in outlet_ids]).statistic for ax in posts}
        snap = snapshot(posts, outlets, groups, truth, spent, n)
        cov = np.mean([abs(p["x"] - p["tx"]) <= Z90 * p["sd_x"] for p in snap["outlets"]] + [abs(p["y"] - p["ty"]) <= Z90 * p["sd_y"] for p in snap["outlets"]])
        metrics.append({"tokens": spent, "calls": n, "spearman_econ": rho["econ"], "spearman_social": rho["social"],
                        "max_sd": float(max(sd["econ"].max(), sd["social"].max())),
                        "max_country_sd": float(max(max(p["sd_x"], p["sd_y"]) for p in snap["countries"])), "coverage90": float(cov)})
        if rounds % frame_every == 0:
            frames.append(snap)
    frames.append(snapshot(posts, outlets, groups, truth, spent, len(used)))
    return metrics, frames


def at(m, key, t):
    rows = [r for r in m if r["tokens"] <= t]
    return rows[-1][key] if rows else float("nan")


def gif(frames_a, frames_b, kind, path, title_filter=None):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation, PillowWriter
    from matplotlib.patches import Ellipse
    pick = (lambda f: [p for p in f[kind] if title_filter is None or p.get("country") == title_filter])
    allp = [p for f in frames_a + frames_b for p in pick(f)]
    R = max(2.0, max(max(abs(p["x"]) + p["sd_x"], abs(p["y"]) + p["sd_y"], abs(p["tx"]), abs(p["ty"])) for p in allp) * 1.05)
    fig, axs = plt.subplots(1, 2, figsize=(11, 5.6))
    cmap = plt.get_cmap("tab10")
    names = sorted({p["id"] for p in allp}); col = {n: cmap(i % 10) for i, n in enumerate(names)}
    L = min(len(frames_a), len(frames_b))

    def draw(i):
        for ax, frames, ttl in zip(axs, (frames_a, frames_b), ("random pairs", "active, uncertainty per token")):
            ax.clear(); f = frames[min(i, len(frames) - 1)]
            for p in pick(f):
                ax.add_patch(Ellipse((p["x"], p["y"]), 2 * p["sd_x"], 2 * p["sd_y"], color=col[p["id"]], alpha=0.18))
                ax.plot(p["x"], p["y"], "o", color=col[p["id"]], ms=4)
                ax.plot([p["x"], p["tx"]], [p["y"], p["ty"]], "--", color=col[p["id"]], lw=0.9, alpha=0.7)   # error line
                ax.plot(p["tx"], p["ty"], "x", color=col[p["id"]], ms=5, alpha=0.7)
                ax.annotate(p["label"], (p["x"], p["y"]), fontsize=7, xytext=(3, 3), textcoords="offset points")
            ax.set_xlim(-R, R); ax.set_ylim(-R, R); ax.set_aspect("equal"); ax.axhline(0, lw=.5, c="grey"); ax.axvline(0, lw=.5, c="grey")
            ax.set_title(f"{ttl}\n{f['tokens'] / 1e3:,.0f}k tokens · {f['calls']} calls", fontsize=10)
            ax.set_xlabel("← left   economic   right →"); ax.set_ylabel("← liberal   social   conservative →")
            ax.text(0, 0, "SIMULATED", fontsize=40, color="red", alpha=0.10, ha="center", va="center", rotation=30)
        fig.suptitle(WATERMARK + "   ·   dot = estimate, oval = ±1 sd, × = simulated truth, dashed = error", fontsize=9, color="#b91c1c")
    anim = FuncAnimation(fig, draw, frames=L, interval=350)
    anim.save(path, writer=PillowWriter(fps=3)); plt.close(fig)


def curves(multi, budget, path):
    """Mean ± sd over seeds of the headline metrics against tokens spent, random vs cost-aware."""
    import matplotlib.pyplot as plt
    grid = np.linspace(0, budget, 300)
    style = {"random": ("#9a9a9a", "random pairs"), "cost_aware": ("#1b7f79", "cost-aware (uncertainty per token)")}
    panels = (("max_country_sd", "largest country sd (lower is better)"), ("spearman_econ", "rank recovery, economic"),
              ("spearman_social", "rank recovery, social"))
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    for ax, (key, title) in zip(axes, panels):
        for m, mets in multi.items():
            Y = np.array([[at(met, key, t) for t in grid] for met in mets])
            with warnings.catch_warnings():                   # before the first batch every seed is NaN
                warnings.simplefilter("ignore", RuntimeWarning)
                mu, sd = np.nanmean(Y, 0), np.nanstd(Y, 0)
            c, lab = style[m]
            ax.plot(grid / 1e6, mu, color=c, lw=2, label=lab)
            ax.fill_between(grid / 1e6, mu - sd, mu + sd, color=c, alpha=0.18, lw=0)
            ends = [met[-1]["tokens"] for met in mets if met[-1]["tokens"] < budget - 5_000]
            if ends:
                ax.axvline(np.mean(ends) / 1e6, color=c, ls=":", lw=1.2)
        ax.set_title(title, fontsize=10, loc="left")
        ax.set_xlabel("tokens spent (millions)")
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(alpha=0.25)
    axes[0].legend(fontsize=8, frameon=False)
    axes[0].text(0.98, 0.97, "dotted: every comparable pair bought", transform=axes[0].transAxes, ha="right", va="top",
                 fontsize=7.5, color="#555")
    fig.suptitle(f"E3 · real stories & costs, SIMULATED verdicts · mean ± sd over {len(next(iter(multi.values())))} seeds",
                 x=0.01, ha="left", fontweight="bold", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=750_000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--frame-every", type=int, default=2)
    ap.add_argument("--country", default="United Kingdom")
    ap.add_argument("--seeds", type=int, default=5, help="metrics averaged over seeds; animation uses the first seed")
    ap.add_argument("--milestones", type=float, nargs="*", help="token budgets for the table (default 25/50/100 %% of budget)")
    ap.add_argument("--tag", default="", help="suffix for report/GIF/curve files, e.g. _7500k (keeps the default run's files)")
    ap.add_argument("--frames-out", default="frontend/src/assets/plane-frames.json")
    args = ap.parse_args()
    conn = store.connect()
    reg = load_outlets(); outlets = {o.id: o for o in reg}
    health = store.latest_health(conn)
    ok = {o.id for o in reg if o.id in health and health[o.id].feeds_ok > 0}
    run_id, cands, n_cross, n_ev = load_candidates(conn, ok)
    outlet_ids = sorted({c.outlet_a for c in cands} | {c.outlet_b for c in cands})
    groups = {c: {o: w for o, w in g.items() if o in outlet_ids} for c, g in reference_groups(reg).items()}
    groups = {c: g for c, g in groups.items() if g}
    pairs = {(c.event_id.split(":")[0], frozenset((c.outlet_a, c.outlet_b))) for c in cands}
    print(f"events {n_ev} · outlets {len(outlet_ids)} · comparable pairs {len(pairs)} ({n_cross} cross-country) · "
          f"candidates incl. full-text option {len(cands)}")
    res = {m: run(m, cands, outlet_ids, outlets, groups, args.budget, args.seed, args.frame_every) for m in ("random", "cost_aware")}
    multi = {m: [res[m][0]] + [run(m, cands, outlet_ids, outlets, groups, args.budget, args.seed + k, 10 ** 9)[0]
                               for k in range(1, args.seeds)] for m in res}

    L = ["# E3 — semi-real replay", "",
         f"Real data: event run `{run_id}` · {n_ev} events · {len(outlet_ids)} outlets in {len(groups)} countries · "
         f"{len(pairs):,} comparable pairs ({n_cross:,} cross-country) · real text availability and token costs.",
         "Simulated: true positions ~ N(0,1) per outlet per axis; judge verdicts (one call → both axes). Seed "
         f"{args.seed}, budget {args.budget:,.0f} tokens.", "",
         "| Strategy | tokens | calls | Spearman econ | Spearman social | max outlet sd | max country sd | 90 % coverage |",
         "|---|---|---|---|---|---|---|---|"]
    L[-2] = L[-2].replace("| Strategy |", "| Strategy (mean of %d seeds) |" % args.seeds)
    for m, mets in multi.items():
        for t in (args.milestones or [args.budget * f for f in (0.25, 0.5, 1.0)]):
            g = lambda k: float(np.mean([at(met, k, t) for met in mets]))
            sdv = lambda k: float(np.std([at(met, k, t) for met in mets]))
            L.append(f"| {m} | {t:,.0f} | {g('calls'):.0f} | {g('spearman_econ'):.3f} ± {sdv('spearman_econ'):.3f} | "
                     f"{g('spearman_social'):.3f} ± {sdv('spearman_social'):.3f} | {g('max_sd'):.3f} | "
                     f"{g('max_country_sd'):.3f} ± {sdv('max_country_sd'):.3f} | {g('coverage90'):.2f} |")
    for key, tgt in (("max_sd", 0.6), ("max_sd", 0.5), ("max_country_sd", 0.3)):
        reach = {m: next((r["tokens"] for r in res[m][0] if r[key] <= tgt), None) for m in res}
        cell = lambda v: f"{v:,.0f}" if v else f"> {args.budget:,.0f}"
        L.append(f"\nTokens until {key} ≤ {tgt}: random {cell(reach['random'])} · cost-aware {cell(reach['cost_aware'])}")
    # every comparable pair bought before the budget ran out → the data, not the money, is the limit
    done = {m: [met[-1]["tokens"] for met in mets if met and met[-1]["tokens"] < args.budget - 5_000] for m, mets in multi.items()}
    for m, ts in done.items():
        if ts:
            L.append(f"\n{m}: all {len(pairs):,} comparable pairs bought after {np.mean(ts):,.0f} tokens on average "
                     f"({len(ts)}/{len(multi[m])} seeds) — budget left unspent; more certainty now needs more stories, not money")
    rep = "\n".join(L)
    Path("reports").mkdir(exist_ok=True); Path(f"reports/e3_semireal{args.tag}.md").write_text(rep + "\n"); print(rep)
    curves(multi, args.budget, f"reports/e3_curves{args.tag}.png")

    fr = {"meta": {"source": "simulation-semireal", "watermark": WATERMARK, "event_run": run_id, "budget_tokens": args.budget},
          "random": res["random"][1], "cost_aware": res["cost_aware"][1]}
    Path(args.frames_out).write_text(json.dumps(fr, default=float))
    gif(res["random"][1], res["cost_aware"][1], "countries", f"reports/e3_world{args.tag}.gif")
    slug = args.country.lower().replace(" ", "_")
    gif(res["random"][1], res["cost_aware"][1], "outlets", f"reports/e3_country_{slug}{args.tag}.gif", title_filter=args.country)
    print(f"\nwrote reports/e3_semireal{args.tag}.md, reports/e3_curves{args.tag}.png, reports/e3_world{args.tag}.gif, "
          f"reports/e3_country_{slug}{args.tag}.gif, {args.frames_out}")


if __name__ == "__main__":
    main()
