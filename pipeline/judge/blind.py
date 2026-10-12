"""Blind the judge to the outlet (A42): mask an outlet's references to itself in its own article before 3.2 reads it.

An LLM that recognises "© Copyright ANSA", "skriver Aftonbladet" or a " | Fox News" title suffix can answer from its
stereotype of the brand instead of from the text. Only the outlet's OWN names are replaced, with a neutral "[outlet]";
meaning is kept (A41) and mentions of other outlets are untouched. Patterns: data/outlet_aliases.json.

    uv run python -m pipeline.judge.blind     # eval: how many self-references per outlet, and what is left over
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

ALIASES = Path("data/outlet_aliases.json")
MASK = "[outlet]"
_GENITIVE = r"(?:['’]s|:s|:n|:lle|:ssä|:stä|s)?"


@lru_cache(maxsize=1)
def _patterns() -> dict[str, re.Pattern]:
    from pipeline.sources.registry import load_outlets
    aliases = json.loads(ALIASES.read_text())["aliases"]
    out = {}
    for o in load_outlets():
        names = list(aliases.get(o.id, [o.name]))
        domain = urlparse(o.homepage).netloc.removeprefix("www.")
        names += [f"www.{domain}", domain]
        names = sorted(set(names), key=len, reverse=True)            # longest first: "Fox News Digital" before "Fox News"
        alt = "|".join(re.escape(n) for n in names)
        out[o.id] = re.compile(rf"(?<![\w.])(?:{alt}){_GENITIVE}(?![\w])")
    return out


def mask_outlet(text: str | None, outlet_id: str) -> tuple[str | None, int]:
    """(masked text, number of replacements). Unknown outlets are returned unchanged."""
    pat = _patterns().get(outlet_id)
    if not text or pat is None:
        return text, 0
    return pat.subn(MASK, text)


def main():
    from collections import Counter
    from pipeline import store
    from pipeline.sources.registry import load_outlets
    conn = store.connect()
    names = {o.id: o.name for o in load_outlets()}
    hits, docs, leftover = Counter(), Counter(), Counter()
    examples = {}
    for r in conn.execute("""SELECT outlet_id, title, summary, full_text FROM articles
                             WHERE canonical_id IS NULL AND kind IS NULL"""):
        for field in ("title", "summary", "full_text"):
            masked, n = mask_outlet(r[field], r["outlet_id"])
            hits[r["outlet_id"]] += n
            docs[r["outlet_id"]] += n > 0
            first_word = names[r["outlet_id"]].split()[-1].lower()
            if masked and len(first_word) > 3 and first_word in masked.lower():
                leftover[r["outlet_id"]] += 1
                examples.setdefault(r["outlet_id"], masked[max(0, masked.lower().find(first_word) - 50):][:110])
    print(f"self-references masked: {sum(hits.values()):,} in {sum(docs.values()):,} fields")
    print("most per outlet:", dict(hits.most_common(10)))
    print("\nfields where the outlet's name still appears in some form (check these are not self-references):")
    for o, n in leftover.most_common(10):
        print(f"  {o:<14} {n:>4}  …{examples[o]!r}")


if __name__ == "__main__":
    main()
