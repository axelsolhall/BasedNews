"""4.3 Site frames (A82): positions → the site's precomputed, append-only binary frames. No fitting, only reading.

    PYTHONPATH=. uv run python -m pipeline.export.frames history     # the history → site/data/history/ (committed)
    uv run python -m pipeline.export.frames live                     # live positions → <site out>/live.json + live-*.bin
    uv run python -m pipeline.export.frames check [DIR]              # eval: decode the files, print what they hold

The page only plays these files (and smooths them for display). A frame is one moment: for every entity in the
manifest's order (countries, then outlets) four int16 — x, y, sd x, sd y — in units of SCALE, MISSING where the entity
has no position. Countries and outlets share one global scale: positions relative to one fixed
point (origin(): the average country on the history's last day), the same in the world view and a country's view. One frame per STEP_DAYS days counted from the first day, and the latest day.

  - History: one file per year, named by its content (cached for good); manifest.json (never cached) lists them, with
    the entities, the threshold and the inconclusive outlets.
  - Live: live.json + live-<hash>.bin, every frame after the history's last day, rewritten whole each day (≈ 2 kB a
    frame). Its entities are the history's (same rule, same prior counts), so the two files join frame by frame.

Shown (A83): an outlet with at least THRESHOLD history comparisons, a country whose outlets' comparisons sum to at
least THRESHOLD (so two outlets of 2,500 each show their country, not themselves); the other outlets are listed as
inconclusive with their count. Each enters the frames from the first frame its comparisons so far (by event day) reach
THRESHOLD (`since`): the graph fills in as the evidence does. Positions are on one global scale with a fixed origin
(origin()), so a dot moves only when its own score does.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import numpy as np

from pipeline.export.geo import COUNTRIES, REGIONS
from pipeline.ingest.feeds import utc_now_iso
from pipeline.judge import prompt as P
from pipeline.sources.registry import load_outlets

SCALE = 2e-4                      # int16 × SCALE: ±6.5 in model units, 0.0002 steps
MISSING = -32768
STEP_DAYS = 7
THRESHOLD = 5000                  # A83: picked from reports/threshold.md (2026-11-01)
HISTORY_DB = Path("data/history/history.sqlite")
HISTORY_OUT = Path("site/data/history")
PRIOR = Path("pipeline/measure/prior.json")


def pick_days(days: list[str], first: str | None = None, step: int = STEP_DAYS) -> list[str]:
    """The last day of each `step`-day bin counted from `first` (default: the first day) — so the latest day too."""
    if not days:
        return []
    t0 = dt.date.fromisoformat(first or days[0])
    last_of: dict[int, str] = {}
    for d in sorted(days):
        last_of[(dt.date.fromisoformat(d) - t0).days // step] = d
    return [last_of[b] for b in sorted(last_of)]


def country_counts(counts: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for o in load_outlets():
        out[o.country] = out.get(o.country, 0) + counts.get(o.id, 0)
    return out


def entities(counts: dict[str, int], threshold: int = THRESHOLD) -> tuple[list[dict], list[dict]]:
    """(shown entities: countries then outlets, inconclusive outlets) — the registry's order within each."""
    outlets = load_outlets()
    shown = [o for o in outlets if counts.get(o.id, 0) >= threshold]
    cn = country_counts(counts)
    ents = [{"id": c, "kind": "c", "name": c, "iso": iso, "region": r, "n": cn.get(c, 0)}
            for c, iso, r in COUNTRIES if cn.get(c, 0) >= threshold]
    ents += [{"id": o.id, "kind": "o", "name": o.name, "country": o.country, "tier": o.tier, "n": counts[o.id]}
             for o in shown]
    rest = [{"id": o.id, "name": o.name, "country": o.country, "n": counts.get(o.id, 0)}
            for o in outlets if counts.get(o.id, 0) < threshold]
    return ents, rest


def since(db: Path, days: list[str], threshold: int) -> dict[str, str]:
    """{outlet or country: the first of `days` by which its history comparisons (by event day, A81 left out) reach
    `threshold`} — absent if never."""
    from pipeline.history import positions as HP
    h = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    country = {o.id: o.country for o in load_outlets()}
    per: dict[str, dict[str, int]] = {}
    for a, b, d, k in h.execute(f"""SELECT c.first_outlet, c.second_outlet, substr(c.event_id, 1, 10), count(*)
                                    FROM judge_calls c WHERE c.purpose = 'history' AND {HP.not_foreign_call('c')}
                                    GROUP BY 1, 2, 3"""):
        for o in (a, b):
            for key in (o, country.get(o)):
                if key:
                    per.setdefault(key, {}).setdefault(d, 0)
                    per[key][d] += k
    out = {}
    for key, by_day in per.items():
        total, sd = 0, sorted(by_day.items())
        j = 0
        for d in days:                                   # frames in order: add the days up to each
            while j < len(sd) and sd[j][0] <= d:
                total += sd[j][1]
                j += 1
            if total >= threshold:
                out[key] = d
                break
    return out


def origin(pr: dict, ents: list[dict]) -> dict:
    """Fixed reference points (A83, 2026-11-08), from the prior (the history's last day): each country's position then
    (its reference outlets, weighted as A08 does: log of worth) and the world's (their mean over the countries shown).
    Fixed, so a dot moves only when its own score does — never because another outlet or country moved or entered."""
    from pipeline.sources.registry import reference_groups
    ex, sx = P.AXES
    o = pr["outlets"]
    out = {}
    for c, group in reference_groups(load_outlets()).items():
        have = [k for k in group if k in o and ex in o[k] and sx in o[k]]
        w = np.array([np.log(o[k].get("worth", 0.0)) if o[k].get("worth", 0.0) > 1.0 else 0.0 for k in have])
        if not have:
            continue
        w = w if w.sum() > 0 else np.ones(len(have))
        out[c] = (float(np.average([o[k][ex][0] for k in have], weights=w)),
                  float(np.average([o[k][sx][0] for k in have], weights=w)))
    cs = [out[e["id"]] for e in ents if e["kind"] == "c" and e["id"] in out]
    out[None] = tuple(np.mean(cs, axis=0)) if cs else (0.0, 0.0)
    return out


def encode(days: list[str], ents: list[dict], pos: dict, cpos: dict, start: dict[str, str] | None = None,
           ref: dict | None = None) -> np.ndarray:
    """(frames, entities, 4) int16. pos / cpos: {(day, axis, id): (mean, sd)} for outlets / countries; `start`: an
    entity is missing before its day there (since()), none if absent from it; without `start` every entity is shown.
    `ref` (origin()): every country and outlet is placed relative to the one fixed point ref[None]."""
    ex, sx = P.AXES                                      # econ = x, social = y
    ref = ref or {}
    out = np.full((len(days), len(ents), 4), MISSING, dtype=np.int16)
    q = lambda v: int(np.clip(round(v / SCALE), -32767, 32767))          # noqa: E731
    shown = (lambda i, d: True) if start is None else (lambda i, d: i in start and start[i] <= d)     # noqa: E731
    for f, d in enumerate(days):
        for k, e in enumerate(ents):
            if not shown(e["id"], d):
                continue
            src = cpos if e["kind"] == "c" else pos
            ox, oy = ref.get(None, (0.0, 0.0))             # one global scale: an outlet is where it is, in any view
            a, b = src.get((d, ex, e["id"])), src.get((d, sx, e["id"]))
            if a is None or b is None:
                continue
            out[f, k] = (q(a[0] - ox), q(b[0] - oy), q(a[1]), q(b[1]))
    return out


def decode(buf: bytes, n_ents: int) -> np.ndarray:
    """The inverse of encode's layout: (frames, entities, 4) floats, NaN where missing."""
    a = np.frombuffer(buf, dtype="<i2").reshape(-1, n_ents, 4)
    f = a.astype(np.float32) * SCALE
    f[a == MISSING] = np.nan
    return f


def read(conn, table: str, key: str, days: list[str]) -> dict:
    q = ",".join("?" * len(days))
    return {(d, ax, i): (m, s) for d, ax, i, m, s in
            conn.execute(f"SELECT day, axis, {key}, mean, sd FROM {table} WHERE day IN ({q})", days)}


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def named(prefix: str, data: bytes) -> str:
    return f"{prefix}.{hashlib.sha256(data).hexdigest()[:10]}.bin"


def history(db: Path = HISTORY_DB, out: Path = HISTORY_OUT, threshold: int | None = None,
            prior: Path | None = None, benchmarks: dict | None = None) -> dict:
    """The whole history → one frame file per year + manifest.json; files no longer listed are removed. `benchmarks`
    (pipeline.history.threshold.sweep: agreement with outside ratings per minimum) goes into the manifest for the page."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    all_days = [r[0] for r in conn.execute("SELECT DISTINCT day FROM history_positions ORDER BY day")]
    if not all_days:
        raise SystemExit("the history has no positions")
    prior, threshold = prior or PRIOR, threshold or THRESHOLD
    pr = json.loads(prior.read_text())
    if pr["as_of"] != all_days[-1]:
        raise SystemExit(f"{prior} is of {pr['as_of']}, the history ends {all_days[-1]}: run pipeline.history.prior --json")
    counts = {o: v["n"] for o, v in pr["outlets"].items()}
    days = pick_days(all_days)
    ents, rest = entities(counts, threshold)
    start = since(db, days, threshold)
    if start:                                          # frames before anything is shown are empty: the timeline starts later
        days = [d for d in days if d >= min(start.values())]
    for e in ents:
        e["since"] = start.get(e["id"])
    arr = encode(days, ents, read(conn, "history_positions", "outlet", days),
                 read(conn, "history_country_positions", "country", days), start, origin(pr, ents))
    out.mkdir(parents=True, exist_ok=True)
    chunks = []
    for year in sorted({d[:4] for d in days}):
        ks = [k for k, d in enumerate(days) if d[:4] == year]
        data = arr[ks].astype("<i2").tobytes()
        name = named(f"h-{year}", data)
        if not (out / name).exists():
            _write(out / name, data)
        chunks.append({"file": name, "days": [days[k] for k in ks]})
    manifest = {"version": 1, "scale": SCALE, "missing": MISSING, "step_days": STEP_DAYS, "axes": list(P.AXES),
                "first": all_days[0], "last": all_days[-1], "threshold": threshold, "regions": REGIONS,
                "entities": ents, "inconclusive": rest, "chunks": chunks, "benchmarks": benchmarks,
                "generated": utc_now_iso()}
    _write(out / "manifest.json", json.dumps(manifest, separators=(",", ":")).encode())
    keep = {c["file"] for c in chunks} | {"manifest.json"}
    for f in out.glob("h-*.bin"):
        if f.name not in keep:
            f.unlink()
    return manifest


def live(conn, out: Path, threshold: int | None = None, prior: Path | None = None) -> dict:
    """The live days after the history (live_positions) → live.json + live-<hash>.bin in `out`."""
    pr = json.loads((prior or PRIOR).read_text())
    threshold = threshold or THRESHOLD
    counts = {o: v["n"] for o, v in pr["outlets"].items()}
    ents, _ = entities(counts, threshold)
    nxt = (dt.date.fromisoformat(pr["as_of"]) + dt.timedelta(days=1)).isoformat()
    all_days = [r[0] for r in conn.execute("SELECT DISTINCT day FROM live_positions WHERE day >= ? ORDER BY day", (nxt,))]
    days = pick_days(all_days, first=nxt)
    arr = encode(days, ents, read(conn, "live_positions", "outlet", days), read(conn, "live_country_positions", "country", days),
                 ref=origin(pr, ents)) if days else np.zeros((0, len(ents), 4), np.int16)
    data = arr.astype("<i2").tobytes()
    out.mkdir(parents=True, exist_ok=True)
    name = named("live", data)
    _write(out / name, data)
    meta = {"version": 1, "history_last": pr["as_of"], "entities": [e["id"] for e in ents], "file": name,
            "days": days, "updated": utc_now_iso()}
    _write(out / "live.json", json.dumps(meta, separators=(",", ":")).encode())
    for f in out.glob("live-*.bin"):                 # the previous day's file: the page reads live.json first
        if f.name != name:
            f.unlink()
    return meta


def check(root: Path) -> str:
    """Decode what is there and say what it holds (the export gate reads the live half the same way)."""
    lines = []
    m = root / "manifest.json"
    if m.exists():
        man = json.loads(m.read_text())
        n = len(man["entities"])
        frames = [decode((root / c["file"]).read_bytes(), n) for c in man["chunks"]]
        f = np.concatenate(frames)
        assert f.shape[0] == sum(len(c["days"]) for c in man["chunks"]), "frames ≠ days"
        shown = sum(e["kind"] == "o" for e in man["entities"])
        lines.append(f"history {man['first']} → {man['last']}: {f.shape[0]} frames × {n} entities "
                     f"({n - shown} countries, {shown} outlets; {len(man['inconclusive'])} inconclusive below "
                     f"{man['threshold']:,}) · {sum((root / c['file']).stat().st_size for c in man['chunks']) / 1e6:.2f} MB"
                     f" · missing {np.isnan(f).any(axis=2).mean():.1%} · |x| max {np.nanmax(np.abs(f[..., :2])):.2f}")
    lv = root / "live.json"
    if lv.exists():
        meta = json.loads(lv.read_text())
        f = decode((root / meta["file"]).read_bytes(), len(meta["entities"]))
        assert f.shape[0] == len(meta["days"]), "live frames ≠ days"
        lines.append(f"live after {meta['history_last']}: {f.shape[0]} frames ({', '.join(meta['days'][-3:])})")
    return "\n".join(lines) or f"nothing in {root}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["history", "live", "check"])
    ap.add_argument("dir", nargs="?")
    ap.add_argument("--threshold", type=int)
    a = ap.parse_args()
    if a.what == "history":
        from pipeline.history.threshold import sweep
        out = Path(a.dir or HISTORY_OUT)
        day, rows = sweep()
        history(out=out, threshold=a.threshold, benchmarks={"day": day, "rows": rows})
        print(check(out))
    elif a.what == "live":
        from pipeline import store
        from pipeline.export.run import out_dir
        out = Path(a.dir) if a.dir else out_dir()
        from pipeline.measure.showcase import export_json
        conn = store.connect()
        live(conn, out, a.threshold)
        ex = export_json(conn, out)                     # A85: the day's examples, beside the frames
        print(check(out) + f"\nexamples: {len(ex['examples'])} daily")
    else:
        print(check(Path(a.dir or HISTORY_OUT)))


if __name__ == "__main__":
    main()
