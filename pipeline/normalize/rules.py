"""M3 decision rule (A34): several headlines share one stored text. One article renamed, or a container page?

A pure function of similarity matrices, so it is unit-testable without the embedding model.

  1. Link two items as the same article when their summaries match, or when their headlines are near-identical
     in meaning. (A renamed article keeps its summary; live-blog entries and news-list items each have their own.)
  2. If the links connect the whole group: one article with n headline versions, for any n. It keeps the text.
  3. Otherwise it is a container (live blog, news list): only a component whose headline clearly matches the
     text keeps it; the rest lose it. A tie gives the text to nobody (false split beats false merge).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class NormalizeConfig:
    same_summary: float = 0.6   # measured 2026-10-11: live-blog entries <= 0.16 pairwise, renamed copies 1.0
    same_title: float = 0.75    # headlines this close in meaning are one item even without summaries
    owns_text: float = 0.5      # headline-to-text similarity a container entry needs to keep the text (top stories 0.57-0.80)
    margin: float = 0.15        # ... and its lead over the runner-up (four different car crashes scored 0.47-0.57)


@dataclass(frozen=True)
class Member:
    id: str
    published: str | None
    has_summary: bool


@dataclass
class Decision:
    kind: str                                            # "renamed" | "container"
    canonical: dict[str, str] = field(default_factory=dict)   # member id -> canonical id (itself if canonical)
    keeps_text: set[str] = field(default_factory=set)         # canonical ids that keep the shared text
    best_text_sim: float = 0.0
    runner_up_text_sim: float = 0.0


def decide(members: list[Member], summary_sim: np.ndarray, title_sim: np.ndarray, text_sim: np.ndarray,
           cfg: NormalizeConfig = NormalizeConfig()) -> Decision:
    n = len(members)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            same_summary = members[i].has_summary and members[j].has_summary and summary_sim[i, j] >= cfg.same_summary
            if same_summary or title_sim[i, j] >= cfg.same_title:
                parent[find(i)] = find(j)

    comps: dict[int, list[int]] = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    first = lambda idx: min(idx, key=lambda k: (members[k].published or "9999", members[k].id))   # earliest copy
    canon = {members[k].id: members[first(idx)].id for idx in comps.values() for k in idx}

    if len(comps) == 1:
        root = next(iter(canon.values()))
        return Decision("renamed", canon, {root}, float(np.max(text_sim)), 0.0)

    scores = sorted(((float(np.max(text_sim[idx])), members[first(idx)].id) for idx in comps.values()), reverse=True)
    (best, owner), runner = scores[0], scores[1][0]
    keeps = {owner} if best >= cfg.owns_text and best - runner >= cfg.margin else set()
    return Decision("container", canon, keeps, best, runner)
