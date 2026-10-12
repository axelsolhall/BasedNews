"""2.3 same-item pairs (A44): two outlets publishing the same headline AND the same text are equal on that story —
"the same thing, nothing more or less" (2026-10-11). Such a pair is a known tie: kept, never sent to the judge.

v2 (same day): text overlap alone was not enough — 15 of 29 near-identical-text pairs had different headlines, several
with real framing ("Trump tordner mod Zelenskyj" vs "Trump: Ukraine har brug for en ny præsident"). Those pairs are
judged; with the same body they isolate headline framing. A tie needs, after removing what is not editorial (agency
tags, "continue reading" links, a leading copy of the headline, soft hyphens, case and punctuation):
  identical headline  AND  at most MAX_WORD_DIFF of the words differ.
"""
from __future__ import annotations

import difflib
import re

MAX_WORD_DIFF = 0.02         # ≤ 2 % of words added, removed or changed
PREFILTER_JACCARD = 0.6      # cheap shingle check before the word-level diff
SHINGLE = 5
MIN_WORDS = 15               # too short to tell
NOT_EDITORIAL = {"ntb", "tt", "stt", "ritzau", "dpa", "afp", "reuters", "ap", "ansa", "efe", "europa", "press", "fortsæt",
                 "med", "at", "læse", "les", "mer", "läs", "mer"}
_TRAIL = re.compile(r"\b(ntb|tt|stt|ritzau|dpa|afp|reuters|ansa|efe|europa press|fortsæt med at læse)\b", re.I)


def words(text: str | None) -> list[str]:
    return re.findall(r"\w+", (text or "").replace("­", "").lower())


def norm_headline(t: str | None) -> str:
    return " ".join(words(t))


def body_words(text: str | None, headline: str | None) -> list[str]:
    w = words(_TRAIL.sub(" ", text or ""))
    h = words(headline)
    if h and w[:len(h)] == h:                    # one extractor kept the headline as the first line of the text
        w = w[len(h):]
    return w


def shingles(w: list[str]) -> set[str]:
    return {" ".join(w[i:i + SHINGLE]) for i in range(len(w) - SHINGLE + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def same_item(title_a: str | None, text_a: str | None, title_b: str | None, text_b: str | None) -> float | None:
    """Share of identical words if the two items count as the same thing, else None."""
    if norm_headline(title_a) != norm_headline(title_b) or not norm_headline(title_a):
        return None
    wa, wb = body_words(text_a, title_a), body_words(text_b, title_b)
    if min(len(wa), len(wb)) < MIN_WORDS or jaccard(shingles(wa), shingles(wb)) < PREFILTER_JACCARD:
        return None
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    diff = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")
    share = diff / max(len(wa), len(wb))
    return round(1 - share, 4) if share <= MAX_WORD_DIFF else None
