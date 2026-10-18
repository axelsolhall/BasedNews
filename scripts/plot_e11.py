"""E11's figures (docs/experiments/e11_prereg.json), from the training runs' metrics and E9's test report.

    PYTHONPATH=. uv run python scripts/plot_e11.py [--runs data/distil/runs] [--e9 reports/e9.json]

reports/e11_training.png — agreement with gpt-5.4-mini on the validation days, per axis, as the full run trains
                           (step 0 = the untrained model), with validation loss.
reports/e11_learning_curve.png — agreement on E9's 943 test calls, per axis, against training examples: the untrained
                           model (C1), the first third, two thirds and all of the training days; gpt-5.4-mini's own
                           retest agreement as the band to reach.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

COLOR = {"econ": "#2a6fdb", "social": "#d9480f"}         # one hue per axis, the same in both figures
NAME = {"econ": "economic", "social": "social"}
INK, MUTED, GRID = "#1f2328", "#59636e", "#d8dee4"


def style(ax):
    ax.grid(axis="y", color=GRID, linewidth=0.8); ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED)


def metrics(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip() and '"val_loss"' in x]


def always_na(val: Path) -> dict:
    """Agreement a judge would get by always answering "na": the share of gpt-5.4-mini's validation verdicts that are."""
    ans = [json.loads(json.loads(x)["messages"][2]["content"]) for x in val.read_text().splitlines() if x.strip()]
    return {ax: sum(a[ax] == "na" for a in ans) / len(ans) for ax in ("econ", "social")}


def training(runs: Path, out: Path, val: Path):
    m = metrics(runs / "full" / "metrics.jsonl")
    x = [r["examples_seen"] for r in m]
    base = always_na(val) if val.exists() else {}
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax_ in ("econ", "social"):
        if ax_ in base:
            a1.axhline(base[ax_], color=COLOR[ax_], linestyle=":", linewidth=1.2)
            a1.text(x[0], base[ax_], f'always "na" ({NAME[ax_]})', color=MUTED, fontsize=8, va="bottom")
        y = [r[f"val_agree_{ax_}"] for r in m]
        a1.plot(x, y, color=COLOR[ax_], linewidth=2, marker="o", markersize=5, label=NAME[ax_])
        a1.annotate(f"{NAME[ax_]} {y[-1]:.0%}", (x[-1], y[-1]), xytext=(6, 0), textcoords="offset points",
                    color=INK, va="center", fontsize=9)
    a1.set_title("Agreement with gpt-5.4-mini, validation days", loc="left", color=INK, fontsize=11)
    a1.set_xlabel("training examples seen", color=MUTED); a1.set_ylabel("share of verdicts equal", color=MUTED)
    a1.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0)); a1.legend(frameon=False)
    a2.plot(x, [r["val_loss"] for r in m], color=INK, linewidth=2, marker="o", markersize=5)
    a2.set_title("Validation loss (answer tokens)", loc="left", color=INK, fontsize=11)
    a2.set_xlabel("training examples seen", color=MUTED); a2.set_yscale("log")
    for a in (a1, a2):
        style(a)
    fig.tight_layout(); fig.savefig(out / "e11_training.png", dpi=160); plt.close(fig)


def learning_curve(runs: Path, e9: dict, out: Path):
    c = e9["candidates"]
    points = [("C1", 0)] + [(k, json.loads((runs / d / "run.json").read_text())["train_examples"])
                            for k, d in (("C4-1/3", "one-third"), ("C4-2/3", "two-thirds"), ("C4", "full"))
                            if k in c and (runs / d / "run.json").exists()]
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    for ax_ in ("econ", "social"):
        lo, hi = e9["J2_ceiling"][ax_][1:]
        ax.axhspan(lo, hi, color=COLOR[ax_], alpha=0.10, linewidth=0)
        ax.text(points[-1][1], hi, f"gpt-5.4-mini vs itself ({NAME[ax_]})", color=MUTED, fontsize=8, ha="right", va="bottom")
        xs = [n for _, n in points]; ys = [c[k][f"J1_{ax_}"][0] for k, _ in points]
        err = [[c[k][f"J1_{ax_}"][0] - c[k][f"J1_{ax_}"][1] for k, _ in points],
               [c[k][f"J1_{ax_}"][2] - c[k][f"J1_{ax_}"][0] for k, _ in points]]
        ax.errorbar(xs, ys, yerr=err, color=COLOR[ax_], linewidth=2, marker="o", markersize=6, capsize=3, label=NAME[ax_])
    ax.set_title("Agreement with gpt-5.4-mini on E9's 943 test calls", loc="left", color=INK, fontsize=11)
    ax.set_xlabel("training examples (0 = the untrained Qwen3-14B)", color=MUTED)
    ax.set_ylabel("share of verdicts equal (95 % interval)", color=MUTED)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0)); ax.legend(frameon=False, loc="lower right")
    style(ax); fig.tight_layout(); fig.savefig(out / "e11_learning_curve.png", dpi=160); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, default=Path("data/distil/runs"))
    ap.add_argument("--e9", type=Path, default=Path("reports/e9.json"))
    ap.add_argument("--out", type=Path, default=Path("reports"))
    a = ap.parse_args()
    a.out.mkdir(exist_ok=True)
    if (a.runs / "full" / "metrics.jsonl").exists():
        training(a.runs, a.out, Path("data/distil/sft/val.jsonl")); print(f"→ {a.out / 'e11_training.png'}")
    e9 = json.loads(a.e9.read_text())
    if "C4" in e9["candidates"]:
        learning_curve(a.runs, e9, a.out); print(f"→ {a.out / 'e11_learning_curve.png'}")


if __name__ == "__main__":
    main()
