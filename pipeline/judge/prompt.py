"""3.2 judge prompt and answer format (A46). Versioned: every judgment stores PROMPT_VERSION, and judgments from
different prompt versions are never mixed in one fit (a prompt change can shift the measurement scale).

v1 asked "which article is further toward RIGHT / CONSERVATIVE?". Its eval (2026-10-11, gpt-5.4-mini, 130 calls) found
the judge picks the article with MORE MATERIAL whichever direction is asked (longer text chosen 71 % / 86 %; the
"further LEFT/LIBERAL?" mirror agreed only 20 % on the social axis). v2 asks for a score per article on a symmetric
−2…+2 scale and says outright that length and detail are not evidence; the verdict is derived from the two scores.

Direction: "positive" asks which article sits further toward economic RIGHT / CONSERVATIVE (the + ends of the plane);
"negative" asks the mirror question (LEFT / LIBERAL) — used by the framing-symmetry test (A28).
"""
from __future__ import annotations

import json

PROMPT_VERSION = "v4"
VERSIONS = ("v1", "v2", "v3", "v4")
AXES = ("econ", "social")
POLES = {"positive": {"econ": "economic RIGHT", "social": "CONSERVATIVE"},
         "negative": {"econ": "economic LEFT", "social": "LIBERAL"}}

SYSTEM = """You compare how two news articles about the same event are framed.
Judge only the text in front of you — wording, emphasis, which sources are quoted, what is included or left out.
Never use what you know or assume about any publication; outlet names are hidden as [outlet].

Axes:
- econ: economic RIGHT (markets, business, lower taxes, deregulation, fiscal restraint) versus economic LEFT
  (redistribution, regulation, labour, public spending, welfare).
- social: CONSERVATIVE (tradition, order, security, national identity, restrictive immigration, religion) versus
  LIBERAL (individual rights, diversity, minority rights, open borders, progressive social values).

For each axis answer:
- "A" or "B": that article's framing sits further toward {econ_pole} (econ) / {social_pole} (social);
- "same": both are framed about equally on this axis;
- "na": the story has no angle on this axis at all (for example a football result on the economic axis).
Give one short reason per axis that points to the text."""

SYSTEM_V2 = """You compare how two news articles about the same event are framed.
Judge only the text in front of you — wording, emphasis, which sources are quoted, what is included or left out.
Never use what you know or assume about any publication; outlet names are hidden as [outlet].
The amount of text is not evidence: a longer or more detailed article is not more left, right, liberal or
conservative than a short one. Judge the slant of what is said, not how much is said.

Axes:
- econ: economic LEFT (redistribution, regulation, labour, public spending, welfare) versus economic RIGHT
  (markets, business, lower taxes, deregulation, fiscal restraint).
- social: LIBERAL (individual rights, diversity, minority rights, open borders, progressive social values) versus
  CONSERVATIVE (tradition, order, security, national identity, restrictive immigration, religion).

For each axis, score BOTH articles on the same scale, comparing them with each other:
{scale}
Use "na" for both articles when the story has no angle on that axis at all (for example a football result on the
economic axis). Give one short reason per axis that points to the text."""

SCALES = {"positive": "-2 = clearly {lo}, -1 = leans {lo}, 0 = neutral or balanced, 1 = leans {hi}, 2 = clearly {hi}",
          "negative": "-2 = clearly {hi}, -1 = leans {hi}, 0 = neutral or balanced, 1 = leans {lo}, 2 = clearly {lo}"}
SCORE = {"type": "string", "enum": ["-2", "-1", "0", "1", "2", "na"]}
SCHEMA_V2 = {
    "type": "object", "additionalProperties": False, "required": list(AXES),
    "properties": {ax: {"type": "object", "additionalProperties": False, "required": ["a", "b", "reason"],
                        "properties": {"a": SCORE, "b": SCORE, "reason": {"type": "string"}}} for ax in AXES},
}

# v3 (2026-10-11): one forced choice per axis — "Which article is more X: A or B?" — "na" only when the story has
# no angle at all, no reasons. Asked in a random direction per call (more LEFT/LIBERAL or more RIGHT/CONSERVATIVE) so any
# lean caused by the wording itself cancels out (A28) and stays measurable.
SYSTEM_V3 = """You compare the framing of two news articles about the same event.
Use only the text: word choice, which voices are quoted and how they are described, what is presented as the problem
and as the solution, and what is included or left out. Ignore length and amount of detail. Outlet names are hidden as
[outlet]; never guess the publication.

Economic axis
- LEFT framing centres workers, unions, tenants, consumers and public services; treats inequality, corporate power or
  market failure as the problem and regulation, taxation, redistribution or public spending as the answer.
- RIGHT framing centres business, investors, taxpayers and markets; treats taxes, regulation, public spending or state
  intervention as the problem and growth, competition, deregulation or fiscal restraint as the answer.

Social axis
- LIBERAL framing centres individual rights, minorities, migrants and civil liberties; treats diversity, immigration
  and social change as normal or positive; scrutinises police, military and state power.
- CONSERVATIVE framing centres order, security, tradition, national identity and religion; treats crime, immigration
  and rapid social change as threats; trusts police, military and authorities.

Answer each question with "A" or "B". If one article leans even slightly more, pick it.
Use "na" only when the event has no angle at all on that axis (for example a football result on the economic axis)."""

# v4 (2026-10-11, after E4): removes sourcing as a leaning ("trusts / scrutinises police, military and
# authorities"; E4 put official-source-heavy straight news — NYT, Sky, BBC — on the conservative side), says crime is a
# threat to society only as a frame, rules out tone/style, language and article order, and widens what counts as an
# economic angle (E4: 90 % "na" on econ). Questions and answers as v3.
SYSTEM_V4 = """You compare the framing of two news articles about the same event.

Judge only what the text emphasises and endorses: word choice, whose view gets space and how it is described, what is
presented as the problem and as the solution, and what is included or left out.
These are not a leaning: quoting officials, police or experts (ordinary reporting); length and amount of detail;
tone, style or sensationalism; the language an article is written in; the order the articles are shown in.
Outlet names are hidden as [outlet]; never guess the publication.

Economic axis
- LEFT framing centres workers, unions, tenants, consumers and public services; treats inequality, corporate power or
  market failure as the problem and regulation, taxation, redistribution or public spending as the answer.
- RIGHT framing centres business, investors, taxpayers and markets; treats taxes, regulation, public spending or state
  intervention as the problem and growth, competition, deregulation or fiscal restraint as the answer.
Any story touching money, prices, jobs, companies, trade, taxes, sanctions or public budgets has an economic angle.

Social axis
- LIBERAL framing centres individual rights, minorities, migrants and civil liberties; presents diversity,
  immigration and social change as normal or positive.
- CONSERVATIVE framing centres order, tradition, national identity, family and religion; presents immigration, crime
  and rapid social change as threats to society.

Answer each question with "A" or "B". If one article leans even slightly more, pick it.
Use "na" only when the event has no angle at all on that axis (for example a football result on the economic axis)."""

QUESTIONS_V3 = {"negative": {"econ": "Which article's framing is more economically LEFT: A or B?",
                             "social": "Which article's framing is more LIBERAL: A or B?"},
                "positive": {"econ": "Which article's framing is more economically RIGHT: A or B?",
                             "social": "Which article's framing is more CONSERVATIVE: A or B?"}}
SCHEMA_V3 = {"type": "object", "additionalProperties": False, "required": list(AXES),
             "properties": {ax: {"type": "string", "enum": ["A", "B", "na"]} for ax in AXES}}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(AXES),
    "properties": {ax: {"type": "object", "additionalProperties": False, "required": ["verdict", "reason"],
                        "properties": {"verdict": {"type": "string", "enum": ["A", "B", "same", "na"]},
                                       "reason": {"type": "string"}}} for ax in AXES},
}


def schema(version: str = PROMPT_VERSION) -> dict:
    return {"v4": SCHEMA_V3, "v3": SCHEMA_V3, "v2": SCHEMA_V2}.get(version, SCHEMA)


def messages(text_a: str, text_b: str, direction: str = "positive", version: str = PROMPT_VERSION) -> list[dict]:
    if version in ("v3", "v4"):
        q = QUESTIONS_V3[direction]
        return [{"role": "system", "content": SYSTEM_V4 if version == "v4" else SYSTEM_V3},
                {"role": "user", "content": f"Article A\n\n{text_a}\n\n---\n\nArticle B\n\n{text_b}\n\n---\n\n"
                                            f"econ: {q['econ']}\nsocial: {q['social']}"}]
    if version == "v2":
        scale = "\n".join(f"- {ax}: " + SCALES[direction].format(lo=lo, hi=hi) for ax, lo, hi in
                          (("econ", "economic LEFT", "economic RIGHT"), ("social", "LIBERAL", "CONSERVATIVE")))
        system = SYSTEM_V2.format(scale=scale)
    else:
        poles = POLES[direction]
        system = SYSTEM.format(econ_pole=poles["econ"], social_pole=poles["social"])
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"Article A\n\n{text_a}\n\n---\n\nArticle B\n\n{text_b}"}]


def parse(raw: str | dict) -> dict[str, dict]:
    """{axis: {"verdict": A|B|same|na, "reason": str[, "scores": (a, b)]}}; malformed → abstention ("na").
    v2 answers carry scores: A if a > b (A further toward the scale's + end as presented), B if a < b, same if equal."""
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        d = {}
    out = {}
    for ax in AXES:
        if isinstance(d, dict) and isinstance(d.get(ax), str):        # v3: the letter = further toward the asked pole
            out[ax] = {"verdict": d[ax] if d[ax] in ("A", "B") else "na", "reason": ""}
            continue
        v = (d.get(ax) or {}) if isinstance(d, dict) else {}
        reason = str(v.get("reason", ""))[:500]
        if "a" in v or "b" in v:                                    # v2
            a, b = v.get("a"), v.get("b")
            if a in ("-2", "-1", "0", "1", "2") and b in ("-2", "-1", "0", "1", "2"):
                ia, ib = int(a), int(b)
                verdict = "A" if ia > ib else "B" if ia < ib else "same"
                out[ax] = {"verdict": verdict, "reason": reason, "scores": (ia, ib)}
            else:
                out[ax] = {"verdict": "na", "reason": reason}
            continue
        verdict = v.get("verdict") if v.get("verdict") in ("A", "B", "same", "na") else "na"
        out[ax] = {"verdict": verdict, "reason": reason}
    return out


def first_wins(verdict: str, direction: str = "positive") -> bool | None:
    """True = the first-shown article is further toward the + end (right / conservative). None = same or na."""
    if verdict not in ("A", "B"):
        return None
    wins = verdict == "A"
    return wins if direction == "positive" else not wins
