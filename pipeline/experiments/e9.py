"""E9: a local judge for the history vs gpt-5.4-mini (docs/experiments/e9_prereg.json). Free, on the gaming PC.

    uv run python -m pipeline.experiments.e9 run --candidate C1     # the server must be running that candidate's model
    uv run python -m pipeline.experiments.e9 analyse                # → reports/e9.json, reports/e9.md

The reference is every gpt-5.4-mini call with prompt v4 (live 'measure' + 'eval'); each is asked again, same prompt
version, same A/B order and direction, same text kind, through OpenAIJudge pointed at the local server. The prompts
are rebuilt from the stored articles (the original requests predate the call log, A51), so an article that gained a
headline version since is shown with it. Verdicts are stored with purpose 'e9h<harness>-<candidate>' only.

Harness 2 (2026-10-17), fixed before any agreement was computed: harness 1 capped thinking at 6,000 tokens, which
long prompts pushed past the server's 8,192 (27 calls refused), and its JSON grammar allowed endless whitespace (27
thinking answers looped to the cap and parsed as na/na). Harness 2 lets the server use the remaining context, the
server disallows free whitespace in structured output (infra/pc/llm.env), and an answer cut off by the token limit is a
failed call, never a verdict. Harness-1 rows stay in the database under 'e9-<candidate>' and are not analysed.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from pipeline import store
from pipeline.contracts import ComparisonRequest
from pipeline.judge import prompt as P
from pipeline.judge.client import JudgeCallFailed
from pipeline.judge.client import LocalJudge as _LocalJudge
from pipeline.judge.client import SwapJudge, ThresholdJudge
from pipeline.judge.client import local_client
from pipeline.measure.run import A_FULL, A_SUMMARY, record, record_failure, rendered
from pipeline.rate.hierarchical import HierarchicalBT

PREREG = Path("docs/experiments/e9_prereg.json")
CANDIDATES = {                                     # as pre-registered; `model` is what the server must be running
    "C1": {"model": "Qwen/Qwen3-14B-AWQ", "thinking": False, "sampling": {"temperature": 0.0}},
    "C2": {"model": "Qwen/Qwen3-14B-AWQ", "thinking": True, "sampling": {"temperature": 0.6, "top_p": 0.95}},
    "C3": {"model": "Qwen/Qwen3-8B-AWQ", "thinking": False, "sampling": {"temperature": 0.0}},
    # E11 (docs/experiments/e11_prereg.json): the 14B with a LoRA adapter trained on gpt-5.4-mini's v4 verdicts, served
    # as a vLLM LoRA module named `served`; the learning curve's adapters (first third, two thirds of the training days)
    "C4": {"model": "Qwen/Qwen3-14B-AWQ", "served": "c4", "thinking": False, "sampling": {"temperature": 0.0}},
    "C4-2/3": {"model": "Qwen/Qwen3-14B-AWQ", "served": "c4-two-thirds", "thinking": False, "sampling": {"temperature": 0.0}},
    "C4-1/3": {"model": "Qwen/Qwen3-14B-AWQ", "served": "c4-one-third", "thinking": False, "sampling": {"temperature": 0.0}},
    # E12 (pipeline/experiments/e12.py, A71): C4 read from its probabilities, "na" only past the cut tuned on E11's
    # validation days (docs/experiments/e12_cuts.json, written before this candidate is asked)
    "C4-T": {"model": "Qwen/Qwen3-14B-AWQ", "served": "c4", "thinking": False, "sampling": {"temperature": 0.0},
             "na_cut": "docs/experiments/e12_cuts.json"},
    # E13 (docs/experiments/e13_prereg.json, A72): both orders averaged — C4 as trained, and E13's retrained adapters
    "C4-S": {"model": "Qwen/Qwen3-14B-AWQ", "served": "c4", "thinking": False, "sampling": {"temperature": 0.0}, "swap": True},
    "C5": {"model": "Qwen/Qwen3-14B-AWQ", "served": "c5", "thinking": False, "sampling": {"temperature": 0.0}},
    "C5-S": {"model": "Qwen/Qwen3-14B-AWQ", "served": "c5", "thinking": False, "sampling": {"temperature": 0.0}, "swap": True},
    "C6": {"model": "Qwen/Qwen3-14B-AWQ", "served": "c6", "thinking": False, "sampling": {"temperature": 0.0}},
    "C6-S": {"model": "Qwen/Qwen3-14B-AWQ", "served": "c6", "thinking": False, "sampling": {"temperature": 0.0}, "swap": True},
    # E13 amended: C7 = C4-1/3 plus balanced verdict weights, no swap (C6 stacked both and was stopped untested)
    "C7": {"model": "Qwen/Qwen3-14B-AWQ", "served": "c7", "thinking": False, "sampling": {"temperature": 0.0}},
    # E13 amended (A73): the same four on Gemma 4 12B (server: infra/pc/llm-gemma.env) — untrained, LoRA as C4-1/3,
    # LoRA-swap as C5, LoRA-weights as C7; same days, steps and settings
    "G1": {"model": "google/gemma-4-12B-it-qat-w4a16-ct", "thinking": False, "sampling": {"temperature": 0.0}},
    "G4-1/3": {"model": "google/gemma-4-12B-it-qat-w4a16-ct", "served": "g4-one-third", "thinking": False, "sampling": {"temperature": 0.0}},
    "G5": {"model": "google/gemma-4-12B-it-qat-w4a16-ct", "served": "g5", "thinking": False, "sampling": {"temperature": 0.0}},
    "G7": {"model": "google/gemma-4-12B-it-qat-w4a16-ct", "served": "g7", "thinking": False, "sampling": {"temperature": 0.0}},
    # E13: Gemma's learning curve, as E11's for Qwen — G4-1/3's recipe on two thirds and all of the days
    "G4-2/3": {"model": "google/gemma-4-12B-it-qat-w4a16-ct", "served": "g4-two-thirds", "thinking": False, "sampling": {"temperature": 0.0}},
    "G4": {"model": "google/gemma-4-12B-it-qat-w4a16-ct", "served": "g4", "thinking": False, "sampling": {"temperature": 0.0}},
}
HARNESS = 2
PARALLEL = 16
BOOT, SEED = 2000, 2027
BOOT_POSITIONS = 1000         # J3 refits the model per resample (spreads held at the full fit's)


def purpose_of(candidate: str) -> str:
    return f"e9h{HARNESS}-{candidate}"


def LocalJudge(client, spec: dict):
    """The candidate as the shared local judge (pipeline.judge.client.LocalJudge): its sampling and thinking switch;
    a candidate with a cut file is read from its probabilities instead (ThresholdJudge, E12)."""
    if spec.get("swap"):
        return SwapJudge(client, served=spec["served"])
    if "na_cut" in spec:
        return ThresholdJudge(json.loads(Path(spec["na_cut"]).read_text())["na_cut"], client, served=spec["served"])
    return _LocalJudge(client, thinking=spec["thinking"], sampling=spec["sampling"], served=spec.get("served", "judge"))


def reference_calls(conn) -> list:
    return conn.execute("""SELECT * FROM judge_calls WHERE model = 'gpt-5.4-mini' AND prompt_version = 'v4'
                           AND purpose IN ('measure', 'eval') ORDER BY at, call_id""").fetchall()


def articles(conn, ids) -> dict:
    ids = list(ids)
    out = {}
    for k in range(0, len(ids), 900):
        chunk = ids[k:k + 900]
        out.update({r["id"]: r for r in conn.execute(
            f"SELECT id, outlet_id, title, summary, full_text, published FROM articles WHERE id IN ({','.join('?' * len(chunk))})",
            chunk)})
    return out


def asked(conn, candidate: str) -> set[str]:
    """Reference calls this candidate already answered (or failed) under the current harness: a run resumes."""
    return {r[0] for r in conn.execute(
        """SELECT json_extract(context, '$.ref_call') FROM judge_call_log
           WHERE json_extract(context, '$.candidate') = ? AND json_extract(context, '$.harness') = ?""",
        (candidate, HARNESS))}


def run(candidate: str, limit: int | None = None, client=None, conn=None) -> dict:
    spec = CANDIDATES[candidate]
    client = client or local_client()
    served = {m.id: getattr(m, "root", None) for m in client.models.list().data}
    if served.get("judge") != spec["model"]:
        raise SystemExit(f"the server runs {served.get('judge')}, {candidate} needs {spec['model']} (infra/pc/llm.env)")
    if spec.get("served", "judge") not in served:
        raise SystemExit(f"{candidate} needs the LoRA module {spec['served']} on the server (vLLM --lora-modules)")
    conn = conn or store.connect()
    purpose, done = purpose_of(candidate), asked(conn, candidate)
    refs = [r for r in reference_calls(conn) if r["call_id"] not in done][:limit]
    arts = articles(conn, {r["first_article"] for r in refs} | {r["second_article"] for r in refs})
    hist = store.headline_history(conn, list(arts))
    judge = LocalJudge(client, spec)
    print(f"{candidate}: {len(refs)} calls to ask ({len(done)} done before), harness {HARNESS}", flush=True)

    def ask(ref):
        a, b = arts[ref["first_article"]], arts[ref["second_article"]]
        try:                                           # a reply cut off at the token limit raises (harness 2)
            return ref, judge.compare(rendered(a, hist, ref["text_kind"]), rendered(b, hist, ref["text_kind"]),
                                      ref["direction"], "v4"), None
        except JudgeCallFailed as e:
            return ref, None, e

    counts = {"ok": 0, "failed": 0}
    with ThreadPoolExecutor(PARALLEL) as pool:
        for k, (ref, v, err) in enumerate(pool.map(ask, refs), 1):
            req = ComparisonRequest(ref["event_id"], "both", ref["first_outlet"], ref["second_outlet"],
                                    ref["first_article"], ref["second_article"])
            ctx = {"ref_call": ref["call_id"], "ref_purpose": ref["purpose"], "candidate": candidate,
                   "harness": HARNESS, **spec}
            if v is None:
                record_failure(conn, purpose, req, err, ctx)
                counts["failed"] += 1
            else:
                record(conn, purpose, req, ref["text_kind"], v, ref["direction"], ref["event_id"], "v4", context=ctx)
                counts["ok"] += 1
            if k % 50 == 0:
                conn.commit()
                print(f"  {k}/{len(refs)}", flush=True)
    conn.commit()
    print(f"{candidate}: done, {counts['failed']} failed calls")
    return counts


# ---- analysis -----------------------------------------------------------------------------------------------------
def verdicts(conn, purpose_sql: str, params=()) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for r in conn.execute(f"SELECT j.call_id, j.axis, j.verdict FROM judgments j JOIN judge_calls c ON c.call_id = j.call_id "
                          f"WHERE {purpose_sql}", params):
        out.setdefault(r["call_id"], {})[r["axis"]] = r["verdict"]
    return out


def interval(x: np.ndarray, rng, boot: int = BOOT) -> list[float]:
    """[mean, 2.5 %, 97.5 %] by bootstrap over the items."""
    if len(x) == 0:
        return [float("nan")] * 3
    boots = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(boot)]
    return [round(float(x.mean()), 3), round(float(np.percentile(boots, 2.5)), 3), round(float(np.percentile(boots, 97.5)), 3)]


def ceiling(conn, rng) -> dict:
    """gpt-5.4-mini against itself: v4 eval pairs it was asked twice, same order and direction (J2)."""
    calls = conn.execute("""SELECT call_id, first_article, second_article, direction FROM judge_calls
                            WHERE model = 'gpt-5.4-mini' AND prompt_version = 'v4' AND purpose = 'eval' ORDER BY at""").fetchall()
    by = {}
    for c in calls:
        by.setdefault((c["first_article"], c["second_article"], c["direction"]), []).append(c["call_id"])
    pairs = [v[:2] for v in by.values() if len(v) >= 2]
    v = verdicts(conn, "c.model = 'gpt-5.4-mini' AND c.prompt_version = 'v4' AND c.purpose = 'eval'")
    return {"pairs": len(pairs), **{ax: interval(np.array([v[a].get(ax) == v[b].get(ax) for a, b in pairs], float), rng)
                                    for ax in P.AXES}}


POS_SQL = """SELECT c.call_id, c.first_outlet, c.second_outlet, c.first_article, c.second_article, c.text_kind,
                    j.axis, j.first_wins FROM judgments j JOIN judge_calls c ON c.call_id = j.call_id """


def positions(rows, outlets, hyper=None) -> tuple[dict, dict]:
    """Outlet positions per axis from verdict rows, with live's hierarchical model (as pipeline.measure.run.fit)."""
    pos, hy = {}, {}
    for ax in P.AXES:
        rs = [r for r in rows if r["axis"] == ax and r["first_wins"] is not None]
        comps = [(r["first_outlet"], r["second_outlet"], r["first_article"], r["second_article"],
                  A_FULL if r["text_kind"] == "full" else A_SUMMARY) for r in rs]
        t, s_ = hyper[ax] if hyper else (None, None)
        h = HierarchicalBT(outlets, ax).fit(comps, [bool(r["first_wins"]) for r in rs], sigma_a=s_, tau=t)
        pos[ax] = h.mean[:len(outlets)]
        hy[ax] = max(h.evidence, key=h.evidence.get) if h.evidence else (h.tau, h.sigma_a)
    return pos, hy


def j3(conn, cand: str, rng, boot: int = BOOT_POSITIONS) -> dict:
    """Outlet positions from gpt-5.4-mini's verdicts vs the candidate's, on the live calls only; Spearman per axis."""
    from scipy.stats import spearmanr
    purpose = purpose_of(cand)
    ref_rows = conn.execute(POS_SQL + "WHERE c.model = 'gpt-5.4-mini' AND c.prompt_version = 'v4' AND c.purpose = 'measure'"
                            ).fetchall()
    link = dict(conn.execute("""SELECT c.call_id, json_extract(l.context, '$.ref_call') FROM judge_call_log l
                                JOIN judge_calls c ON c.call_id = l.call_id WHERE c.purpose = ?
                                AND json_extract(l.context, '$.ref_purpose') = 'measure'""", (purpose,)).fetchall())
    cand_rows = [dict(r) | {"ref": link[r["call_id"]]} for r in conn.execute(POS_SQL + "WHERE c.purpose = ?", (purpose,))
                 if r["call_id"] in link]
    refs = sorted({r["ref"] for r in cand_rows})              # the live calls both judged
    keep = set(refs)
    ref_rows = [r for r in ref_rows if r["call_id"] in keep]
    outlets = sorted({r["first_outlet"] for r in ref_rows} | {r["second_outlet"] for r in ref_rows})
    if len(refs) < 2 or len(outlets) < 3:
        return {"calls": len(refs), "outlets": len(outlets), **{ax: [None, None, None] for ax in P.AXES}}
    p_ref, h_ref = positions(ref_rows, outlets)
    p_c, h_c = positions(cand_rows, outlets)
    out = {"calls": len(refs), "outlets": len(outlets)}
    by_ref = {k: [] for k in refs}
    by_cand = {k: [] for k in refs}
    for r in ref_rows:
        by_ref[r["call_id"]].append(r)
    for r in cand_rows:
        by_cand[r["ref"]].append(r)
    boots = {ax: [] for ax in P.AXES}
    for _ in range(boot):
        pick = rng.choice(len(refs), len(refs), replace=True)
        a, _ = positions([x for k in pick for x in by_ref[refs[k]]], outlets, h_ref)
        b, _ = positions([x for k in pick for x in by_cand[refs[k]]], outlets, h_c)
        for ax in P.AXES:
            boots[ax].append(spearmanr(a[ax], b[ax]).statistic)
    for ax in P.AXES:
        rho = spearmanr(p_ref[ax], p_c[ax]).statistic
        out[ax] = [round(float(rho), 3), round(float(np.nanpercentile(boots[ax], 2.5)), 3),
                   round(float(np.nanpercentile(boots[ax], 97.5)), 3)]
    return out


def overlaps(a: list, b: list) -> bool:
    """Do two [mean, lo, hi] intervals overlap?"""
    return a[1] is not None and b[1] is not None and a[1] <= b[2] and b[1] <= a[2]


def decide(out: dict) -> dict:
    """The pre-registered rule: the candidate with the highest mean J1 over both axes; valid if its J1 interval overlaps
    J2's on both axes. Speed breaks ties only."""
    cands = out["candidates"]
    if not cands:
        return {"history_judge": None, "valid": False, "reason": "no candidate has run"}
    mean_j1 = {c: np.mean([r[f"J1_{ax}"][0] for ax in P.AXES]) for c, r in cands.items()}
    best = max(cands, key=lambda c: (round(float(mean_j1[c]), 3), -cands[c]["mean_output_tokens"]))
    ok = {ax: overlaps(cands[best][f"J1_{ax}"], out["J2_ceiling"][ax]) for ax in P.AXES}
    return {"history_judge": best, "mean_J1": round(float(mean_j1[best]), 3), "overlaps_ceiling": ok,
            "valid": all(ok.values())}


def report(out: dict) -> str:
    c2 = out["J2_ceiling"]
    L = ["# E9 — a local judge for the history vs gpt-5.4-mini", "",
         f"Pre-registration `docs/experiments/e9_prereg.json` (written {out['prereg']}). Harness {out['harness']} "
         f"(see `pipeline/experiments/e9.py` for the harness-1 fix). {out['reference_calls']} reference calls. 95 % "
         f"intervals by bootstrap. Not a claim (pipeline phase).", "",
         f"**Ceiling (J2):** gpt-5.4-mini against itself on {c2['pairs']} pairs asked twice — "
         + ", ".join(f"{ax} {c2[ax][0]:.0%} [{c2[ax][1]:.0%}, {c2[ax][2]:.0%}]" for ax in P.AXES), "",
         "| candidate | asked | failed | agreement econ (J1) | agreement social (J1) | positions ρ econ (J3) | positions ρ social (J3) | same/na econ · social (gpt) | output tokens |",
         "|---|---:|---:|---|---|---|---|---|---:|"]
    for c, r in out["candidates"].items():
        fmt = lambda x: "–" if x[0] is None else f"{x[0]:.2f} [{x[1]:.2f}, {x[2]:.2f}]"   # noqa: E731
        L.append(f"| {c} ({r['spec']['model'].split('/')[-1]}, thinking {'on' if r['spec']['thinking'] else 'off'}) | "
                 f"{r['asked']} | {r['failed']} | {fmt(r['J1_econ'])} | {fmt(r['J1_social'])} | "
                 f"{fmt(r['J3_positions_spearman']['econ'])} | {fmt(r['J3_positions_spearman']['social'])} | "
                 f"{r['same_or_na_econ']:.0%} · {r['same_or_na_social']:.0%} ({r['gpt_same_or_na']['econ']:.0%} · "
                 f"{r['gpt_same_or_na']['social']:.0%}) | {r['mean_output_tokens']:.0f} |")
    L += ["", "**What the disagreements cost (reported, not decided on):** flip = gpt A, local B (wrong way); miss = gpt "
              "decides, local abstains (a wasted call); invent = gpt abstains, local decides (noise). Shares of all calls; "
              "κ = Cohen's kappa (0 chance, 1 perfect); 'always na' = what abstaining on every call would agree.", "",
          "| candidate | κ econ · social | always na econ · social | flip econ · social (of both decided) | miss econ · social | invent econ · social |",
          "|---|---|---|---|---|---|"]
    for c, r in out["candidates"].items():
        if "errors_econ" not in r:
            continue
        e, so = r["errors_econ"], r["errors_social"]
        L.append(f"| {c} | {r['kappa_econ']:.2f} · {r['kappa_social']:.2f} | {r['always_na_econ']:.0%} · "
                 f"{r['always_na_social']:.0%} | {e['flip']:.1%} ({e['flip_when_both_decide']:.0%}) · {so['flip']:.1%} "
                 f"({so['flip_when_both_decide']:.0%}) | {e['miss']:.0%} · {so['miss']:.0%} | {e['invent']:.0%} · {so['invent']:.0%} |")
    d = out["decision"]
    L += ["", f"**Decision rule (pre-registered):** highest mean J1 → **{d.get('history_judge')}**; valid if its J1 "
              f"interval overlaps the ceiling on both axes → **{'valid' if d.get('valid') else 'not valid'}** "
              f"({d.get('overlaps_ceiling')}). The maintainer decides (D1)."]
    return "\n".join(L) + "\n"


def errors(ref: list, mine: list) -> dict:
    """What a disagreement costs (reported, not decided on): a flip (gpt A, local B — pushes positions the wrong way),
    a miss (gpt decides, local abstains — a wasted call) or an invention (gpt abstains, local decides — noise). Shares
    of the calls; flips also as a share of the calls where both decided."""
    dec = lambda v: v in ("A", "B")                                              # noqa: E731
    both = [(r, m) for r, m in zip(ref, mine) if dec(r) and dec(m)]
    n = max(len(ref), 1)
    return {"flip": round(sum(r != m for r, m in both) / n, 3),
            "flip_when_both_decide": round(sum(r != m for r, m in both) / max(len(both), 1), 3),
            "miss": round(sum(dec(r) and not dec(m) for r, m in zip(ref, mine)) / n, 3),
            "invent": round(sum(not dec(r) and dec(m) for r, m in zip(ref, mine)) / n, 3)}


def kappa(a: list, b: list) -> float:
    """Cohen's kappa: agreement beyond what the two answer mixes would give by chance (reported, not decided on)."""
    labels = sorted({x for x in a + b if x is not None}, key=str)
    if not a or len(labels) < 2:
        return float("nan")
    po = float(np.mean([x == y for x, y in zip(a, b)]))
    pe = sum((a.count(lab) / len(a)) * (b.count(lab) / len(b)) for lab in labels)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else float("nan")


def analyse(conn=None, out_dir: Path = Path("reports"), boot_positions: int = BOOT_POSITIONS) -> dict:
    conn = conn or store.connect()
    rng = np.random.default_rng(SEED)
    ref = verdicts(conn, "c.model = 'gpt-5.4-mini' AND c.prompt_version = 'v4' AND c.purpose IN ('measure', 'eval')")
    out = {"prereg": json.loads(PREREG.read_text())["written_at"], "harness": HARNESS, "reference_calls": len(ref),
           "J2_ceiling": ceiling(conn, rng), "candidates": {}}
    for cand, spec in CANDIDATES.items():
        purpose = purpose_of(cand)
        rows = conn.execute("""SELECT c.call_id, json_extract(l.context, '$.ref_call') ref, l.status, c.output_tokens
                               FROM judge_call_log l LEFT JOIN judge_calls c ON c.call_id = l.call_id
                               WHERE json_extract(l.context, '$.candidate') = ? AND json_extract(l.context, '$.harness') = ?""",
                            (cand, HARNESS)).fetchall()
        if not rows:
            continue
        mine = verdicts(conn, "c.purpose = ?", (purpose,))
        ok = [(r["ref"], r["call_id"]) for r in rows if r["call_id"] in mine and r["ref"] in ref]
        res = {"spec": spec, "asked": len(rows), "failed": sum(r["status"] != "ok" for r in rows),
               "mean_output_tokens": round(float(np.mean([r["output_tokens"] or 0 for r in rows if r["call_id"]] or [0])), 1)}
        for ax in P.AXES:
            agree = np.array([ref[r].get(ax) == mine[c].get(ax) for r, c in ok], float)
            res[f"J1_{ax}"] = interval(agree, rng)
            res[f"kappa_{ax}"] = kappa([ref[r].get(ax) for r, _ in ok], [mine[c].get(ax) for _, c in ok])
            res[f"always_na_{ax}"] = round(float(np.mean([ref[r].get(ax) in ("same", "na") for r, _ in ok] or [np.nan])), 3)
            res[f"errors_{ax}"] = errors([ref[r].get(ax) for r, _ in ok], [mine[c].get(ax) for _, c in ok])
            res[f"same_or_na_{ax}"] = round(float(np.mean([mine[c].get(ax) in ("same", "na") for _, c in ok] or [np.nan])), 3)
        res["gpt_same_or_na"] = {ax: round(float(np.mean([ref[r].get(ax) in ("same", "na") for r, _ in ok] or [np.nan])), 3)
                                 for ax in P.AXES}
        res["J3_positions_spearman"] = j3(conn, cand, rng, boot_positions)
        out["candidates"][cand] = res
    out["decision"] = decide(out)
    out_dir.mkdir(exist_ok=True)
    (out_dir / "e9.json").write_text(json.dumps(out, indent=1))
    (out_dir / "e9.md").write_text(report(out))
    print(report(out))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["run", "analyse"])
    ap.add_argument("--candidate", choices=list(CANDIDATES))
    ap.add_argument("--limit", type=int, help="ask only the first N (a smoke test; the rest later)")
    args = ap.parse_args()
    if args.what == "run":
        if not args.candidate:
            ap.error("run needs --candidate")
        run(args.candidate, args.limit)
    else:
        analyse()


if __name__ == "__main__":
    main()
