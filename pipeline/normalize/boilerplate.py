"""2.1 boilerplate by frequency AND position (A40): repeated blocks at the start or end of an outlet's articles are the
site (headers, footers, pop-ups, tip boxes, subscription offers, copyright lines), not the story.

A paragraph is boilerplate for an outlet when all three hold (2026-10-11):
  1. it appears verbatim in >= MIN_REPEATS articles of that outlet;
  2. in most of those articles it sits at the EDGE: inside the run of repeated paragraphs that touches the start or the
     end of the text (footers can be 20 paragraphs long, so "first/last k" would miss them). A repeated paragraph with
     unique story text on both sides is body text — a quote or background in a follow-up — and is kept;
  3. the articles are about different stories (mean headline similarity < SAME_STORY_SIM), so an opening paragraph
     reused in follow-ups of one story is kept.
Exception to 2: a paragraph in >= ANYWHERE_REPEATS articles about different stories is boilerplate wherever it sits —
measured: every such repeat is an inline embed placeholder ("Wir benötigen Ihre Einwilligung… Instagram-Inhalt"),
affiliate note, app promo or ad-break marker ("Story continued below"); story text never repeats that often.
Pure functions here; pipeline/normalize/run.py applies them and stores what it found.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable

MIN_REPEATS = 3
ANYWHERE_REPEATS = 10
EDGE_SHARE = 0.5                # at the edge in more than half of its occurrences
SAME_STORY_SIM = 0.5
MIN_PARAGRAPH_CHARS = 15        # "Foto: NTB"-length lines are left to the text cleaner


def norm_paragraph(p: str) -> str:
    return re.sub(r"\s+", " ", p.strip().lower())


def paragraphs(text: str | None) -> list[str]:
    return [p for p in (text or "").split("\n") if len(p.strip()) >= MIN_PARAGRAPH_CHARS]


def edge_flags(paras: list[str], repeated: set[str]) -> list[bool]:
    """True for paragraphs in the leading or trailing run of repeated paragraphs."""
    keys = [norm_paragraph(p) for p in paras]
    edge = [False] * len(keys)
    for order in (range(len(keys)), range(len(keys) - 1, -1, -1)):
        for k in order:
            if keys[k] not in repeated:
                break
            edge[k] = True
    return edge


def find_boilerplate(docs: Iterable[tuple[str, str, str | None]],
                     mean_title_sim: Callable[[list[str]], float],
                     same_story_sim: float = SAME_STORY_SIM) -> dict[tuple[str, str], dict]:
    """docs: (article_id, outlet_id, text). mean_title_sim(article_ids) → mean pairwise headline similarity.
    Returns {(outlet_id, normalised paragraph): {"n": articles, "edge": share at the edge, "sim": headline sim}}."""
    docs = [(aid, outlet, paragraphs(text)) for aid, outlet, text in docs]
    holders = defaultdict(set)
    for aid, outlet, paras in docs:
        for p in paras:
            holders[(outlet, norm_paragraph(p))].add(aid)
    repeated = defaultdict(set)
    for (outlet, p), ids in holders.items():
        if len(ids) >= MIN_REPEATS:
            repeated[outlet].add(p)
    at_edge = defaultdict(int)
    for aid, outlet, paras in docs:
        for p, e in zip(paras, edge_flags(paras, repeated[outlet])):
            if e:
                at_edge[(outlet, norm_paragraph(p))] += 1
    found = {}
    for outlet, ps in repeated.items():
        for p in ps:
            ids = holders[(outlet, p)]
            share = at_edge[(outlet, p)] / len(ids)
            if share <= EDGE_SHARE and len(ids) < ANYWHERE_REPEATS:
                continue
            s = mean_title_sim(sorted(ids))
            if s < same_story_sim:
                found[(outlet, p)] = {"n": len(ids), "edge": round(share, 2), "sim": round(s, 3)}
    return found


def strip_boilerplate(text: str | None, known: set[str]) -> str | None:
    """Remove paragraphs whose normalised form is known boilerplate for this outlet."""
    if not text:
        return text
    kept = [p for p in text.split("\n") if norm_paragraph(p) not in known]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return out or None
