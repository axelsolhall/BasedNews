"""4.2 Export: the current fit → static JSON for the site (ADR-010). Reads verdicts, writes one file; no AI calls.

    uv run python -m pipeline.export.run [--out frontend/src/data]      # build + check
    uv run python -m pipeline.export.run --check                          # check the existing file only

Refits the hierarchical model (A49) from every real verdict of the current prompt version, places outlets and
countries relative to the average outlet (A25, A26), and writes `plane-latest.json` in the shape of one E4 frame, so the
site draws it with the same component. Only aggregates leave the private side (A10): no text, no titles, no URLs.

The default output folder is git-ignored and served by `ng serve` at /data/. In production the job runner uploads it to
the site bucket only when BASEDNEWS_PUBLISH_POSITIONS=1 (A54): real positions go public by the maintainer's decision.
Check (also the job runner's gate): valid JSON, finite numbers, every country present, gzip size under the budget.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
from collections import Counter
from pathlib import Path

from pipeline import store
from pipeline.aggregate.plane import OVAL_SD_MULTIPLIER, _contrast_stats, dumps_site
from pipeline.ingest.feeds import utc_now_iso
from pipeline.judge import prompt as P
from pipeline.sources.registry import load_outlets, reference_groups
from pipeline.version import commit

OUT = Path("frontend/src/data")
FILE = "plane-latest.json"
BUDGET_GZIP_BYTES = 100_000          # ADR-010: each view loads under ~100 kB compressed


class NothingToExport(RuntimeError):
    pass


def out_dir() -> Path:
    return Path(os.environ.get("BASEDNEWS_SITE_OUT") or OUT)


def build(conn, purpose: str = "measure") -> dict:
    from pipeline.measure.run import fit                       # heavy import (judge client); only when building
    reg = load_outlets()
    names = {o.id: o for o in reg}
    groups = reference_groups(reg)
    calls = conn.execute("""SELECT first_outlet, second_outlet, model, cost_usd, at FROM judge_calls
                            WHERE purpose = ? AND prompt_version = ?""", (purpose, P.PROMPT_VERSION)).fetchall()
    if not calls:
        raise NothingToExport(f"no {purpose} verdicts for prompt {P.PROMPT_VERSION} yet")
    n_comp = Counter()
    for c in calls:
        n_comp[c["first_outlet"]] += 1
        n_comp[c["second_outlet"]] += 1
    outlet_ids = sorted(set(n_comp) | {o for g in groups.values() for o in g})
    posts, n_rows = fit(conn, outlet_ids, purpose)
    op = {ax: posts[ax].outlet_posterior() for ax in P.AXES}
    outlets = []
    for o in outlet_ids:
        x, sx = _contrast_stats(op["econ"], {o: 1.0})
        y, sy = _contrast_stats(op["social"], {o: 1.0})
        meta = names.get(o)
        outlets.append({"id": o, "label": meta.name if meta else o, "country": meta.country if meta else "",
                        "tier": meta.tier if meta else "extra", "x": x, "y": y, "sd_x": sx, "sd_y": sy, "n": n_comp[o]})
    countries = []
    for country, w in sorted(groups.items()):
        x, sx = _contrast_stats(op["econ"], w)
        y, sy = _contrast_stats(op["social"], w)
        countries.append({"id": country, "label": country, "country": country, "x": x, "y": y, "sd_x": sx, "sd_y": sy})
    return {
        "meta": {"source": "real", "kind": "latest_fit", "validated": False, "prompt": P.PROMPT_VERSION,
                 "models": sorted({c["model"] for c in calls}), "rating_model": "hierarchical (A49)",
                 "calls": len(calls), "decisive_verdicts": n_rows, "first_call": min(c["at"] for c in calls),
                 "last_call": max(c["at"] for c in calls), "fitted_at": utc_now_iso(), "commit": commit(),
                 "oval_sd_multiplier": OVAL_SD_MULTIPLIER,
                 "spreads": {ax: {"tau": posts[ax].tau, "sigma_a": posts[ax].sigma_a} for ax in P.AXES}},
        "frame": {"calls": len(calls), "cost_usd": round(sum(c["cost_usd"] or 0 for c in calls), 2),
                  "outlets": outlets, "countries": countries},
    }


def _numbers(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _numbers(v)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield obj


def check(path: Path) -> dict:
    """The export's eval: what the browser downloads is valid, complete and small enough."""
    if not path.exists():
        return {"ok": False, "reason": f"{path} missing"}
    raw = path.read_bytes()
    data = json.loads(raw)
    frame = data["frame"]
    expected = set(reference_groups(load_outlets()))
    got = {c["id"] for c in frame["countries"]}
    finite = all(math.isfinite(v) for v in _numbers(data))
    gz = len(gzip.compress(raw, 9))
    ok = finite and got == expected and gz <= BUDGET_GZIP_BYTES and len(frame["outlets"]) > 0
    reasons = [r for r, bad in (("non-finite numbers", not finite), (f"countries missing: {sorted(expected - got)}", got != expected),
                                (f"gzip {gz} B over budget {BUDGET_GZIP_BYTES} B", gz > BUDGET_GZIP_BYTES),
                                ("no outlets", not frame["outlets"])) if bad]
    return {"ok": ok, "reason": "; ".join(reasons), "bytes": len(raw), "gzip_bytes": gz, "outlets": len(frame["outlets"]),
            "measured_outlets": sum(1 for o in frame["outlets"] if o.get("n")), "countries": len(got),
            "calls": data["meta"]["calls"], "prompt": data["meta"]["prompt"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None, help=f"output folder (default: BASEDNEWS_SITE_OUT or {OUT})")
    ap.add_argument("--check", action="store_true", help="only check the existing file")
    args = ap.parse_args()
    path = (args.out or out_dir()) / FILE
    if not args.check:
        data = build(store.connect())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps_site(data))
        print(f"wrote {path}: {data['meta']['calls']} calls (prompt {data['meta']['prompt']}), "
              f"{len(data['frame']['outlets'])} outlets, {len(data['frame']['countries'])} countries")
    res = check(path)
    print(f"check: {'OK' if res['ok'] else 'FAIL — ' + res['reason']} · {res.get('bytes', 0):,} B, "
          f"gzip {res.get('gzip_bytes', 0):,} B (budget {BUDGET_GZIP_BYTES:,} B)")
    if not res["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
