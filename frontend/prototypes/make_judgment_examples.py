"""Judgment examples for the prototypes: REAL judge outputs (gpt-5.4-mini) with the model's own reasons.

    PYTHONPATH=. uv run python frontend/prototypes/make_judgment_examples.py   → frontend/prototypes/judgments.json

The live prompt (v4) gives no reasons, so the examples come from the prompt-v2 eval run, which asked for one short
reason per axis. Nothing is bought: these calls are already archived (A51). What is shown follows A10: outlet,
headline, link, date, the verdict and the model's reason — never article text. The file is git-ignored until the
maintainer decides to publish examples (it is real data, not demo data).

Selection is a fixed rule, not taste (so the examples are not picked to look right): calls where the judge decided at
least one axis, reasons of a readable length, most decided axes first, at most one example per outlet, newest first.
"""
import json
import re
from pathlib import Path

from pipeline import store
from pipeline.judge import prompt as P
from pipeline.sources.registry import load_outlets

N = 10            # featured examples
PER_COUNTRY = 2   # then at least this many per country, where the eval run has them


def main():
    reg = {o.id: o for o in load_outlets()}
    conn = store.connect()
    rows = conn.execute("""
        SELECT c.call_id, c.at, c.direction, c.first_outlet, c.second_outlet, c.first_article, c.second_article,
               j.axis, j.verdict, j.reason
        FROM judge_calls c JOIN judgments j USING(call_id)
        WHERE c.purpose = 'eval' AND c.prompt_version = 'v2' AND c.model = 'gpt-5.4-mini'
        ORDER BY c.at DESC""").fetchall()
    calls = {}
    for r in rows:
        c = calls.setdefault(r["call_id"], {k: r[k] for k in ("call_id", "at", "direction", "first_outlet", "second_outlet",
                                                              "first_article", "second_article")} | {"axes": {}})
        c["axes"][r["axis"]] = (r["verdict"], (r["reason"] or "").strip())
    ids = [a for c in calls.values() for a in (c["first_article"], c["second_article"])]
    art = {r["id"]: r for r in conn.execute(
        "SELECT id, outlet_id, country, title, url, published FROM articles WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)}

    def readable(c):
        return all(30 <= len(reason) <= 260 for _, reason in c["axes"].values())

    def decided(c):
        return sum(v in ("A", "B") for v, _ in c["axes"].values())

    pool = [c for c in calls.values() if decided(c) and readable(c)
            and c["first_article"] in art and c["second_article"] in art and art[c["first_article"]]["title"]]
    pool.sort(key=lambda c: c["at"], reverse=True)                 # newest first …
    pool.sort(key=lambda c: -decided(c))                         # … within: most decided axes first (stable)
    def example(c, featured):
        side = []
        for k in ("first", "second"):
            a = art[c[f"{k}_article"]]
            o = reg.get(a["outlet_id"])
            side.append({"outlet": o.name if o else a["outlet_id"], "outlet_id": a["outlet_id"], "country": a["country"],
                         "title": a["title"], "url": a["url"], "published": (a["published"] or "")[:10]})
        axes = {}
        for ax, (v, reason) in c["axes"].items():
            fw = P.first_wins(v, c["direction"])         # True: the first article is further right / more conservative
            axes[ax] = {"decided": v in ("A", "B"), "same": v == "same", "further": None if fw is None else (0 if fw else 1),
                        # the model wrote "A"/"B"; the page shows outlet names instead
                        "reason": re.sub(r"\b(?:Article |article )?([AB])\b", lambda m: side[m.group(1) == "B"]["outlet"], reason)}
        return {"id": c["call_id"][:8], "at": c["at"][:10], "featured": featured, "articles": side, "axes": axes}

    # featured: N examples, at most one per outlet; then up to PER_COUNTRY more for every country that has any
    used, taken, out = set(), set(), []
    for c in pool:
        if len(out) == N:
            break
        if c["first_outlet"] in used or c["second_outlet"] in used:
            continue
        used |= {c["first_outlet"], c["second_outlet"]}
        taken.add(c["call_id"])
        out.append(example(c, True))
    per = {}
    for e in out:
        for x in e["articles"]:
            per[x["country"]] = per.get(x["country"], 0) + 1
    for c in pool:
        if c["call_id"] in taken:
            continue
        cs = {art[c["first_article"]]["country"], art[c["second_article"]]["country"]}
        if all(per.get(k, 0) >= PER_COUNTRY for k in cs):
            continue
        taken.add(c["call_id"])
        for k in cs:
            per[k] = per.get(k, 0) + 1
        out.append(example(c, False))
    data = {"meta": {"source": "judge_calls/judgments, purpose=eval, prompt v2, gpt-5.4-mini",
                     "note": "REAL judge outputs with the model's reasons; headline and link only (A10)"},
            "examples": out}
    p = Path(__file__).with_name("judgments.json")
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(f"wrote {p}: {len(out)} examples from {len(pool)} eligible calls")
    print("per country:", dict(sorted(per.items())))
    for e in out[:N]:
        a, b = e["articles"]
        print(f"- {a['outlet']} ({a['country']}) vs {b['outlet']} ({b['country']}): "
              + " · ".join(f"{ax}: {'same' if d['same'] else ('—' if not d['decided'] else e['articles'][d['further']]['outlet'])}"
                           for ax, d in e["axes"].items()))


if __name__ == "__main__":
    main()
