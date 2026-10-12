"""Why does an outlet land where it does? Re-ask its decisive verdicts with the reasons version of the active prompt.

    uv run python -m pipeline.judge.diagnose --outlet nyt --axis social [--max 25]

Same pair, same order, same question direction as the original call; the only addition is "give one short reason per
axis". Diagnostic only: stored as api usage, never as a measurement. Prints headlines, verdicts and reasons (model text,
no article text) and how often the reasoned answer matches the original.
"""
from __future__ import annotations

import argparse
import json

from pipeline import store
from pipeline.judge import prompt as P
from pipeline.judge.client import OpenAIJudge
from pipeline.measure.run import rendered
from pipeline.settings import load_settings
from pipeline.sources.registry import load_outlets

SCHEMA = {"type": "object", "additionalProperties": False, "required": list(P.AXES),
          "properties": {ax: {"type": "object", "additionalProperties": False, "required": ["answer", "reason"],
                              "properties": {"answer": {"type": "string", "enum": ["A", "B", "na"]},
                                             "reason": {"type": "string"}}} for ax in P.AXES}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outlet", required=True)
    ap.add_argument("--axis", default="social")
    ap.add_argument("--max", type=int, default=25)
    args = ap.parse_args()
    reg = {o.id: o for o in load_outlets()}
    s = load_settings(); conn = store.connect(); judge = OpenAIJudge(s)
    calls = conn.execute("""SELECT c.*, j.verdict v FROM judge_calls c JOIN judgments j USING(call_id)
                            WHERE c.purpose = 'measure' AND c.prompt_version = ? AND j.axis = ? AND j.verdict IN ('A','B')
                            AND ? IN (c.first_outlet, c.second_outlet) ORDER BY c.at LIMIT ?""",
                         (P.PROMPT_VERSION, args.axis, args.outlet, args.max)).fetchall()
    ids = [i for c in calls for i in (c["first_article"], c["second_article"])]
    art = {r["id"]: r for r in conn.execute("SELECT id, outlet_id, title, summary, full_text, published FROM articles "
                                           "WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)}
    hist = store.headline_history(conn, ids)
    cost, same, toward = 0.0, 0, {"more conservative": 0, "more liberal": 0}
    for c in calls:
        a, b = art[c["first_article"]], art[c["second_article"]]
        msgs = P.messages(rendered(a, hist, c["text_kind"]), rendered(b, hist, c["text_kind"]), c["direction"], P.PROMPT_VERSION)
        msgs[1]["content"] += "\n\nGive one short reason per axis that points to the text."
        kw = dict(model=judge.model, messages=msgs, response_format={"type": "json_schema",
                  "json_schema": {"name": "diagnostic", "strict": True, "schema": SCHEMA}})
        if judge.reasoning:
            kw["reasoning_effort"] = judge.reasoning
        r = judge.client.chat.completions.create(**kw)
        d = json.loads(r.choices[0].message.content)[args.axis]
        pin, pout = s.price(judge.model)
        usd = (r.usage.prompt_tokens * pin + r.usage.completion_tokens * pout) / 1e6
        cost += usd
        store.log_usage(conn, "openai", judge.model, "judge-diagnostic", r.usage.prompt_tokens, r.usage.completion_tokens, usd)
        same += d["answer"] == c["v"]
        target_is_a = c["first_outlet"] == args.outlet
        asked = P.QUESTIONS_V3[c["direction"]][args.axis].split("more ")[1].split(":")[0]       # e.g. "LIBERAL"
        picked_target = (d["answer"] == "A") == target_is_a if d["answer"] in ("A", "B") else None
        if picked_target is not None:
            cons = (asked == "CONSERVATIVE") == picked_target
            toward["more conservative" if cons else "more liberal"] += 1
        other = b if target_is_a else a
        print(f"\n[{reg[args.outlet].name} = {'A' if target_is_a else 'B'} · vs {reg[other['outlet_id']].name} · asked: more {asked}]"
              f"\n  {reg[args.outlet].name}: {(a if target_is_a else b)['title'][:95]}"
              f"\n  {reg[other['outlet_id']].name}: {other['title'][:95]}"
              f"\n  → {d['answer']} (original {c['v']}) — {d['reason']}")
    conn.commit()
    print(f"\n{len(calls)} calls · ${cost:.3f} · reasoned answer = original in {same}/{len(calls)} · "
          f"{reg[args.outlet].name} judged {toward}")


if __name__ == "__main__":
    main()
