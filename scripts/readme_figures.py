"""The README's figures, from the project's own records. Re-run after new results.

    PYTHONPATH=. uv run python scripts/readme_figures.py            # → docs/img/*.png

Inputs: reports/e9.json (local judges), reports/e10.json (prompt v4 on fresh stories), data/figures/plane_preliminary.json
(`pipeline.export.run.build` over live's and E10's v4 verdicts), data/figures/history_years.json (articles per year in
the history database, cached by --history). Numbers that live only in experiment write-ups are written here with their
source. Colours: Okabe–Ito (colour-blind safe); the economic axis is always blue, the social axis vermillion.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Ellipse, FancyBboxPatch  # noqa: E402

OUT = Path("docs/img")
ECON, SOCIAL = "#0072B2", "#D55E00"
US, UK, OTHER = "#0072B2", "#D55E00", "#999999"
GREEN, PURPLE = "#009E73", "#CC79A7"
INK, MUTED, GRID, PAPER = "#1f2328", "#59636e", "#d8dee4", "#ffffff"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED, "figure.facecolor": PAPER, "axes.facecolor": PAPER})
K50 = math.sqrt(2 * math.log(2))                       # radius in sd of a 2-D 50 % credible region (independent axes)


def style(ax, grid="y"):
    if grid:
        ax.grid(axis=grid, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, dpi=170, bbox_inches="tight")
    plt.close(fig)
    print(f"→ {OUT / name}")


# ---- 1. the idea: the same event, two framings, one verdict -------------------------------------------------------
def concept():
    fig, ax = plt.subplots(figsize=(10, 3.4))
    ax.set_xlim(0, 10); ax.set_ylim(0, 3.4); ax.axis("off")

    def card(x, y, w, h, title, body, edge):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12", fc="#f6f8fa", ec=edge, lw=1.6))
        ax.text(x + 0.18, y + h - 0.32, title, color=MUTED, fontsize=8.5, va="top")
        ax.text(x + 0.18, y + h - 0.72, body, color=INK, fontsize=10.5, va="top", wrap=True, fontweight="bold")

    card(0.2, 1.85, 3.4, 1.3, "Outlet A  (name hidden)", "Government delivers\ntax relief for families", ECON)
    card(0.2, 0.25, 3.4, 1.3, "Outlet B  (name hidden)", "Tax cuts for the rich\nleave services unfunded", SOCIAL)
    ax.annotate("", xy=(4.75, 1.7), xytext=(3.75, 2.45), arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.4))
    ax.annotate("", xy=(4.75, 1.7), xytext=(3.75, 0.9), arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.4))
    ax.add_patch(FancyBboxPatch((4.8, 1.2), 1.7, 1.0, boxstyle="round,pad=0.02,rounding_size=0.12", fc=INK, ec=INK))
    ax.text(5.65, 1.85, "LLM judge", color="white", ha="center", va="center", fontsize=10.5, fontweight="bold")
    ax.text(5.65, 1.47, "same event?\nwhich leans further?", color="#c9d1d9", ha="center", va="center", fontsize=7.5)
    ax.annotate("", xy=(7.0, 1.7), xytext=(6.55, 1.7), arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.4))
    ax.text(7.1, 2.25, "economic:  A further right", color=ECON, fontsize=10.5, fontweight="bold", va="center")
    ax.text(7.1, 1.75, "social:      no angle", color=SOCIAL, fontsize=10.5, fontweight="bold", va="center")
    ax.text(7.1, 1.1, "× thousands of stories,\n228 outlets, 30 languages\n→ positions with credible regions",
            color=MUTED, fontsize=8.5, va="center")
    save(fig, "concept.png")


# ---- 2. the plane: preliminary real positions ---------------------------------------------------------------------
def plane():
    d = json.loads(Path("data/figures/plane_preliminary.json").read_text())
    f, meta = d["frame"], d["meta"]
    us_uk = [o for o in f["outlets"] if o["country"] in ("United States", "United Kingdom") and o["n"] >= 30]
    fig, ax = plt.subplots(figsize=(8.2, 6.6))
    for o in sorted(us_uk, key=lambda o: o["country"]):
        c = US if o["country"] == "United States" else UK
        ax.add_patch(Ellipse((o["x"], o["y"]), 2 * K50 * o["sd_x"], 2 * K50 * o["sd_y"], fc=c, ec=c, alpha=0.13, lw=1))
        ax.plot(o["x"], o["y"], "o", color=c, ms=6, mec="white", mew=1.2, zorder=3)
    ax.axhline(0, color=GRID, lw=1); ax.axvline(0, color=GRID, lw=1)
    lim = max(max(abs(o["x"]) + K50 * o["sd_x"], abs(o["y"]) + K50 * o["sd_y"]) for o in us_uk) * 1.08
    ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim); ax.set_aspect("equal")
    # labels in a column right of the regions, in the points' vertical order, with leader lines (no collisions)
    gap, col = 2 * lim / (len(us_uk) + 2), max(o["x"] + K50 * o["sd_x"] for o in us_uk) + 0.15 * lim
    ys = []
    for o in sorted(us_uk, key=lambda o: o["y"]):
        y = o["y"] if not ys else max(o["y"], ys[-1] + gap)
        ys.append(y)
    shift = max(0.0, ys[-1] - (lim - gap)) if ys else 0.0
    for o, y in zip(sorted(us_uk, key=lambda o: o["y"]), ys):
        y -= shift
        ax.plot([o["x"], col - 0.03 * lim], [o["y"], y], color=MUTED, lw=0.5, alpha=0.6, zorder=2)
        ax.text(col, y, o["label"], fontsize=8.5, color=INK, va="center", zorder=4)
    ax.set_xlabel("economic   ← left   ·   right →"); ax.set_ylabel("social   ← liberal   ·   conservative →")
    ax.plot([], [], "o", color=US, label="United States"); ax.plot([], [], "o", color=UK, label="United Kingdom")
    ax.legend(frameon=False, loc="upper left")
    ax.set_title(f"Preliminary: US and UK outlets, relative to the average outlet\n{meta['calls']:,} verdicts "
                 f"(gpt-5.4-mini, prompt v4), stories of 2026-10-09 → 16 · shaded: 50 % credible regions",
                 loc="left", fontsize=10, color=INK)
    style(ax, None)
    save(fig, "plane_preliminary.png")


# ---- 3. the judge's prompt, validated against an outside rating ---------------------------------------------------
def prompt_versions():
    e10 = json.loads(Path("reports/e10.json").read_text())
    pts = [("E4\nv3, first real run", 0.38), ("E4b\nv4, same pairs", 0.69), ("E6\nv4, new stories", 0.60),
           (f"E10\nv4, after the freeze", e10["rho"])]   # E4/E4b/E6: docs/experiments/e4*/e6_results.md
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    xs = range(len(pts))
    ax.bar(xs, [p[1] for p in pts], color=[OTHER, ECON, ECON, ECON], width=0.55)
    for x, (_, v) in zip(xs, pts):
        ax.text(x, v + 0.02, f"{v:.2f}", ha="center", color=INK, fontsize=10, fontweight="bold")
    ax.axhline(0.60, color=SOCIAL, ls="--", lw=1.3)
    ax.text(len(pts) - 0.6, 0.615, "pre-registered target 0.60", color=SOCIAL, fontsize=8.5, ha="right")
    ax.set_xticks(list(xs)); ax.set_xticklabels([p[0] for p in pts], fontsize=8.5)
    ax.set_ylim(0, 1); ax.set_ylabel("Spearman ρ with AllSides")
    ax.set_title("Does the judge order US/UK outlets like an independent rating? (social axis)", loc="left", fontsize=10, color=INK)
    style(ax)
    save(fig, "prompt_versions.png")


# ---- 4. the cost of eleven years ----------------------------------------------------------------------------------
def cost():
    rows = [("every pair,\ngpt-5.4-mini", 18_600, OTHER), ("planner-picked 5 %,\ngpt-5.4-mini", 930, ECON),
            ("the same 5 % on a\nlocal GPU (LoRA judge)", 4.58, GREEN)]   # README §History: 12 M pairs × $0.00155
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    ys = range(len(rows))[::-1]
    ax.barh(list(ys), [r[1] for r in rows], color=[r[2] for r in rows], height=0.55)
    ax.set_xscale("log"); ax.set_xlim(1, 60_000)
    ax.set_xticks([1, 10, 100, 1_000, 10_000]); ax.set_xticklabels(["$1", "$10", "$100", "$1k", "$10k"])
    for y, (_, v, _) in zip(ys, rows):
        ax.text(v * 1.15, y, f"${v:,.0f}" if v >= 10 else f"${v:.2f}", va="center", color=INK, fontsize=11, fontweight="bold")
    ax.set_yticks(list(ys)); ax.set_yticklabels([r[0] for r in rows], fontsize=9)
    ax.set_xlabel("one-time cost of judging the 2015 → 2026 history (log scale)")
    ax.set_title("Preliminary: the cost plan for the 2015 → 2026 history (the local judge is still being tested)",
                 loc="left", fontsize=10, color=INK)
    style(ax, "x")
    save(fig, "cost.png")


# ---- 5. local judges against the paid one ---------------------------------------------------------------------------
def local_judges():
    e9 = json.loads(Path("reports/e9.json").read_text())
    names = {"C1": "Qwen3-14B", "C2": "Qwen3-14B\n+ thinking", "C3": "Qwen3-8B", "C4": "Qwen3-14B\n+ LoRA (ours)"}
    cands = [c for c in ("C3", "C1", "C2", "C4") if c in e9["candidates"]]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    for ax, axis, col in zip(axes, ("econ", "social"), (ECON, SOCIAL)):
        lo, hi = e9["J2_ceiling"][axis][1:]
        ax.axhspan(lo, hi, color=col, alpha=0.12, lw=0)
        ax.text(len(cands) - 0.55, hi + 0.005, "gpt-5.4-mini vs itself", color=MUTED, fontsize=8, ha="right", va="bottom")
        for x, c in enumerate(cands):
            m, a, b = e9["candidates"][c][f"J1_{axis}"]
            k = e9["candidates"][c].get(f"kappa_{axis}")
            ax.bar(x, m, color=col if c == "C4" else OTHER, width=0.6)
            ax.errorbar(x, m, yerr=[[m - a], [b - m]], color=INK, capsize=3, lw=1)
            ax.text(x, b + 0.012, f"{m:.2f}" + (f"\nκ {k:.2f}" if k is not None else ""), ha="center", fontsize=8.5, color=INK)
        ax.set_xticks(range(len(cands))); ax.set_xticklabels([names[c] for c in cands], fontsize=8.5)
        ax.set_ylim(0.4, 1.02); ax.set_title({"econ": "economic axis", "social": "social axis"}[axis], color=col, fontsize=10, loc="left")
        style(ax)
    axes[0].set_ylabel("agreement with gpt-5.4-mini\n(943 calls, 95 % interval)")
    fig.suptitle("Preliminary: local judges on the paid judge's 943 calls (E9; the LoRA learning curve is still running)",
                 x=0.06, ha="left", fontsize=10.5, color=INK)
    save(fig, "local_judges.png")


# ---- 6. the per-pair angle model --------------------------------------------------------------------------------------
def angle():
    # FINDINGS F3, evidence 2: March 2019 simulation at a fixed budget (5,985 calls each)
    rows = [("economic calls answered", 0.10, 0.35), ("economic order vs truth (ρ)", 0.57, 0.80)]
    fig, ax = plt.subplots(figsize=(7.5, 3.0))
    for y, (name, a, b) in enumerate(rows[::-1]):
        ax.plot([a, b], [y, y], color=GRID, lw=6, solid_capstyle="round", zorder=1)
        ax.plot(a, y, "o", color=OTHER, ms=11, zorder=2); ax.plot(b, y, "o", color=ECON, ms=11, zorder=2)
        ax.text(a, y + 0.22, f"{a:.2f}" if "ρ" in name else f"{a:.0%}", ha="center", color=MUTED, fontsize=9)
        ax.text(b, y + 0.22, f"{b:.2f}" if "ρ" in name else f"{b:.0%}", ha="center", color=ECON, fontsize=9, fontweight="bold")
    ax.set_yticks([0, 1]); ax.set_yticklabels([r[0] for r in rows[::-1]]); ax.set_ylim(-0.6, 1.6); ax.set_xlim(0, 1)
    ax.plot([], [], "o", color=OTHER, label="one average per axis"); ax.plot([], [], "o", color=ECON, label="each pair's predicted angle")
    ax.legend(frameon=False, loc="lower right", fontsize=8.5)
    ax.set_title("Same number of calls, more economic answers (simulation, 37 days of real stories)", loc="left", fontsize=10, color=INK)
    style(ax, "x")
    save(fig, "angle.png")


# ---- 7. the history ----------------------------------------------------------------------------------------------------
def history(refresh: bool):
    cache = Path("data/figures/history_years.json")
    if refresh or not cache.exists():
        import sqlite3
        conn = sqlite3.connect("file:data/history/history.sqlite?mode=ro", uri=True)
        rows = conn.execute("SELECT substr(published, 1, 4), count(*), count(DISTINCT outlet_id) FROM articles GROUP BY 1").fetchall()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({y: [n, o] for y, n, o in rows}))
    years = json.loads(cache.read_text())
    ys = sorted(years)
    fig, ax = plt.subplots(figsize=(8.5, 3.2))
    ax.bar(ys, [years[y][0] / 1000 for y in ys], color=ECON, width=0.65)
    for y in ys:
        ax.text(y, years[y][0] / 1000 + 12, f"{years[y][1]}", ha="center", fontsize=7.5, color=MUTED)
    ax.set_ylabel("articles (thousands)")
    total = sum(v[0] for v in years.values())
    ax.set_title(f"The history: {total / 1e6:.1f} M articles from GDELT + the publishers' own pages "
                 "(numbers: outlets present that year)", loc="left", fontsize=10, color=INK)
    plt.xticks(fontsize=8.5)
    style(ax)
    save(fig, "history.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", action="store_true", help="recount the history database (slow)")
    a = ap.parse_args()
    for f in (concept, plane, prompt_versions, cost, local_judges, angle):
        f()
    history(a.history)


if __name__ == "__main__":
    main()
